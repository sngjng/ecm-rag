"""PDF 페이지별로 native text / mixed / scanned 성격을 빠르게 분류한다."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(slots=True)
class PageProfile:
    """OCR routing에 사용할 페이지의 경량 프로파일."""

    page_no: int
    text_chars: int
    image_count: int
    kind: str


def classify_pdf(
    pdf_path: str | Path,
    min_native_chars: int = 80,
) -> list[PageProfile]:
    """PyMuPDF로 각 페이지의 텍스트/이미지 존재량을 확인한다.

    OCR은 비용이 크고 native PDF text보다 오탈자가 늘 수 있으므로 모든 페이지에
    무조건 OCR을 적용하지 않고 먼저 라우팅하기 위한 전처리다.
    """
    doc = fitz.open(pdf_path)
    out: list[PageProfile] = []

    for idx, page in enumerate(doc):
        text_chars = len(page.get_text("text").strip())
        image_count = len(page.get_images(full=True))

        if text_chars < min_native_chars and image_count:
            kind = "scanned_or_image"
        elif image_count and text_chars >= min_native_chars:
            kind = "mixed"
        else:
            kind = "native_text"

        out.append(PageProfile(idx + 1, text_chars, image_count, kind))

    return out
