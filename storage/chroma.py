"""ChromaDB 저장/검색 adapter.

향후 PostgreSQL/pgvector로 저장소를 바꾸더라도 upstream 코드 변경을 줄이기 위해
벡터 저장소 접근을 이 모듈에 한정한다.
"""

from __future__ import annotations

from typing import Any

import chromadb

from models.schemas import Chunk


class ChromaStore:
    """Persistent Chroma collection에 Chunk를 저장하고 검색한다."""

    def __init__(self, path: str, collection_name: str) -> None:
        # 폐쇄망/온프레미스 환경을 고려해 로컬 persistent client를 사용한다.
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Chunk 본문, 벡터, 메타데이터를 동일 ID로 upsert한다."""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length must match")

        self.collection.upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.retrieval_text for c in chunks],
            embeddings=embeddings,
            metadatas=[self._metadata(c) for c in chunks],
        )

    def query(self, query_embedding: list[float], top_k: int = 30) -> dict[str, Any]:
        """Dense vector 기준 top-k 후보를 조회한다."""
        return self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )

    @staticmethod
    def _metadata(chunk: Chunk) -> dict[str, Any]:
        """Chunk의 연결 정보를 Chroma metadata scalar 형태로 직렬화한다."""
        # Chroma 버전에 따라 metadata nested structure/array 지원 범위가 다를 수 있어
        # 복잡한 list는 문자열로 평탄화해 저장한다.
        return {
            "document_id": chunk.document_id,
            "kind": chunk.kind,
            "parent_id": chunk.parent_id or "",
            "table_id": chunk.table_id or "",
            "chunk_index": chunk.chunk_index,
            "chunk_count": chunk.chunk_count,
            "previous_chunk_id": chunk.previous_chunk_id or "",
            "next_chunk_id": chunk.next_chunk_id or "",
            "source_pages": ",".join(str(x) for x in chunk.source_pages),
            "heading_path": " > ".join(chunk.heading_path),
        }
