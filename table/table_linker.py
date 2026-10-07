"""페이지를 넘어 이어지는 표인지 판정하기 위한 초기 continuation detector."""

from __future__ import annotations

from difflib import SequenceMatcher

from models.schemas import CanonicalTable
from table.header_builder import build_header_paths
from table.span_resolver import expand_spans


def header_signature(table: CanonicalTable, header_rows: int = 1) -> str:
    """표의 앞쪽 헤더를 비교 가능한 문자열 signature로 만든다."""
    grid = expand_spans(table)
    return " | ".join(build_header_paths(grid, header_rows))


def likely_continuation(
    a: CanonicalTable,
    b: CanonicalTable,
    threshold: float = 0.72,
) -> bool:
    """두 표가 동일 logical table의 연속 조각일 가능성을 heuristic으로 판정한다.

    현재는 페이지 연속성 + 열 수 + 헤더 유사도를 사용한다. 실제 보험약관에서는
    다음 페이지에 헤더가 생략되는 경우도 있으므로 향후 bbox/열 x좌표/직전·직후 본문
    등을 추가해 정교화할 예정이다.
    """
    if not a.source_pages or not b.source_pages:
        return False

    # 같은 페이지 또는 바로 다음 페이지에 있어야 continuation 후보로 본다.
    if min(b.source_pages) - max(a.source_pages) not in {0, 1}:
        return False

    if a.n_cols and b.n_cols and a.n_cols != b.n_cols:
        return False

    sim = SequenceMatcher(None, header_signature(a), header_signature(b)).ratio()
    return sim >= threshold
