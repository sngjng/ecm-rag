"""dense table grid를 행 단위 key/value record로 변환한다."""

from __future__ import annotations

from table.header_builder import build_header_paths


def rows_to_records(
    grid: list[list[str]],
    header_rows: int = 1,
) -> tuple[list[str], list[dict[str, str]]]:
    """표를 `[header -> cell value]` 구조의 records로 변환한다.

    이 단계부터 각 행은 독립적인 의미 단위가 되며, 이후 fact_generator가
    embedding용 텍스트로 직렬화한다.
    """
    headers = build_header_paths(grid, header_rows)
    records: list[dict[str, str]] = []

    # header_rows 이후부터 실제 데이터 행으로 처리한다.
    for row in grid[header_rows:]:
        record: dict[str, str] = {}
        for idx, header in enumerate(headers):
            record[header] = row[idx].strip() if idx < len(row) else ""

        # 완전히 비어 있는 행은 검색 노이즈가 되므로 버린다.
        if any(record.values()):
            records.append(record)

    return headers, records
