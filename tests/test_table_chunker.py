"""표가 여러 child chunk로 나뉠 때 연결 메타데이터를 검증한다."""

from chunking.table_chunker import chunk_table
from models.schemas import CanonicalTable, TableCell


def test_table_chunks_keep_parent_and_links():
    cells = [
        TableCell(row=0, col=0, text="구분"),
        TableCell(row=0, col=1, text="지급사유"),
    ]

    # 7개 데이터 행을 만들어 max_rows=3 조건에서 강제로 여러 chunk로 나눈다.
    for i in range(1, 8):
        cells.extend(
            [
                TableCell(row=i, col=0, text="암"),
                TableCell(row=i, col=1, text=f"조건{i}"),
            ]
        )

    table = CanonicalTable(
        table_id="T",
        n_rows=8,
        n_cols=2,
        cells=cells,
        source_pages=[46, 47],
    )

    chunks = chunk_table(
        "D",
        table,
        max_rows=3,
        target_tokens=10000,
        overlap_rows=1,
    )

    assert len(chunks) >= 3

    # 모든 조각은 같은 논리 표 T를 parent로 가져야 한다.
    assert all(c.parent_id == "T" for c in chunks)

    # 중간 hit에서 앞뒤 context expansion이 가능하도록 양방향 연결이 있어야 한다.
    assert chunks[0].next_chunk_id == chunks[1].chunk_id
    assert chunks[1].previous_chunk_id == chunks[0].chunk_id
