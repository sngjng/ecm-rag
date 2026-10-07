"""페이지/표 품질에 따라 OCR fallback 엔진을 선택하는 정책 모듈."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class OcrDecision:
    engine: str | None
    reason: str


def choose_ocr(
    page_kind: str,
    table_validation_score: float | None = None,
) -> OcrDecision:
    """OCR 필요 여부와 사용할 엔진을 결정한다.

    실제 OCR 실행은 별도 adapter에서 수행하고, 이 함수는 정책만 담당한다.
    이렇게 분리하면 운영 중 threshold/엔진을 교체하기 쉽다.
    """
    if page_kind == "native_text" and (
        table_validation_score is None or table_validation_score >= 0.9
    ):
        return OcrDecision(None, "Native PDF text/layout is sufficient")

    if table_validation_score is not None and table_validation_score < 0.75:
        return OcrDecision("rapidocr", "Low table validation score; use second OCR path")

    return OcrDecision("easyocr", "Image/scanned content requires OCR")
