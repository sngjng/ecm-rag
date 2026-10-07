"""정규화된 표 record를 deterministic 검색 문장으로 직렬화한다."""

from __future__ import annotations


def record_to_fact(
    table_title: str | None,
    article_path: list[str],
    record: dict[str, str],
    footnotes: list[str] | None = None,
) -> str:
    """한 표 행을 embedding/reranking에 적합한 self-contained text로 만든다.

    자유 생성형 LLM으로 요약하지 않고 규칙 기반 문자열을 만들기 때문에
    지급금액/조건/비율 등 보험약관의 숫자 사실을 임의로 바꾸지 않는다.
    """
    prefix: list[str] = []

    if table_title:
        prefix.append(f"[표] {table_title}")

    # 원래 표가 어느 특약/조항에 속하는지 매 행에 반복 주입한다.
    if article_path:
        prefix.append(f"[문맥] {' > '.join(article_path)}")

    # 빈 값은 제외하여 불필요한 noise를 줄인다.
    body = "; ".join(f"{k} = {v}" for k, v in record.items() if v)

    # 현재 v0.1.0에서는 table-level footnote를 모든 row fact에 반복 주입한다.
    # 향후 cell-specific footnote mapping으로 세분화할 예정이다.
    notes = " ".join(f"[각주] {x}" for x in (footnotes or []))

    return "\n".join([*prefix, body, notes]).strip()
