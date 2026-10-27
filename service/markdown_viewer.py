"""업로드 문서의 Markdown을 웹 뷰어용 안전한 HTML로 변환한다.

업로드 파일에서 만들어진 Markdown은 신뢰할 수 없는 사용자 입력으로 취급한다. 먼저
원문 HTML을 escape하여 ``<script>`` 같은 태그가 Markdown parser에 그대로 전달되지
않게 하고, 변환 결과도 허용 태그·속성·URL scheme만 남긴다. 이중 방어를 적용하면
검색 결과에서 문서를 열어도 저장형 XSS가 실행되지 않는다.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

import markdown


_ALLOWED_TAGS = {
    "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "strong", "table",
    "tbody", "td", "th", "thead", "tr", "ul",
}
_VOID_TAGS = {"br", "hr"}


def _safe_link(value: str) -> str | None:
    """웹 뷰어에서 이동 가능한 안전한 링크만 반환한다."""
    cleaned = value.strip()
    if not cleaned:
        return None
    scheme = urlsplit(cleaned).scheme.lower()
    if scheme not in {"", "http", "https", "mailto"}:
        return None
    return cleaned


class _Sanitizer(HTMLParser):
    """Markdown 변환 결과를 최소 허용 목록으로 다시 직렬화한다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            return
        safe_attrs: list[str] = []
        if tag == "a":
            values = dict(attrs)
            href = _safe_link(values.get("href") or "")
            if href:
                safe_attrs.append(f'href="{escape(href, quote=True)}"')
            title = values.get("title")
            if title:
                safe_attrs.append(f'title="{escape(title, quote=True)}"')
            # 새 창에서 열리는 외부 링크가 opener를 제어하지 못하게 한다.
            if href and urlsplit(href).scheme in {"http", "https"}:
                safe_attrs.extend(['target="_blank"', 'rel="noopener noreferrer"'])
        suffix = f" {' '.join(safe_attrs)}" if safe_attrs else ""
        self.parts.append(f"<{tag}{suffix}>")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&amp;{escape(name)};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&amp;#{escape(name)};")

    def html(self) -> str:
        return "".join(self.parts)


def render_markdown(markdown_text: str) -> str:
    """Markdown을 표·목록·코드 블록을 지원하는 안전한 HTML로 변환한다."""
    # HTML 태그는 화면에서 문자 그대로 보이게 하고 실행 가능한 DOM으로 만들지 않는다.
    escaped_markdown = escape(markdown_text, quote=False)
    rendered = markdown.markdown(
        escaped_markdown,
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )
    sanitizer = _Sanitizer()
    sanitizer.feed(rendered)
    sanitizer.close()
    return sanitizer.html()
