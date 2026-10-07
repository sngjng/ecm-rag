"""BGE reranker를 사용해 1차 검색 후보를 재정렬한다."""

from __future__ import annotations

from FlagEmbedding import FlagReranker


class BgeReranker:
    """query-document pair를 cross-encoder 방식으로 재평가한다."""

    def __init__(self, model_name_or_path: str, use_fp16: bool = True) -> None:
        # 폐쇄망에서는 로컬 모델 디렉터리를 전달한다.
        self.model = FlagReranker(model_name_or_path, use_fp16=use_fp16)

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 8,
    ) -> list[tuple[int, float]]:
        """후보 문서를 query 관련도 기준으로 재정렬한다.

        반환 index는 입력 documents의 index이므로 원래 chunk ID/metadata와 다시
        연결할 수 있다.
        """
        pairs = [[query, doc] for doc in documents]
        scores = self.model.compute_score(pairs, normalize=True)

        # 단일 pair일 때 scalar가 반환될 가능성을 방어한다.
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if not isinstance(scores, list):
            scores = [scores]

        ranked = sorted(
            enumerate(scores),
            key=lambda x: float(x[1]),
            reverse=True,
        )
        return [(idx, float(score)) for idx, score in ranked[:top_k]]
