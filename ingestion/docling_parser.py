"""Docling 기반 PDF 1차 파서.

중요: 이 모듈의 목적은 PDF를 Markdown으로 예쁘게 만드는 것이 아니다.
Docling이 인식한 레이아웃/표 구조를 가능한 한 손실 없이 JSON(dict) 형태로
보존하는 것이 목적이다. 특히 복잡한 병합표는 Markdown 변환 시 rowspan/colspan
정보가 손실될 수 있으므로 export_to_dict() 결과를 canonical source로 사용한다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption
from loguru import logger


class DoclingParser:
    """Docling PDF 변환 설정을 프로젝트 규칙에 맞게 감싼 adapter."""

    def __init__(self, do_cell_matching: bool = True) -> None:
        """파서를 초기화한다.

        Parameters
        ----------
        do_cell_matching:
            True면 Docling이 PDF의 기존 텍스트 셀을 TableFormer 구조에 매핑한다.
            PDF 내부 텍스트 좌표가 정상적이면 좋지만, 복잡한 표에서 여러 열이 잘못
            합쳐지는 경우가 있으므로 False 결과와 비교 검증할 수 있게 옵션화했다.
        """
        # 표 구조 추출을 반드시 켠다. 보험약관에서 표를 단순 텍스트로 처리하지 않는다.
        options = PdfPipelineOptions(do_table_structure=True)
        # 오프라인 운영에서는 모델을 미리 반입하고 경로를 지정해야 네트워크 요청이 없다.
        artifacts = os.environ.get("RAG_DOCLING_ARTIFACTS")
        if artifacts:
            options.artifacts_path = artifacts

        # 정확도 우선. 1,300+ 페이지라 처리시간은 늘지만 표 구조 품질이 더 중요하다.
        options.table_structure_options.mode = TableFormerMode.ACCURATE
        options.table_structure_options.do_cell_matching = do_cell_matching

        # 향후 OCR/VLM fallback을 넣더라도 Docling을 1차 parser로 유지하도록 adapter화한다.
        self.converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )

    def parse(self, pdf_path: str | Path, max_pages: int | None = None) -> dict[str, Any]:
        """PDF를 Docling으로 변환하고 JSON 직렬화 가능한 dict를 반환한다.

        `max_pages`는 개발 단계에서 1,363페이지 전체를 매번 돌리지 않고
        특정 앞부분만 빠르게 회귀 테스트하기 위한 옵션이다.
        """
        pdf_path = Path(pdf_path)
        logger.info("Docling parse start: {}", pdf_path)

        kwargs: dict[str, Any] = {}
        if max_pages:
            kwargs["max_num_pages"] = max_pages

        result = self.converter.convert(pdf_path, **kwargs)
        document = result.document

        # Markdown이 아니라 구조 정보를 가진 dict를 저장한다.
        payload = document.export_to_dict()
        payload["_source_filename"] = pdf_path.name
        return payload

    @staticmethod
    def save_json(payload: dict[str, Any], output_path: str | Path) -> None:
        """Docling 원본 구조를 UTF-8 JSON으로 저장한다.

        이 파일은 파싱 문제가 생겼을 때 가장 먼저 확인해야 하는 디버깅 기준점이다.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
