"""rowspan 복원 로직 회귀 테스트."""

from models.schemas import CanonicalTable, TableCell
from table.span_resolver import expand_spans, inherit_sparse_values


def test_rowspan_is_expanded_and_inherited():
    # 일반암 셀이 두 데이터 행에 걸쳐 병합된 대표 보험 표를 단순화한 fixture.
    table = CanonicalTable(
        table_id="t1",
        n_rows=3,
        n_cols=2,
        cells=[
            TableCell(row=0, col=0, text="구분"),
            TableCell(row=0, col=1, text="지급률"),
            TableCell(row=1, col=0, text="일반암", row_span=2),
            TableCell(row=1, col=1, text="100%"),
            TableCell(row=2, col=1, text="50%"),
        ],
    )

    grid = inherit_sparse_values(expand_spans(table), header_rows=1)

    # 청크가 두 번째 데이터 행에서 시작하더라도 상위 구분 '일반암'이 보존되어야 한다.
    assert grid[1][0] == "일반암"
    assert grid[2][0] == "일반암"
