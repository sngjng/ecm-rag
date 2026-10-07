"""Docling table JSON을 CanonicalTable로 변환한다."""

from __future__ import annotations

import hashlib
from typing import Any

from models.schemas import CanonicalTable, SourceRef, TableCell


def _int(cell: dict[str, Any], *names: str, default: int = 0) -> int:
    """Docling schema 버전 차이를 흡수하기 위한 정수 필드 helper."""
    for name in names:
        if name in cell and cell[name] is not None:
            return int(cell[name])
    return default


def extract_tables(payload: dict[str, Any], document_id: str) -> list[CanonicalTable]:
    """Docling payload의 모든 표를 CanonicalTable 목록으로 변환한다.

    여기서는 의미를 추론하지 않고 좌표/span/text/provenance 보존에 집중한다.
    헤더 판정, span 확장, 행 정규화는 이후 단계에서 수행한다.
    """
    out: list[CanonicalTable] = []

    for t_idx, raw in enumerate(payload.get("tables", []) or []):
        # Docling 버전에 따라 data/table_data 이름이 다를 가능성을 흡수한다.
        data = raw.get("data") or raw.get("table_data") or {}
        raw_cells = data.get("table_cells") or data.get("cells") or []

        cells: list[TableCell] = []
        pages: set[int] = set()

        for cell in raw_cells:
            text = str(cell.get("text", ""))

            # start/end offset 기반으로 rowspan/colspan을 복원한다.
            r0 = _int(cell, "start_row_offset_idx", "row", default=0)
            c0 = _int(cell, "start_col_offset_idx", "col", default=0)
            r1 = _int(cell, "end_row_offset_idx", default=r0 + 1)
            c1 = _int(cell, "end_col_offset_idx", default=c0 + 1)

            # 셀 provenance가 없으면 table 자체 provenance를 fallback으로 사용한다.
            prov = cell.get("prov") or raw.get("prov") or []
            page_no = 1
            if prov and isinstance(prov[0], dict):
                page_no = int(prov[0].get("page_no", 1))
                pages.add(page_no)

            cells.append(
                TableCell(
                    row=r0,
                    col=c0,
                    row_span=max(1, r1 - r0),
                    col_span=max(1, c1 - c0),
                    text=text,
                    is_row_header=bool(cell.get("row_header", False)),
                    is_col_header=bool(cell.get("column_header", False)),
                    source=SourceRef(page_no=page_no),
                )
            )

        n_rows = _int(data, "num_rows", "n_rows", default=0)
        n_cols = _int(data, "num_cols", "n_cols", default=0)

        # 표 순번 기반의 안정적인 ID. 향후 page+bbox 기반 ID로 강화 가능하다.
        tid = hashlib.sha1(f"{document_id}:{t_idx}".encode()).hexdigest()[:16]

        out.append(
            CanonicalTable(
                table_id=f"{document_id}:t:{tid}",
                source_pages=sorted(pages),
                n_rows=n_rows,
                n_cols=n_cols,
                cells=cells,
            )
        )

    return out
