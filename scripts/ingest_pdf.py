"""PDF -> Canonical JSON -> Chunk JSONL까지 수행하는 ingestion entry point.

초기 v0.1.0에서는 가장 중요한 '파싱/정규화/청킹'을 한 명령으로 검증할 수 있게 한다.
벡터 생성과 PostgreSQL 적재는 build_index.py에서 분리해, 파싱 결과를 먼저 사람이 검수할
수 있도록 설계했다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from chunking.semantic_chunker import chunk_text_document
from chunking.table_chunker import chunk_table
from ingestion.docling_parser import DoclingParser
from normalization.document_normalizer import normalize_document
from table.table_extractor import extract_tables
from table.table_validator import validate_table


def main() -> None:
    parser = argparse.ArgumentParser(
        description="보험약관 PDF를 canonical JSON과 검색용 chunks로 변환"
    )
    parser.add_argument("pdf", type=Path, help="입력 보험약관 PDF")
    parser.add_argument("--out", type=Path, default=Path("artifacts"), help="산출물 디렉터리")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="개발/회귀 테스트용 최대 처리 페이지 수",
    )
    parser.add_argument(
        "--no-cell-matching",
        action="store_true",
        help="Docling TableFormer cell matching 비활성화 비교 테스트",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Docling 1차 파싱
    # ------------------------------------------------------------------
    raw, markdown = DoclingParser(
        do_cell_matching=not args.no_cell_matching
    ).parse_bundle(args.pdf, args.max_pages)

    # Docling 원본 JSON을 반드시 남긴다.
    # 이후 canonical 변환이 잘못됐는지, Docling부터 잘못됐는지 구분하는 기준점이다.
    raw_path = args.out / "canonical" / f"{args.pdf.stem}.docling.json"
    DoclingParser.save_json(raw, raw_path)
    markdown_path = args.out / "canonical" / f"{args.pdf.stem}.document.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    # ------------------------------------------------------------------
    # 2. 일반 텍스트/계층 구조 정규화
    # ------------------------------------------------------------------
    doc = normalize_document(raw, args.pdf.name)

    # ------------------------------------------------------------------
    # 3. 표 구조 정규화 + 품질 검증
    # ------------------------------------------------------------------
    tables = extract_tables(raw, doc.document_id)
    for table in tables:
        score, warnings = validate_table(table)
        table.validation_score = score
        table.warnings = warnings
    doc.tables = tables

    # 사람이 비교/검수할 수 있는 canonical 결과 저장.
    canonical_path = args.out / "canonical" / f"{args.pdf.stem}.canonical.json"
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # 4. 검색용 chunk 생성
    # ------------------------------------------------------------------
    # 일반 텍스트는 heading-aware chunking.
    chunks = chunk_text_document(doc)

    # 표는 span 복원 + row fact + parent/prev/next link 기반 chunking.
    for table in doc.tables:
        chunks.extend(chunk_table(doc.document_id, table))

    # JSONL은 대용량 문서에서 한 줄씩 streaming 처리하기 편하므로 사용한다.
    chunks_path = args.out / "chunks" / f"{args.pdf.stem}.chunks.jsonl"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with chunks_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")

    # ------------------------------------------------------------------
    # 5. 파싱 품질 리포트
    # ------------------------------------------------------------------
    # 낮은 품질 표를 별도로 뽑아 향후 RapidOCR/PaddleOCR/VLM fallback 후보로 사용한다.
    report = {
        "document_id": doc.document_id,
        "blocks": len(doc.blocks),
        "tables": len(doc.tables),
        "chunks": len(chunks),
        "low_quality_tables": [
            {
                "table_id": t.table_id,
                "score": t.validation_score,
                "pages": t.source_pages,
                "warnings": t.warnings,
            }
            for t in doc.tables
            if t.validation_score < 0.8
        ],
    }

    report_path = args.out / "reports" / f"{args.pdf.stem}.report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
