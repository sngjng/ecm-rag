"""추출된 표의 최소 구조 품질을 점수화하는 1차 validator."""

from __future__ import annotations

from models.schemas import CanonicalTable
from table.span_resolver import expand_spans


def validate_table(table: CanonicalTable) -> tuple[float, list[str]]:
    """표를 0~1 점수와 warning 목록으로 평가한다.

    현재 점수는 heuristic 기반의 초기 버전이다. 목적은 '완벽한 품질 판정'이 아니라
    명백히 깨진 표를 fallback OCR/parser 대상으로 분기하는 것이다.
    향후 숫자 보존율, 헤더 일관성, 열 수 변동, OCR confidence 등을 추가할 수 있다.
    """
    warnings: list[str] = []

    if not table.cells:
        return 0.0, ["no_cells"]

    grid = expand_spans(table)
    expected = len(grid) * (len(grid[0]) if grid else 0)
    non_empty = sum(1 for row in grid for value in row if value.strip())
    fill_ratio = non_empty / expected if expected else 0.0

    # 셀이 존재하는 표는 기본 점수 0.55에서 시작하고 채움 비율을 가산한다.
    score = 0.55 + min(fill_ratio, 1.0) * 0.35

    # 1행/1열 표는 실제 표가 아니라 레이아웃 오인식일 가능성이 있어 감점한다.
    if table.n_rows <= 1 or table.n_cols <= 1:
        score -= 0.2
        warnings.append("suspicious_dimensions")

    if fill_ratio < 0.25:
        warnings.append("low_fill_ratio")

    return max(0.0, min(1.0, score)), warnings
