"""Dense/lexical 등 서로 다른 검색 결과 순위를 RRF로 결합한다."""

from __future__ import annotations

from collections import defaultdict


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion 점수로 여러 ranking을 합친다.

    검색 엔진별 raw score scale이 달라도 순위만으로 안정적으로 결합할 수 있어
    exact + PostgreSQL FTS + pgvector hybrid 구성에 적합하다.
    """
    scores: dict[str, float] = defaultdict(float)

    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] += 1.0 / (k + rank)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
