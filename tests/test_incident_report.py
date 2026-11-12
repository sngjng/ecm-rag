"""로그분석 AI 가이드 답변 Word 내보내기 테스트."""
import re
from io import BytesIO

from docx import Document

from service.incident_report import build_incident_report


def test_build_incident_report_exports_only_dynamic_guide_content():
    content = build_incident_report(
        answer=(
            "# 로그 분석 및 조치 가이드\n"
            "## 1 핵심 장애 요약\n"
            "TCP 연결 중 Broken pipe 오류가 반복되었습니다.\n"
            "## 2 추정 원인\n"
            "- 상대 서버 연결 종료 가능성을 확인합니다.\n"
            "## 3 조치 방안\n"
            "1. 네트워크 연결 상태를 확인합니다."
        ),
    )

    assert content.startswith(b"PK")
    document = Document(BytesIO(content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "로그 분석 및 조치 가이드" in text
    assert "TCP 연결 중 Broken pipe 오류가 반복되었습니다." in text
    assert "상대 서버 연결 종료 가능성을 확인합니다." in text
    assert "네트워크 연결 상태를 확인합니다." in text
    assert not document.tables


def test_word_report_normalizes_numbering_and_indents_step_details():
    content = build_incident_report(
        answer=(
            "## 4. 확인 절차\n"
            "1. **네트워크 연결 상태 확인**\n"
            "- TCP 연결 상태를 확인합니다.\n"
            "1. **서버 측 로그 분석**\n"
            "- 강제 종료 여부를 확인합니다.\n"
            "1. **예외 처리 로직 검토**\n"
            "- Broken pipe 처리 여부를 확인합니다."
        ),
    )

    document = Document(BytesIO(content))
    paragraphs = [paragraph for paragraph in document.paragraphs if paragraph.text]
    numbered = [
        paragraph
        for paragraph in paragraphs
        if not paragraph.style.name.startswith("Heading")
        and re.match(r"^\d+\. ", paragraph.text)
    ]
    bullets = [paragraph for paragraph in paragraphs if paragraph.text.startswith("• ")]

    assert [paragraph.text.split(" ", 1)[0] for paragraph in numbered] == [
        "1.",
        "2.",
        "3.",
    ]
    assert len(bullets) == 3
    assert all(
        bullet.paragraph_format.left_indent > numbered[0].paragraph_format.left_indent
        for bullet in bullets
    )
