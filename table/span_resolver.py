"""병합 셀(rowspan/colspan)을 검색 가능한 dense grid로 복원하는 모듈."""

from __future__ import annotations

from models.schemas import CanonicalTable


def expand_spans(table: CanonicalTable) -> list[list[str]]:
    """병합 셀 값을 병합 범위 전체에 복제해 2차원 문자열 grid를 만든다.

    예를 들어 '일반암' 셀이 3개 행을 rowspan하고 있다면 원 PDF에는 한 번만
    표시되지만, 청크가 중간에서 잘릴 수 있으므로 각 행에 '일반암'을 복제한다.
    이렇게 해야 child chunk 하나만 검색돼도 상위 조건이 사라지지 않는다.
    """
    # 선언된 n_rows/n_cols가 부정확할 수 있어 실제 cell span 끝 위치와 최대값을 취한다.
    rows = max(table.n_rows, max((c.row + c.row_span for c in table.cells), default=0))
    cols = max(table.n_cols, max((c.col + c.col_span for c in table.cells), default=0))

    grid = [["" for _ in range(cols)] for _ in range(rows)]

    for cell in table.cells:
        # 병합된 셀 범위 전체에 동일한 의미 값을 복제한다.
        for r in range(cell.row, min(rows, cell.row + max(cell.row_span, 1))):
            for c in range(cell.col, min(cols, cell.col + max(cell.col_span, 1))):
                # 이미 다른 셀이 들어간 경우 시작 좌표의 원본 셀을 우선한다.
                if not grid[r][c] or (r == cell.row and c == cell.col):
                    grid[r][c] = cell.text.strip()

    return grid


def inherit_sparse_values(grid: list[list[str]], header_rows: int = 1) -> list[list[str]]:
    """첫 번째 열의 비어 있는 row-header 값을 아래 행으로 상속한다.

    span 정보가 정상 추출되지 않은 표에서도 '구분' 열이 시각적으로 묶여 있는 경우가
    많아 보조적으로 forward-fill한다. 일반 데이터 열까지 무조건 forward-fill하면
    잘못된 의미를 만들 수 있으므로 현재는 첫 열만 제한적으로 처리한다.
    """
    if not grid:
        return grid

    # 원본 grid를 직접 변경하지 않기 위해 shallow row copy를 만든다.
    out = [row[:] for row in grid]
    last = ""

    # header 영역은 상속 대상에서 제외한다.
    for r in range(header_rows, len(out)):
        if out[r] and out[r][0].strip():
            last = out[r][0].strip()
        elif out[r] and last:
            out[r][0] = last

    return out
