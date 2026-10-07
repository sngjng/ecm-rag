"""파일 유형에 따른 정규화. 원문과 추출 좌표를 별도로 남긴다."""
from __future__ import annotations
import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid5


CODE_RE = re.compile(r'\b(?:ORA-\d{5}|SQLSTATE[=: ]+[0-9A-Z]{5}|[A-Z]{3,8}\d{3,6}[A-Z]?|SQLCODE[=: ]+-?\d+)\b')
EXCEPTION_RE = re.compile(r'\b(?:[\w.]+(?:Exception|Error))\b')
FRAME_RE = re.compile(r'^\s*at\s+([\w.$]+)\.([\w$<>]+)\(([^():]+)(?::(\d+))?\)', re.M)

@dataclass
class Item:
    kind: str
    text: str
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
    """기존 보험약관용 Docling/표 pipeline 재사용. 표 셀/페이지 JSON 보존."""
    # PDF 이외 파일은 Docling의 무거운 torch/model dependency 없이 처리할 수 있다.
    from chunking.semantic_chunker import chunk_text_document
    from chunking.table_chunker import chunk_table
    from ingestion.docling_parser import DoclingParser
    from normalization.document_normalizer import normalize_document
    from table.table_extractor import extract_tables
    from table.table_validator import validate_table
    raw = DoclingParser().parse(path)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'docling.json').write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
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
    return items, {'pages': doc.page_count, 'tables': len(doc.tables), 'blocks': len(doc.blocks),
                   'low_quality_tables': [t.table_id for t in doc.tables if t.validation_score < .8]}


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
                   'line_number': int(m.group(4)) if m.group(4) else None}
                  for m in FRAME_RE.finditer(block)]
        # 식별자는 정확 검색 경로를 위해 본문과 별도로 저장한다.
        normalized = re.sub(r'\b\d{4}-\d{2}-\d{2}[ T]\S+', '', block)
        normalized = re.sub(r':\d+\)', ':LINE)', normalized)
        fingerprint = hashlib.sha256(normalized[:4000].encode()).hexdigest()
        items.append(Item(kind='error_trace', text=block[:24000], line_start=start,
                          line_end=start + block.count('\n'), identifier=code.group() if code else
                          (exception.group() if exception else None),
                          error={'error_code': code.group() if code else None,
                                 'exception_class': exception.group() if exception else None,
                                 'message': block.splitlines()[0][:2000], 'raw_trace': block,
                                 'fingerprint': fingerprint, 'frames': frames}))
    return items, {'events': len(items)}


def parse_code(path: Path, repository: str, branch: str, file_path: str) -> tuple[list[Item], dict]:
    """Python은 표준 ast, Java는 tree-sitter 구문 노드로 심볼 경계를 얻는다."""
    source = path.read_text(encoding='utf-8-sig')
    lines = source.splitlines()
    suffix = path.suffix.lower()
    language = {'.py': 'python', '.java': 'java'}.get(suffix, suffix.lstrip('.') or 'text')
    definitions: list[tuple[str, str, str | None, int, int, str | None]] = []
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
        from tree_sitter_language_pack import get_parser
        raw = source.encode('utf-8')
        tree = get_parser('java').parse(raw)
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
                definitions.append((kind_map[node.type], name, owner, node.start_point.row + 1,
                                    node.end_point.row + 1, None))
                if kind_map[node.type] in ('class','interface','enum'): current_owner = name
            for child in node.children: walk(child, current_owner)
        walk(tree.root_node)
    items: list[Item] = []
    # 모듈 개요로 주석, import, top-level 설정이 검색에서 누락되지 않게 한다.
    header = '\n'.join(lines[:min(70, len(lines))])
    if header.strip():
        items.append(Item(kind='code_file', text=f'Repository: {repository}\nPath: {file_path}\n{header}',
                          line_start=1, line_end=min(70, len(lines)), identifier=path.name,
                          metadata={'repository': repository, 'branch': branch, 'file_path': file_path, 'language': language}))
    for kind, name, owner, first, last, signature in definitions:
        context = f'Repository: {repository}\nBranch: {branch}\nPath: {file_path}\nClass: {owner or ""}\nSymbol: {name}\n'
        # 긴 함수의 원문은 여러 청크로 분할. 각 청크에 동일 심볼과 원래 줄 범위를 표시한다.
        for offset in range(first - 1, last, 120):
            end = min(offset + 120, last)
            items.append(Item(kind='code_symbol', text=context + '\n'.join(lines[offset:end]),
                              line_start=offset + 1, line_end=end, identifier=name,
                              parent_id=f'{file_path}:{first}:{name}',
                              metadata={'repository': repository, 'branch': branch, 'file_path': file_path,
                                        'language': language, 'symbol_type': kind, 'class_name': owner},
                              symbol={'repository': repository, 'branch': branch, 'file_path': file_path,
                                      'language': language, 'symbol_type': kind, 'class_name': owner,
                                      'symbol_name': name, 'signature': signature,
                                      'start_line': offset + 1, 'end_line': end}))
    if not definitions:
        for offset in range(0, len(lines), 120):
            items.append(Item(kind='code_file', text=f'Path: {file_path}\n' + '\n'.join(lines[offset:offset+120]),
                              line_start=offset + 1, line_end=min(offset + 120, len(lines)),
                              identifier=path.name, metadata={'repository': repository,
                              'branch': branch, 'file_path': file_path, 'language': language}))
    return items, {'symbols': len(definitions), 'language': language}
