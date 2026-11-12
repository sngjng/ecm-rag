"""화면에 표시된 로그분석 AI 가이드 답변을 Word 문서로 내보낸다."""
from __future__ import annotations

import io
import re
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BODY_FONT = "Malgun Gothic"
TEXT = RGBColor(0, 0, 0)


def _set_run_font(
    run: Any,
    *,
    size: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
) -> None:
    """한글 글꼴이 Word에서 대체되지 않도록 글꼴 정보를 함께 지정한다."""
    run.font.name = BODY_FONT
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), BODY_FONT)
    run.font.color.rgb = TEXT
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def _shade_paragraph(paragraph: Any, fill: str) -> None:
    properties = paragraph._p.get_or_add_pPr()
    shading = properties.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        properties.append(shading)
    shading.set(qn("w:fill"), fill)


def _add_inline_markdown(paragraph: Any, text: str, *, size: float = 10.5) -> None:
    """문구를 바꾸지 않고 굵게·기울임·인라인 코드를 Word 서식으로 옮긴다."""
    pattern = re.compile(r"(\*\*.+?\*\*|`[^`]+`|(?<!\*)\*[^*]+\*(?!\*))")
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            run = paragraph.add_run(text[position : match.start()])
            _set_run_font(run, size=size)
        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            _set_run_font(run, size=size, bold=True)
        elif token.startswith("`"):
            run = paragraph.add_run(token[1:-1])
            _set_run_font(run, size=size)
            run.font.name = "Consolas"
            run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Consolas")
        else:
            run = paragraph.add_run(token[1:-1])
            _set_run_font(run, size=size, italic=True)
        position = match.end()
    if position < len(text):
        run = paragraph.add_run(text[position:])
        _set_run_font(run, size=size)


def _add_heading(document: Document, text: str, level: int) -> None:
    word_level = min(max(level, 1), 3)
    paragraph = document.add_paragraph(style=f"Heading {word_level}")
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(12 if word_level == 1 else 8)
    paragraph.paragraph_format.space_after = Pt(5)
    _add_inline_markdown(
        paragraph,
        text,
        size={1: 16, 2: 13, 3: 11.5}[word_level],
    )
    for run in paragraph.runs:
        run.bold = True


def _add_numbered_step(document: Document, text: str, number: int) -> None:
    """모델이 모든 항목을 1로 작성해도 Word에는 연속 번호로 표시한다."""
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(0.75)
    paragraph.paragraph_format.first_line_indent = Cm(-0.45)
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.paragraph_format.space_after = Pt(3)
    paragraph.paragraph_format.line_spacing = 1.3
    prefix = paragraph.add_run(f"{number}. ")
    _set_run_font(prefix, size=10.5, bold=True)
    _add_inline_markdown(paragraph, text)


def _add_bullet_item(document: Document, text: str, *, nested: bool) -> None:
    """번호 절차의 상세 항목은 한 단계 더 들여써 계층을 명확히 한다."""
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(1.3 if nested else 0.75)
    paragraph.paragraph_format.first_line_indent = Cm(-0.35)
    paragraph.paragraph_format.space_after = Pt(3)
    paragraph.paragraph_format.line_spacing = 1.3
    marker = paragraph.add_run("• ")
    _set_run_font(marker, size=10.5)
    _add_inline_markdown(paragraph, text)


def _add_answer(document: Document, answer: str) -> None:
    """화면의 Markdown 가이드를 같은 순서와 문구로 Word 요소로 변환한다."""
    in_code = False
    code_lines: list[str] = []
    procedure_number = 0
    in_numbered_procedure = False

    def flush_code() -> None:
        if not code_lines:
            return
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.left_indent = Cm(0.4)
        paragraph.paragraph_format.right_indent = Cm(0.2)
        paragraph.paragraph_format.space_before = Pt(3)
        paragraph.paragraph_format.space_after = Pt(7)
        paragraph.paragraph_format.line_spacing = 1.15
        _shade_paragraph(paragraph, "F2F2F2")
        run = paragraph.add_run("\n".join(code_lines))
        _set_run_font(run, size=9)
        run.font.name = "Consolas"
        run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Consolas")
        code_lines.clear()

    for raw_line in answer.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                flush_code()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if heading:
            procedure_number = 0
            in_numbered_procedure = False
            _add_heading(document, heading.group(2), len(heading.group(1)))
            continue

        bullet = re.match(r"^\s*[-*+]\s+(.+)$", line)
        numbered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if numbered:
            procedure_number += 1
            in_numbered_procedure = True
            _add_numbered_step(document, numbered.group(1), procedure_number)
            continue
        if bullet:
            _add_bullet_item(
                document,
                bullet.group(1),
                nested=in_numbered_procedure,
            )
            continue

        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            procedure_number = 0
            in_numbered_procedure = False
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(5)
            _shade_paragraph(paragraph, "D9D9D9")
            continue

        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(4 if line else 2)
        paragraph.paragraph_format.line_spacing = 1.35
        if line:
            _add_inline_markdown(paragraph, line)
            # 일반 본문이 시작되면 이후 불릿은 새로운 최상위 목록으로 처리한다.
            procedure_number = 0
            in_numbered_procedure = False

    flush_code()


def build_incident_report(*, answer: str) -> bytes:
    """화면의 가이드 답변만 포함한 편집 가능한 DOCX 바이트를 반환한다."""
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)

    normal = document.styles["Normal"]
    normal.font.name = BODY_FONT
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.font.size = Pt(10.5)

    _add_answer(document, answer)

    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()
