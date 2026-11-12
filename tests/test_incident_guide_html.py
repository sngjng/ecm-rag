"""로그분석 AI 화면의 Markdown 답변 렌더링 회귀 테스트."""

from pathlib import Path

HTML_PATH = Path(__file__).resolve().parents[1] / "web" / "incident_guide.html"


def test_incident_answer_uses_safe_markdown_renderer():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "function renderMarkdown(container, markdown)" in html
    assert 'role === "assistant" && status !== "thinking"' in html
    assert "renderMarkdown(content, text);" in html
    assert 'content.className = "message__text";' in html
    # 모델 응답을 innerHTML로 바로 넣으면 스크립트가 실행될 수 있으므로 금지한다.
    assert "content.innerHTML" not in html


def test_incident_markdown_has_document_styles():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert ".message__text--markdown h1" in html
    assert ".message__text--markdown h2" in html
    assert ".message__text--markdown ul" in html
    assert ".message__text--markdown pre" in html


def test_incident_markdown_normalizes_procedure_numbering_and_indentation():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'const list = document.createElement("ol");' in html
    assert 'const details = document.createElement("ul");' in html
    assert "if (details.childNodes.length) entry.append(details);" in html
    assert ".message__text--markdown li > ul" in html

