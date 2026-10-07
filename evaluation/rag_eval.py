"""최종 RAG 답변의 근거 사용 상태를 평가하는 최소 지표."""

from __future__ import annotations


def citation_coverage(
    answer_source_ids: set[str],
    provided_source_ids: set[str],
) -> float:
    """답변이 주장한 source ID가 실제 제공 문맥에 포함된 비율을 계산한다."""
    if not answer_source_ids:
        return 1.0
    return len(answer_source_ids & provided_source_ids) / len(answer_source_ids)
