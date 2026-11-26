"""vLLM·Cohere 형식의 HTTP rerank endpoint를 호출한다."""
from __future__ import annotations

import math

from service.config import RerankerSettings
from service.http_client import get_http_client


def rerank_remote(
    settings: RerankerSettings, query: str, documents: list[str], top_k: int
) -> list[tuple[int, float]]:
    """원격 모델의 점수를 원래 후보의 위치와 함께 반환한다."""
    top_n = min(top_k, len(documents))
    if top_n == 0:
        return []
    client = get_http_client("reranker", settings.timeout_seconds, settings.api_key, 20, 10)
    response = client.post(
        settings.base_url,
        json={
            "model": settings.model,
            "query": query,
            "documents": [document[:settings.max_input_characters] for document in documents],
            "top_n": top_n,
        },
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != top_n:
        raise ValueError("리랭커 응답 결과 개수가 요청과 다릅니다")
    scores: dict[int, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("리랭커 응답 결과 형식이 올바르지 않습니다")
        index, score = row.get("index"), row.get("relevance_score")
        if type(index) is not int or not 0 <= index < len(documents) or index in scores:
            raise ValueError("리랭커 응답에 잘못되었거나 중복된 후보 번호가 있습니다")
        if type(score) not in (int, float) or not math.isfinite(score):
            raise ValueError("리랭커 응답의 관련도 점수가 올바르지 않습니다")
        scores[index] = float(score)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
