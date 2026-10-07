"""일반 텍스트 블록을 보험약관 문서 계층을 보존하며 청킹한다."""

from __future__ import annotations

import re

from models.schemas import CanonicalDocument, Chunk


def _rough_tokens(text: str) -> int:
    """정밀 tokenizer 없이 빠르게 chunk 크기를 제한하기 위한 근사값."""
    return max(1, len(re.findall(r"\S+", text)) * 2)


def chunk_text_document(
    doc: CanonicalDocument,
    target_tokens: int = 700,
    max_tokens: int = 1000,
) -> list[Chunk]:
    """일반 본문을 heading-aware 방식으로 분할한다.

    고정 길이로 무조건 자르지 않고, heading_path가 바뀌는 시점을 우선 경계로 사용한다.
    단 너무 짧은 chunk가 양산되지 않도록 target_tokens 이상일 때만 적극 flush한다.
    """
    chunks: list[Chunk] = []
    buf: list[str] = []
    pages: set[int] = set()
    heading_path: list[str] = []

    def flush() -> None:
        """현재 buffer를 하나의 Chunk로 확정하고 상태를 초기화한다."""
        if not buf:
            return

        idx = len(chunks)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.document_id}:text:{idx:06d}",
                document_id=doc.document_id,
                kind="text",
                retrieval_text="\n".join(buf).strip(),
                chunk_index=idx,
                source_pages=sorted(pages),
                heading_path=list(heading_path),
            )
        )
        buf.clear()
        pages.clear()

    for block in doc.blocks:
        # 새 heading 계층이 등장했고 기존 buffer가 충분히 크면 논리 경계에서 chunk를 닫는다.
        if block.heading_path:
            if (
                heading_path
                and block.heading_path != heading_path
                and _rough_tokens("\n".join(buf)) >= target_tokens
            ):
                flush()
            heading_path = list(block.heading_path)

        candidate = "\n".join([*buf, block.text])

        # 논리 경계가 없어도 max_tokens 초과는 방지한다.
        if buf and _rough_tokens(candidate) > max_tokens:
            flush()

        buf.append(block.text)
        pages.add(block.source.page_no)

    flush()

    # 텍스트 청크도 표 청크와 동일하게 순서 연결 정보를 부여한다.
    for idx, chunk in enumerate(chunks):
        chunk.chunk_count = len(chunks)
        chunk.previous_chunk_id = chunks[idx - 1].chunk_id if idx else None
        chunk.next_chunk_id = chunks[idx + 1].chunk_id if idx + 1 < len(chunks) else None

    return chunks
