"""보험약관 제목 패턴을 식별하는 정규식 기반 heading classifier.

현재 단계에서는 LLM이나 형태소 분석기를 사용하지 않고 보험약관에서 반복적으로
등장하는 정형 패턴을 우선 사용한다. 규칙 기반이므로 재현성과 디버깅이 쉽고,
오탐/누락 사례가 발견되면 테스트 케이스와 정규식을 함께 보강할 수 있다.
"""

from __future__ import annotations

import re

# 예: 제3조, 제3조의2, 제15조 (중도인출금)
ARTICLE_RE = re.compile(r"제\s*\d+(?:조의\d+|조)(?:\s*\([^)]*\))?")
# 예: 2-4 암진단Ⅱ(...)보장 특별약관
SPECIAL_RE = re.compile(r"^\s*\d+(?:-\d+)+\s+.+특별약관")
# 예: 제2관 보험금의 지급
SECTION_RE = re.compile(r"^\s*제\d+관\s+.+")
# 예: [별표40-1] 본인일부부담금 ...
APPENDIX_RE = re.compile(r"^\s*\[별표\s*\d+(?:-\d+)?\]\s*.+")


def classify_heading(text: str) -> str | None:
    """문자열이 보험약관의 어느 heading 수준인지 판정한다.

    Returns
    -------
    str | None
        appendix / special_clause / section / article 중 하나 또는 일반 본문이면 None.
    """
    # PDF 파싱에서 줄바꿈/다중 공백이 흔하므로 먼저 한 줄 형태로 정리한다.
    value = " ".join(text.split())

    # 더 구체적인 패턴부터 우선 판정한다.
    if APPENDIX_RE.match(value):
        return "appendix"
    if SPECIAL_RE.match(value):
        return "special_clause"
    if SECTION_RE.match(value):
        return "section"
    if ARTICLE_RE.search(value):
        return "article"
    return None
