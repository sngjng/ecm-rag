"""파싱 단계의 기본 품질 통계를 계산한다."""

from __future__ import annotations

from dataclasses import dataclass

from models.schemas import CanonicalDocument


@dataclass(slots=True)
class ParseReport:
    """문서 하나의 파싱 요약 지표."""

    blocks: int
    tables: int
    low_quality_tables: int


def build_parse_report(doc: CanonicalDocument) -> ParseReport:
    """validation_score 0.8 미만 표 개수를 포함한 파싱 요약을 만든다."""
    return ParseReport(
        blocks=len(doc.blocks),
        tables=len(doc.tables),
        low_quality_tables=sum(1 for t in doc.tables if t.validation_score < 0.8),
    )
