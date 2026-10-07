"""검색 단계 평가 지표."""

from __future__ import annotations


def recall_at_k(expected_ids: set[str], retrieved_ids: list[str], k: int) -> float:
    """정답 source/chunk ID 중 top-k에서 회수된 비율을 계산한다."""
    if not expected_ids:
        return 0.0
    return len(expected_ids.intersection(retrieved_ids[:k])) / len(expected_ids)
