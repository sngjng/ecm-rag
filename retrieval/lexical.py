"""한국어 보험약관용 경량 BM25 lexical retrieval."""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi


def tokenize_ko(text: str) -> list[str]:
    """형태소 분석기 없이 보험 용어/숫자/코드를 보존하는 단순 tokenizer.

    예: '암진단Ⅱ', 질병코드, 50%, 1-180일 같은 표현은 exact-match 검색에서
    중요하므로 기호 일부(.%+-)를 token 내부에 허용한다.
    """
    return re.findall(r"[가-힣A-Za-z0-9.%+-]+", text.lower())


class BM25Index:
    """메모리 기반 BM25 인덱스. 초기 hybrid retrieval 검증용 구현."""

    def __init__(self, documents: list[str]) -> None:
        self.documents = documents
        self.tokenized = [tokenize_ko(x) for x in documents]
        self.index = BM25Okapi(self.tokenized)

    def search(self, query: str, top_k: int = 30) -> list[tuple[int, float]]:
        """query와 lexical 유사도가 높은 문서 index와 점수를 반환한다."""
        scores = self.index.get_scores(tokenize_ko(query))
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(idx, float(score)) for idx, score in ranked[:top_k]]
