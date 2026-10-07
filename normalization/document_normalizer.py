"""Docling JSON을 보험약관용 CanonicalDocument로 정규화한다.

Docling의 export schema와 downstream RAG 로직을 직접 결합하지 않기 위한 계층이다.
이 모듈에서는 주로 본문/제목을 처리하고, 표는 table/table_extractor.py에서 별도로
구조화한다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from models.schemas import BlockType, CanonicalBlock, CanonicalDocument, SourceRef
from normalization.article_parser import classify_heading


def _iter_text_items(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Docling payload에서 텍스트 성격의 노드만 순회한다.

    Docling 버전에 따라 최상위 키가 일부 달라질 수 있어 특정 한 키에 강하게
    의존하지 않고 대표 container를 순회한다. 이후 실제 배포 버전에 맞춰 schema
    adapter를 더 엄격하게 분리하는 것이 좋다.
    """
    for key in ("texts", "groups", "key_value_items"):
        items = payload.get(key, [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and (item.get("text") or item.get("orig")):
                    yield item


def _page_from_item(item: dict[str, Any]) -> int:
    """Docling provenance에서 1-based 페이지 번호를 얻는다."""
    prov = item.get("prov") or []
    if prov and isinstance(prov, list) and isinstance(prov[0], dict):
        return int(prov[0].get("page_no", 1))
    return 1


def normalize_document(payload: dict[str, Any], filename: str | None = None) -> CanonicalDocument:
    """Docling 결과를 보험약관 계층이 포함된 CanonicalDocument로 변환한다.

    heading_stack은 현재 문장이 어느 특별약관/관/조 아래에 있는지 기억한다.
    이 정보는 이후 청크를 잘라도 각 청크에 상위 문맥을 반복 주입할 때 사용한다.
    """
    filename = filename or payload.get("_source_filename", "document.pdf")

    # 파일명을 기반으로 재현 가능한 ID를 생성한다.
    # 운영 환경에서는 파일 hash + 버전/개정일 조합으로 강화할 수 있다.
    document_id = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:16]

    heading_stack: list[str] = []
    blocks: list[CanonicalBlock] = []

    for idx, item in enumerate(_iter_text_items(payload)):
        text = (item.get("text") or item.get("orig") or "").strip()
        if not text:
            continue

        # 보험약관 고유 패턴(특별약관, 관, 조, 별표)을 식별한다.
        htype = classify_heading(text)

        # 계층 수준에 따라 하위 문맥을 교체한다.
        if htype:
            if htype in {"special_clause", "appendix"}:
                # 새로운 특약/별표를 만나면 이전 조/관 문맥은 폐기한다.
                heading_stack = [text]
            elif htype == "section":
                # 특약/별표 수준은 유지하고 관(section)을 갱신한다.
                heading_stack = heading_stack[:1] + [text]
            elif htype == "article":
                # 특약 > 관 수준은 유지하고 조(article)를 갱신한다.
                heading_stack = heading_stack[:2] + [text]

        page = _page_from_item(item)
        blocks.append(
            CanonicalBlock(
                block_id=f"{document_id}:b:{idx:06d}",
                type=BlockType.TITLE if htype else BlockType.TEXT,
                text=text,
                heading_path=list(heading_stack),
                source=SourceRef(page_no=page),
                metadata={"heading_type": htype} if htype else {},
            )
        )

    # Docling pages가 dict인 경우 현재 변환된 페이지 수로 사용한다.
    page_count = None
    pages = payload.get("pages")
    if isinstance(pages, dict):
        page_count = len(pages)

    return CanonicalDocument(
        document_id=document_id,
        filename=Path(filename).name,
        page_count=page_count,
        blocks=blocks,
    )
