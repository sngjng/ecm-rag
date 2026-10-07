"""복잡한 보험약관 표를 의미가 끊기지 않게 child chunk로 나누는 로직."""

from __future__ import annotations

from models.schemas import CanonicalTable, Chunk
from table.fact_generator import record_to_fact
from table.row_normalizer import rows_to_records
from table.span_resolver import expand_spans, inherit_sparse_values


def _estimate_tokens(text: str) -> int:
    """정밀 tokenizer 없이 빠르게 크기를 제한하기 위한 보수적 근사치."""
    # 한국어는 공백 기준 word count가 토큰 수를 과소평가하기 쉬워 문자수/2를 사용한다.
    return max(1, len(text) // 2)


def chunk_table(
    document_id: str,
    table: CanonicalTable,
    header_rows: int = 1,
    target_tokens: int = 900,
    max_rows: int = 30,
    overlap_rows: int = 1,
) -> list[Chunk]:
    """표를 검색용 child chunk로 변환한다.

    처리 순서
    ---------
    1. rowspan/colspan을 확장하여 모든 행이 독립 의미를 갖게 함
    2. 희소한 첫 번째 row-header를 상속
    3. 다단 헤더를 포함한 record(dict)로 변환
    4. 행 단위 fact 문자열로 직렬화
    5. 토큰/행 수 기준으로 chunk 분리
    6. overlap 및 prev/next/parent link 부여

    결과적으로 어떤 chunk 하나만 검색돼도 원래 표의 조건을 최대한 이해할 수 있고,
    필요하면 동일 parent table의 앞뒤 chunk를 다시 가져올 수 있다.
    """

    # 1) 병합 구조를 평면 grid로 확장한다.
    grid = inherit_sparse_values(expand_spans(table), header_rows=header_rows)

    # 2) 헤더 경로를 key로 가지는 row record를 생성한다.
    _, records = rows_to_records(grid, header_rows=header_rows)
    if not records:
        return []

    # groups에는 최종적으로 각 child chunk에 포함될 row record 목록이 들어간다.
    groups: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_tokens = 0

    for rec in records:
        # embedding에 들어갈 한 행의 의미 텍스트를 먼저 만들어 크기를 추정한다.
        fact = record_to_fact(table.title, table.article_path, rec, table.footnotes)
        tokens = _estimate_tokens(fact)

        # 현재 chunk가 충분히 찼다면 새 chunk를 시작한다.
        # max_rows는 비정상적으로 짧은 행이 많을 때 chunk가 너무 커지는 것을 막는 안전장치다.
        if current and (len(current) >= max_rows or current_tokens + tokens > target_tokens):
            groups.append(current)

            # 경계에서 문맥이 끊기지 않도록 마지막 1개(기본값) 행을 다음 chunk에 중복시킨다.
            current = current[-overlap_rows:] if overlap_rows else []
            current_tokens = sum(
                _estimate_tokens(record_to_fact(table.title, table.article_path, r, table.footnotes))
                for r in current
            )

        current.append(rec)
        current_tokens += tokens

    if current:
        groups.append(current)

    chunks: list[Chunk] = []

    for idx, group in enumerate(groups):
        # 각 행을 deterministic fact로 만들어 하나의 검색 텍스트로 합친다.
        # LLM 요약이 아니라 규칙 기반 직렬화이므로 원문 의미 왜곡 위험이 낮다.
        text = "\n".join(
            record_to_fact(table.title, table.article_path, rec, table.footnotes)
            for rec in group
        )

        chunks.append(
            Chunk(
                chunk_id=f"{table.table_id}:c:{idx:04d}",
                document_id=document_id,
                kind="table",
                retrieval_text=text,

                # 모든 child chunk가 동일 logical table을 가리킨다.
                parent_id=table.table_id,
                table_id=table.table_id,

                chunk_index=idx,
                chunk_count=len(groups),
                source_pages=table.source_pages,
                heading_path=table.article_path,
                metadata={
                    "row_count": len(group),
                    "validation_score": table.validation_score,
                },
            )
        )

    # 검색 hit가 중간 chunk여도 앞/뒤 chunk로 확장할 수 있도록 양방향 link를 만든다.
    for idx, chunk in enumerate(chunks):
        chunk.previous_chunk_id = chunks[idx - 1].chunk_id if idx else None
        chunk.next_chunk_id = chunks[idx + 1].chunk_id if idx + 1 < len(chunks) else None

    return chunks
