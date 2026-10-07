"""Chroma dense retrieval + BGE reranker 동작을 확인하는 CLI."""

from __future__ import annotations

import argparse

from embedding.bge_m3 import BgeM3Embedder
from retrieval.reranker import BgeReranker
from storage.chroma import ChromaStore


def main() -> None:
    p = argparse.ArgumentParser(description="보험약관 RAG 검색 품질 테스트")
    p.add_argument("query", help="자연어 검색 질의")
    p.add_argument("--model", default="BAAI/bge-m3")
    p.add_argument("--reranker", default="BAAI/bge-reranker-v2-m3")
    p.add_argument("--chroma", default="./artifacts/chroma")
    p.add_argument("--collection", default="insurance_policy_chunks")
    args = p.parse_args()

    # 1) 질의를 BGE-M3 dense vector로 변환한다.
    emb = BgeM3Embedder(args.model)
    qvec = emb.encode_dense([args.query])[0]

    # 2) Chroma에서 넉넉하게 top 30 후보를 가져온다.
    result = ChromaStore(args.chroma, args.collection).query(qvec, top_k=30)
    docs = result.get("documents", [[]])[0]
    ids = result.get("ids", [[]])[0]

    # 3) cross-encoder reranker로 최종 top 8을 선택한다.
    reranked = BgeReranker(args.reranker).rerank(
        args.query,
        docs,
        top_k=8,
    )

    # 4) 현재 CLI는 검색 품질 확인용이므로 LLM 답변 생성 없이 후보 내용을 출력한다.
    for rank, (idx, score) in enumerate(reranked, start=1):
        print(f"#{rank} score={score:.4f} id={ids[idx]}")
        print(docs[idx][:1200])
        print("-" * 80)


if __name__ == "__main__":
    main()
