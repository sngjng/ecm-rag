"""생성된 chunk JSONL을 BGE-M3로 벡터화해 ChromaDB에 저장한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from embedding.bge_m3 import BgeM3Embedder
from models.schemas import Chunk
from storage.chroma import ChromaStore


def load_chunks(path: Path) -> list[Chunk]:
    """JSONL 한 줄을 Pydantic Chunk로 검증하며 로드한다."""
    return [
        Chunk.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    p = argparse.ArgumentParser(description="보험약관 chunk를 Chroma에 벡터 인덱싱")
    p.add_argument("chunks", type=Path, help="ingest_pdf.py가 생성한 .chunks.jsonl")
    p.add_argument(
        "--model",
        default="BAAI/bge-m3",
        help="BGE-M3 모델명 또는 폐쇄망 로컬 모델 경로",
    )
    p.add_argument("--chroma", default="./artifacts/chroma")
    p.add_argument("--collection", default="insurance_policy_chunks")
    p.add_argument("--batch-size", type=int, default=8)
    args = p.parse_args()

    chunks = load_chunks(args.chunks)

    # 검색용 retrieval_text만 벡터화한다. canonical JSON은 근거 데이터로 별도 유지한다.
    embedder = BgeM3Embedder(args.model)
    vectors = embedder.encode_dense(
        [c.retrieval_text for c in chunks],
        batch_size=args.batch_size,
    )

    store = ChromaStore(args.chroma, args.collection)
    store.upsert(chunks, vectors)

    print(f"indexed={len(chunks)} collection={args.collection}")


if __name__ == "__main__":
    main()
