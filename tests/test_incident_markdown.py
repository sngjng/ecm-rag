"""로그 분석 API가 원문과 안전하게 렌더링한 답변을 함께 반환하는지 확인한다."""
from service.routes import incident_guide
from service.schemas import IncidentGuideRequest


def test_analysis_returns_formatted_answer_without_changing_original(monkeypatch):
    answer = (
        "## 원인\n\n**DB 오류**\n\n- 연결 확인\n\n"
        "```sql\nSELECT 1;\n```\n\n"
        "| 항목 | 값 |\n| --- | --- |\n| 상태 | 오류 |\n\n"
        '<script>alert(1)</script>\n\n[위험](javascript:alert(1))'
    )
    monkeypatch.setattr(incident_guide.incident_repository, "get_template",
                        lambda: {"content": "template"})
    monkeypatch.setattr(incident_guide, "generate_incident_guide",
                        lambda payload, template: {"answer": answer, "generation_status": "completed"})
    result = incident_guide.analyze_incident(IncidentGuideRequest(question="원인 분석"), {})
    assert result["answer"] == answer
    rendered = result["answer_html"]
    assert "<h2>원인</h2>" in rendered
    assert "<strong>DB 오류</strong>" in rendered
    assert "<ul>" in rendered
    assert "<pre><code>" in rendered
    assert "<table>" in rendered
    assert "<script>" not in rendered
    assert "javascript:" not in rendered
