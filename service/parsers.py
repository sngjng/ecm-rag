"""파일 유형에 따른 정규화. 원문과 추출 좌표를 별도로 남긴다."""
from __future__ import annotations
import ast
import hashlib
import json
import re
from datetime import datetime
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path


CODE_RE = re.compile(r'\b(?:ORA-\d{5}|SQLSTATE[=: ]+[0-9A-Z]{5}|[A-Z]{3,8}\d{3,6}[A-Z]?|SQLCODE[=: ]+-?\d+)\b')
EXCEPTION_RE = re.compile(r'\b(?:[\w.]+(?:Exception|Error))\b')
FRAME_RE = re.compile(r'^\s*at\s+([\w.$]+)\.([\w$<>]+)\(([^():]+)(?::(\d+))?\)', re.M)
JAVA_EXCEPTION_RE = re.compile(
    r'\b(?:[A-Za-z_$][\w.$]*)?(?:Exception|Error|Throwable)\b'
)


@lru_cache(maxsize=1)
def _get_java_parser():
    """Java grammar/parser를 Worker 프로세스당 한 번만 생성한다.

    JAR 디컴파일 결과는 Java 파일이 수천 개일 수 있으므로 파일마다 grammar를 다시
    초기화하면 화면 polling 제한보다 오래 걸릴 수 있다. Worker는 작업을 순차 처리하므로
    동일 parser 인스턴스를 안전하게 재사용한다.
    """
    from tree_sitter_language_pack import get_parser

    return get_parser('java')


def _mask_java_non_code(source: str) -> str:
    """문자열과 주석을 공백으로 바꾸되 줄/문자 위치는 그대로 유지한다.

    JAR 디컴파일 결과는 신뢰할 수 없는 입력이며, 일부 tree-sitter wheel 조합은
    문법 분석 중 Python 예외가 아닌 segmentation fault를 발생시킬 수 있다.
    아래 경량 분석기는 중괄호 위치와 선언부만 필요하므로 문자열/주석 내부의
    가짜 ``class``, ``(``, ``{``가 인식되지 않도록 먼저 마스킹한다.
    """
    result = list(source)
    index = 0
    state = 'code'
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ''
        if state == 'code':
            if char == '/' and next_char == '/':
                result[index] = result[index + 1] = ' '
                state = 'line_comment'
                index += 2
                continue
            if char == '/' and next_char == '*':
                result[index] = result[index + 1] = ' '
                state = 'block_comment'
                index += 2
                continue
            if char == '"':
                result[index] = ' '
                state = 'string'
            elif char == "'":
                result[index] = ' '
                state = 'character'
        elif state == 'line_comment':
            if char == '\n':
                state = 'code'
            else:
                result[index] = ' '
        elif state == 'block_comment':
            if char == '*' and next_char == '/':
                result[index] = result[index + 1] = ' '
                state = 'code'
                index += 2
                continue
            if char != '\n':
                result[index] = ' '
        elif state in ('string', 'character'):
            delimiter = '"' if state == 'string' else "'"
            if char == '\\':
                result[index] = ' '
                if index + 1 < len(source):
                    if source[index + 1] != '\n':
                        result[index + 1] = ' '
                    index += 2
                    continue
            elif char == delimiter:
                result[index] = ' '
                state = 'code'
            elif char != '\n':
                result[index] = ' '
        index += 1
    return ''.join(result)


def _java_line_number(source: str, position: int) -> int:
    return source.count('\n', 0, position) + 1


def _java_block_end(masked: str, opening_brace: int) -> int:
    """선언의 여는 중괄호와 짝이 맞는 닫는 중괄호 위치를 반환한다."""
    depth = 0
    for position in range(opening_brace, len(masked)):
        if masked[position] == '{':
            depth += 1
        elif masked[position] == '}':
            depth -= 1
            if depth == 0:
                return position
    return len(masked) - 1


def _parse_java_definitions_without_native(
    source: str,
) -> list[tuple[str, str, str | None, int, int, str | None]]:
    """네이티브 확장 없이 클래스와 메서드 선언 위치를 추출한다.

    완전한 Java 컴파일러가 아니라 검색 인덱스 생성용 안전 분석기다. CFR가 만든
    소스에서 stack trace 검색에 필요한 package/class/method 키와 대략적인 본문
    범위를 얻는 것이 목적이다. 실패해도 프로세스가 종료되지 않고 파일 청킹으로
    계속 진행할 수 있다는 점이 JAR 일괄 처리에서는 더 중요하다.
    """
    masked = _mask_java_non_code(source)
    lines = source.splitlines()
    definitions: list[tuple[str, str, str | None, int, int, str | None]] = []
    class_ranges: list[tuple[int, int, str]] = []
    class_pattern = re.compile(
        r'\b(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)[^;{}]*\{', re.M
    )
    for match in class_pattern.finditer(masked):
        opening = match.end() - 1
        closing = _java_block_end(masked, opening)
        first = _java_line_number(masked, match.start())
        last = _java_line_number(masked, closing)
        kind = 'class' if match.group(1) == 'record' else match.group(1)
        signature = lines[first - 1].strip()[:1000] if lines else None
        definitions.append((kind, match.group(2), None, first, last, signature))
        class_ranges.append((opening, closing, match.group(2)))

    # 한 선언이 여러 줄에 걸칠 수 있어 괄호 내부는 제한적으로 DOTALL을 허용한다.
    # 제어문/호출문은 선언으로 오인하지 않도록 줄 시작, 선택적 modifier, 반환형을 요구한다.
    method_pattern = re.compile(
        r'(?ms)^[ \t]*(?:@[\w$.]+(?:\([^\n]*\))?[ \t]*)*'
        r'(?:(?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default)\s+)*'
        r'(?:<[^{;()]{1,300}>\s+)?'
        r'(?:(?P<return>[A-Za-z_$][\w$.[\]<>?, ]*)\s+)?'
        r'(?P<name>[A-Za-z_$][\w$]*)\s*\([^;{}]{0,4000}\)'
        r'(?:\s+throws\s+[^{}]{1,1000})?\s*\{'
    )
    excluded = {'if', 'for', 'while', 'switch', 'catch', 'try', 'do', 'else', 'new', 'return'}
    for match in method_pattern.finditer(masked):
        name = match.group('name')
        if name in excluded:
            continue
        opening = match.end() - 1
        owners = [item for item in class_ranges if item[0] < match.start() < item[1]]
        if not owners:
            continue
        # 가장 안쪽 범위가 중첩 클래스의 실제 소유자다.
        _, _, owner = min(owners, key=lambda item: item[1] - item[0])
        closing = _java_block_end(masked, opening)
        first = _java_line_number(masked, match.start())
        last = _java_line_number(masked, closing)
        signature = ' '.join(source[match.start():opening].split())[:1000]
        kind = 'constructor' if name == owner and not match.group('return') else 'method'
        definitions.append((kind, name, owner, first, last, signature))
    return sorted(definitions, key=lambda item: (item[3], item[4], item[1]))


def extract_java_diagnostics(
    text: str,
    *,
    signature: str | None = None,
    symbol_name: str | None = None,
    symbol_type: str | None = None,
) -> dict:
    """Java 코드에서 예외 유형과 업무 오류 코드를 검색용 메타데이터로 분류한다.

    AST 종류와 무관한 공통 단계이므로 직접 업로드한 Java와 CFR로 복원한 JAR에
    동일하게 적용된다. 완전한 컴파일 의미 분석이 아니라 운영 검색용 분류이며,
    선언(throws), 발생(throw new), 처리(catch), 참조 유형을 각각 보존한다.
    """
    searchable = f"{signature or ''}\n{text}"
    referenced = sorted(set(JAVA_EXCEPTION_RE.findall(searchable)))

    declared: set[str] = set()
    for match in re.finditer(r'\bthrows\s+([^\{;\n]+)', searchable):
        declared.update(JAVA_EXCEPTION_RE.findall(match.group(1)))

    thrown = sorted(set(re.findall(
        r'\bthrow\s+new\s+([A-Za-z_$][\w.$]*(?:Exception|Error|Throwable))\b',
        text,
    )))
    caught: set[str] = set()
    for match in re.finditer(r'\bcatch\s*\(([^)]*)\)', text, re.S):
        caught.update(JAVA_EXCEPTION_RE.findall(match.group(1)))

    error_codes = sorted(set(CODE_RE.findall(text)))
    is_exception_class = bool(
        symbol_type == 'class'
        and symbol_name
        and (
            symbol_name.endswith(('Exception', 'Error', 'Throwable'))
            or re.search(
                rf'\bclass\s+{re.escape(symbol_name)}\b[^{{;]*\bextends\s+'
                rf'[\w.$]*(?:Exception|Error|Throwable)\b',
                text,
            )
        )
    )
    return {
        'exception_types': referenced,
        'declared_exceptions': sorted(declared),
        'thrown_exceptions': thrown,
        'caught_exceptions': sorted(caught),
        'error_codes': error_codes,
        'is_exception_class': is_exception_class,
    }


def _java_diagnostic_context(metadata: dict) -> str:
    """구조화된 Java 진단 메타를 embedding 문맥 문자열로 변환한다."""
    values = [
        ('Exception types', metadata.get('exception_types')),
        ('Declared throws', metadata.get('declared_exceptions')),
        ('Thrown exceptions', metadata.get('thrown_exceptions')),
        ('Caught exceptions', metadata.get('caught_exceptions')),
        ('Error codes', metadata.get('error_codes')),
    ]
    return ''.join(
        f"{label}: {', '.join(items)}\n" for label, items in values if items
    )

@dataclass
class Item:
    kind: str
    text: str
    # 사용자에게 보여줄 원문(text)과 검색에 넣을 metadata-enriched 문장을 분리한다.
    embedding_text: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    identifier: str | None = None
    parent_id: str | None = None
    metadata: dict = field(default_factory=dict)
    # error 이벤트/심볼은 트랜잭션 안에서 청크와 함께 저장한다.
    error: dict | None = None
    symbol: dict | None = None


def parse_pdf(path: Path, out: Path) -> tuple[list[Item], dict]:
    """Docling으로 ECM PDF를 파싱하고 표 셀·페이지 좌표와 canonical JSON을 보존한다."""
    # PDF 이외 파일은 Docling의 무거운 torch/model dependency 없이 처리할 수 있다.
    from chunking.semantic_chunker import chunk_text_document
    from chunking.table_chunker import chunk_table
    from ingestion.docling_parser import DoclingParser
    from normalization.document_normalizer import normalize_document
    from table.table_extractor import extract_tables
    from table.table_validator import validate_table
    raw, markdown = DoclingParser().parse_bundle(path)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'docling.json').write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    (out / 'document.md').write_text(markdown, encoding='utf-8')
    doc = normalize_document(raw, path.name)
    doc.tables = extract_tables(raw, doc.document_id)
    for table in doc.tables:
        table.validation_score, table.warnings = validate_table(table)
    (out / 'canonical.json').write_text(doc.model_dump_json(indent=2), encoding='utf-8')
    chunks = chunk_text_document(doc)
    for table in doc.tables:
        chunks.extend(chunk_table(doc.document_id, table))
    items = [Item(kind=c.kind, text=c.retrieval_text, page_start=min(c.source_pages) if c.source_pages else None,
                  page_end=max(c.source_pages) if c.source_pages else None, parent_id=c.parent_id,
                  metadata={'heading_path': c.heading_path, 'table_id': c.table_id,
                            'original_chunk_id': c.chunk_id, **c.metadata}) for c in chunks if c.retrieval_text.strip()]
    # 파서/embedding 모델을 바꿀 때 PDF를 다시 읽지 않도록 검색 단위도 artifact로 보존한다.
    with (out / 'chunks.jsonl').open('w', encoding='utf-8') as handle:
        for item in items:
            handle.write(json.dumps(asdict(item), ensure_ascii=False, default=str) + '\n')
    report = {'pages': doc.page_count, 'tables': len(doc.tables), 'blocks': len(doc.blocks),
              'low_quality_tables': [t.table_id for t in doc.tables if t.validation_score < .8]}
    (out / 'metadata.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return items, report


def parse_docx(path: Path) -> tuple[list[Item], dict]:
    """DOCX 문단과 표를 문서 순서대로 읽는다. 표 헤더를 행마다 재주입한다."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    document = Document(path)
    items: list[Item] = []
    heading = ''
    for element in document.element.body.iterchildren():
        if element.tag == qn('w:p'):
            paragraph = Paragraph(element, document)
            value = paragraph.text.strip()
            if not value: continue
            if paragraph.style and paragraph.style.name.startswith('Heading'):
                heading = value
            items.append(Item(kind='text', text=(heading + '\n' if heading != value and heading else '') + value,
                              metadata={'heading': heading}))
        elif element.tag == qn('w:tbl'):
            table_id = f'table:{len(items)}'
            table = Table(element, document)
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            if not rows: continue
            headers = rows[0]
            for row_number, row in enumerate(rows[1:], 2):
                fact = '; '.join(f'{headers[i] if i < len(headers) else f"열{i+1}"}: {value}'
                                 for i, value in enumerate(row))
                items.append(Item(kind='table', text=f'{heading}\n{fact}',
                                  parent_id=table_id,
                                  metadata={'row': row_number, 'heading': heading}))
    return items, {'paragraphs_and_table_rows': len(items)}


def parse_text(path: Path) -> tuple[list[Item], dict]:
    text = path.read_text(encoding='utf-8-sig')
    # 문단 경계를 우선한다. 너무 긴 문단은 문자수로 분할하고 다음 조각에 소량 문맥을 반복한다.
    paragraphs = re.split(r'\n\s*\n', text)
    parts = []
    for paragraph in paragraphs:
        while len(paragraph) > 3000:
            cut = paragraph.rfind('\n', 0, 3000)
            cut = cut if cut > 1000 else 3000
            parts.append(paragraph[:cut]); paragraph = paragraph[cut:]
        if paragraph.strip(): parts.append(paragraph)
    items = []
    for part in parts:
        if items and len(items[-1].text) + len(part) < 3000:
            items[-1].text += '\n\n' + part
        else:
            items.append(Item(kind='text', text=part.strip()))
    return items, {'characters': len(text)}


INCIDENT_FIELDS = {
    '장애번호': 'incident_id', 'incident_id': 'incident_id',
    '시스템': 'system', 'system': 'system',
    '제품': 'product', 'product': 'product',
    '버전': 'version', 'version': 'version',
    '증상': 'symptom', 'symptom': 'symptom',
    '원인': 'cause', 'cause': 'cause',
    '조치': 'action', 'action': 'action',
    '결과': 'result', 'result': 'result',
}


def parse_incident(path: Path) -> tuple[list[Item], dict]:
    """장애 기록의 `키: 값`을 구조화하고 본문과 embedding 표현을 분리한다.

    자유 형식 문서는 문단 청킹으로 fallback한다. 정형 필드가 두 개 이상인 문단은
    장애 단위로 유지해 증상/원인/조치가 서로 다른 청크로 잘리지 않게 한다.
    """
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    items: list[Item] = []
    structured = 0
    for block in re.split(r'\n\s*\n', text):
        block = block.strip()
        if not block:
            continue
        fields: dict[str, str] = {}
        for line in block.splitlines():
            match = re.match(r'^\s*([^:：]{1,40})\s*[:：]\s*(.+?)\s*$', line)
            if match and match.group(1).strip().lower() in INCIDENT_FIELDS:
                key = INCIDENT_FIELDS[match.group(1).strip().lower()]
                fields[key] = match.group(2).strip()
        if len(fields) >= 2:
            structured += 1
            order = ('incident_id','system','product','version','symptom','cause','action','result')
            labels = {
                'incident_id':'장애번호', 'system':'시스템', 'product':'제품', 'version':'버전',
                'symptom':'증상', 'cause':'원인', 'action':'조치', 'result':'결과',
            }
            embedding_text = '[장애]\n' + '\n'.join(
                f'{labels[key]}: {fields[key]}' for key in order if fields.get(key)
            )
            items.append(Item(
                kind='incident', text=block, embedding_text=embedding_text,
                identifier=fields.get('incident_id'), metadata={'incident': fields},
            ))
        else:
            items.append(Item(kind='incident', text=block))
    return items, {'characters': len(text), 'incidents': len(items), 'structured_incidents': structured}


def parse_logs(path: Path) -> tuple[list[Item], dict]:
    """Java trace의 Caused by / at 프레임을 유지한다. 패턴 외 로그는 라인 묶음으로 보존."""
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    start, current = 1, []
    for line_no, line in enumerate(lines, 1):
        # 새 timestamp 또는 ERROR 레코드가 나오면 기존 stack 전체를 닫는다.
        boundary = bool(re.match(r'^\d{4}-\d{2}-\d{2}[ T]|^\[?(?:ERROR|WARN|FATAL)\b', line))
        if boundary and current:
            blocks.append((start, '\n'.join(current))); start, current = line_no, []
        if not current: start = line_no
        current.append(line)
        if len(current) >= 250:  # 무제한 길이의 파일을 한 벡터로 만들지 않는다.
            blocks.append((start, '\n'.join(current))); current = []
    if current: blocks.append((start, '\n'.join(current)))
    items = []
    for start, block in blocks:
        code = CODE_RE.search(block)
        exception = EXCEPTION_RE.search(block)
        frames = [{'class_name': m.group(1), 'method_name': m.group(2), 'file_name': m.group(3),
                   'line_number': int(m.group(4)) if m.group(4) else None,
                   'is_application_code': not m.group(1).startswith(
                       ('java.', 'javax.', 'jdk.', 'sun.', 'org.springframework.'))}
                  for m in FRAME_RE.finditer(block)]
        timestamp_match = re.match(r'^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})', block)
        occurred_at = None
        if timestamp_match:
            try:
                occurred_at = datetime.fromisoformat(timestamp_match.group(1).replace(' ', 'T'))
            except ValueError:
                pass
        severity_match = re.search(r'\b(ERROR|WARN|FATAL|INFO|DEBUG)\b', block)
        caused_by = re.search(r'Caused by:\s*([\w.]+(?:Exception|Error))(?::\s*([^\n]+))?', block)
        # 식별자는 정확 검색 경로를 위해 본문과 별도로 저장한다.
        normalized = re.sub(r'\b\d{4}-\d{2}-\d{2}[ T]\S+', '', block)
        normalized = re.sub(r':\d+\)', ':LINE)', normalized)
        fingerprint = hashlib.sha256(normalized[:4000].encode()).hexdigest()
        primary = frames[0] if frames else None
        embedding_text = '\n'.join(filter(None, [
            f'Error code: {code.group()}' if code else None,
            f'Exception: {exception.group()}' if exception else None,
            (f"Primary location: {primary['class_name']}.{primary['method_name']} "
             f"{primary['file_name']}:{primary['line_number'] or ''}") if primary else None,
            f'Caused by: {caused_by.group(1)} {caused_by.group(2) or ""}' if caused_by else None,
            block[:4000],
        ]))
        items.append(Item(kind='error_trace', text=block[:24000], embedding_text=embedding_text,
                          line_start=start,
                          line_end=start + block.count('\n'), identifier=code.group() if code else
                          (exception.group() if exception else None),
                          error={'error_code': code.group() if code else None,
                                 'exception_class': exception.group() if exception else None,
                                 'occurred_at': occurred_at,
                                 'severity': severity_match.group(1) if severity_match else None,
                                 'root_cause': caused_by.group(0) if caused_by else None,
                                 'message': block.splitlines()[0][:2000], 'raw_trace': block,
                                 'fingerprint': fingerprint, 'frames': frames}))
    return items, {'events': len(items)}


def parse_code(
    path: Path,
    repository: str,
    branch: str,
    file_path: str,
    *,
    safe_java_parser: bool = False,
) -> tuple[list[Item], dict]:
    """Python은 표준 ast, Java는 tree-sitter 또는 안전 분석기로 심볼 경계를 얻는다.

    ``safe_java_parser=True``는 JAR처럼 다량의 외부/디컴파일 소스를 처리할 때 사용한다.
    이 경로는 네이티브 모듈을 전혀 호출하지 않아 잘못된 소스나 ABI 불일치가 Worker
    프로세스 전체를 segmentation fault로 종료시키는 문제를 방지한다.
    """
    source = path.read_text(encoding='utf-8-sig')
    lines = source.splitlines()
    suffix = path.suffix.lower()
    language = {'.py': 'python', '.java': 'java'}.get(suffix, suffix.lstrip('.') or 'text')
    definitions: list[tuple[str, str, str | None, int, int, str | None]] = []
    package_name: str | None = None
    if suffix == '.py':
        tree = ast.parse(source)
        def walk(body, owner=None):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    definitions.append(('class', node.name, owner, node.lineno, node.end_lineno, None))
                    walk(node.body, node.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definitions.append(('method' if owner else 'function', node.name, owner,
                                        node.lineno, node.end_lineno, None))
        walk(tree.body)
    elif suffix == '.java':
        # 폐쇄망에서는 grammar wheel까지 반입해야 한다. 실패 시 잘못된 심볼 대신 파일 청크만 사용한다.
        raw = source.encode('utf-8')
        package_match = re.search(r'^\s*package\s+([\w.]+)\s*;', source, re.M)
        package_name = package_match.group(1) if package_match else None
        if safe_java_parser:
            definitions.extend(_parse_java_definitions_without_native(source))
        else:
            tree = _get_java_parser().parse(raw)
            if tree.root_node.has_error:
                raise ValueError('Java AST 구문 오류: 소스/grammar 버전을 확인하세요')
            def walk(node, owner=None):
                kind_map = {'class_declaration':'class', 'interface_declaration':'interface',
                            'method_declaration':'method', 'constructor_declaration':'constructor',
                            'enum_declaration':'enum'}
                current_owner = owner
                if node.type in kind_map:
                    name_node = node.child_by_field_name('name')
                    name = raw[name_node.start_byte:name_node.end_byte].decode('utf-8') if name_node else '?'
                    signature = lines[node.start_point.row].strip()[:1000] if lines else None
                    definitions.append((kind_map[node.type], name, owner, node.start_point.row + 1,
                                        node.end_point.row + 1, signature))
                    if kind_map[node.type] in ('class','interface','enum'): current_owner = name
                for child in node.children: walk(child, current_owner)
            walk(tree.root_node)
    items: list[Item] = []
    # 모듈 개요로 주석, import, top-level 설정이 검색에서 누락되지 않게 한다.
    header = '\n'.join(lines[:min(70, len(lines))])
    if definitions and header.strip():
        header_metadata = {'repository': repository, 'branch': branch,
                           'file_path': file_path, 'language': language}
        if suffix == '.java':
            header_metadata.update(extract_java_diagnostics(header))
        items.append(Item(kind='code_file', text=f'Repository: {repository}\nPath: {file_path}\n{header}',
                          line_start=1, line_end=min(70, len(lines)), identifier=path.name,
                          metadata=header_metadata))
    for kind, name, owner, first, last, signature in definitions:
        # 긴 함수의 원문은 여러 청크로 분할. 각 청크에 동일 심볼과 원래 줄 범위를 표시한다.
        for offset in range(first - 1, last, 120):
            end = min(offset + 120, last)
            code = '\n'.join(lines[offset:end])
            metadata = {'repository': repository, 'branch': branch, 'file_path': file_path,
                        'language': language, 'symbol_type': kind, 'class_name': owner}
            if suffix == '.java':
                metadata.update(extract_java_diagnostics(
                    code, signature=signature, symbol_name=name, symbol_type=kind
                ))
            stored_kind = 'exception_class' if metadata.get('is_exception_class') else kind
            metadata['symbol_type'] = stored_kind
            context = (f'Repository: {repository}\nBranch: {branch}\nPath: {file_path}\n'
                       f'Package: {package_name or ""}\nClass: {owner or ""}\nSymbol: {name}\n'
                       f'Signature: {signature or ""}\n'
                       f'{_java_diagnostic_context(metadata)}')
            items.append(Item(kind='code_symbol', text=code, embedding_text=context + code,
                              line_start=offset + 1, line_end=end, identifier=name,
                              parent_id=f'{file_path}:{first}:{name}',
                              metadata=metadata,
                              symbol={'repository': repository, 'branch': branch, 'file_path': file_path,
                                      'language': language, 'symbol_type': stored_kind, 'class_name': owner,
                                      'package_name': package_name,
                                      'symbol_name': name, 'signature': signature,
                                      'start_line': offset + 1, 'end_line': end}))
    if not definitions:
        for offset in range(0, len(lines), 120):
            code = '\n'.join(lines[offset:offset+120])
            metadata = {'repository': repository, 'branch': branch, 'file_path': file_path,
                        'language': language}
            if suffix == '.java':
                metadata.update(extract_java_diagnostics(code))
            context = (f'Repository: {repository}\nBranch: {branch}\nPath: {file_path}\n'
                       f'{_java_diagnostic_context(metadata)}')
            items.append(Item(kind='code_file', text=code, embedding_text=context + code,
                              line_start=offset + 1, line_end=min(offset + 120, len(lines)),
                              identifier=path.name, metadata=metadata))
    exception_types = sorted({
        value for item in items for value in item.metadata.get('exception_types', [])
    })
    error_codes = sorted({
        value for item in items for value in item.metadata.get('error_codes', [])
    })
    return items, {
        'symbols': len(definitions),
        'language': language,
        'exception_types': exception_types,
        'error_codes': error_codes,
        'exception_class_count': len({
            (item.metadata.get('file_path'), item.identifier)
            for item in items if item.metadata.get('is_exception_class')
        }),
    }
