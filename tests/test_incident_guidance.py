"""장애 로그 축약·검색 질의·템플릿 기반 응답의 네트워크 비의존 테스트."""
from types import SimpleNamespace

import service.incident_guidance as guidance_module
from service.incident_guidance import (
    _coerce_answer_to_template,
    _missing_template_headings,
    generate_incident_guide,
)
from service.log_guidance import analyze_log_input, build_log_search_query
from service.schemas import IncidentGuideRequest

SAMPLE_LOG = """2026-10-28 10:00:00 INFO request started
2026-10-28 10:00:01 ERROR payment failed ORA-00942
java.sql.SQLSyntaxErrorException: ORA-00942: table or view does not exist
    at com.company.payment.PaymentRepository.load(PaymentRepository.java:42)
    at com.company.payment.PaymentService.run(PaymentService.java:18)
Caused by: java.sql.SQLException: ORA-00942
    at oracle.jdbc.driver.T4CTTIoer.processError(T4CTTIoer.java:494)
2026-10-28 10:00:02 INFO request finished
"""


def _settings(context_top_k: int = 8, log_context_characters: int = 16000):
    return SimpleNamespace(
        models=SimpleNamespace(llm=SimpleNamespace(context_top_k=context_top_k)),
        incident_guidance=SimpleNamespace(
            max_log_context_characters=log_context_characters
        ),
    )


def test_analyze_log_input_extracts_error_codes_frames_and_context():
    analysis, excerpt = analyze_log_input(SAMPLE_LOG)

    assert "ORA-00942" in analysis["error_codes"]
    assert "java.sql.SQLSyntaxErrorException" in analysis["exception_classes"]
    assert analysis["levels"]["ERROR"] == 1
    assert analysis["first_timestamp"] == "2026-10-28 10:00:00"
    assert analysis["last_timestamp"] == "2026-10-28 10:00:02"
    assert analysis["error_occurrences"][0]["timestamp"] == "2026-10-28 10:00:01"
    assert "ORA-00942" in analysis["error_occurrences"][0]["message"]
    assert analysis["application_frames"][0]["method_name"] == "load"
    assert "payment failed" in excerpt

    query = build_log_search_query("원인과 조치 방법을 알려줘", analysis)
    assert "원인과 조치 방법" in query
    assert "ORA-00942" in query
    assert "PaymentRepository.load" in query
    assert len(query) <= 2000


def test_log_context_budget_limits_selected_excerpt():
    repeated_log = "\n".join(
        f"2026-10-28 ERROR ORA-00942 failure line {index} " + ("x" * 100)
        for index in range(100)
    )

    analysis, excerpt = analyze_log_input(
        repeated_log,
        max_context_characters=1000,
    )

    assert len(excerpt) <= 1000
    assert analysis["selected_characters"] <= 1000
    assert analysis["input_truncated_for_llm"] is True


def test_large_log_excerpt_samples_errors_through_the_end_of_input():
    repeated_log = "\n".join(
        f"2026-10-28 10:{index // 60:02d}:{index % 60:02d} ERROR CASE-{index:04d} failure"
        for index in range(500)
    )

    analysis, excerpt = analyze_log_input(
        repeated_log,
        max_context_characters=16000,
    )

    assert "CASE-0000" in excerpt
    assert "CASE-0499" in excerpt
    assert analysis["error_line_count"] == 500


def test_generate_incident_guide_uses_hidden_search_evidence(monkeypatch):
    captured = {}
    search_result = {
        "title": "DB 장애 처리",
        "source_filename": "db-guide.md",
        "version_label": "1",
        "content": "ORA-00942는 객체 존재 여부와 권한을 확인한다.",
        "neighbor_context": [],
        "identifier": "load",
        "metadata": {"class_name": "PaymentRepository"},
        "kind": "text",
    }
    monkeypatch.setattr(guidance_module, "search", lambda *args, **kwargs: [search_result])
    monkeypatch.setattr(
        guidance_module,
        "get_settings",
        _settings,
    )

    def fake_generate(query, results, history, *, system_prompt):
        captured.update(
            query=query,
            results=results,
            history=history,
            system_prompt=system_prompt,
        )
        return (
            "# 장애 해결 가이드\n권한과 객체를 확인하세요.\n\n"
            "## 원인\n객체 또는 권한을 확인합니다.\n\n"
            "## 조치\nDBA 승인 후 조치합니다."
        )

    monkeypatch.setattr(guidance_module, "generate_answer", fake_generate)
    request = IncidentGuideRequest(log_text=SAMPLE_LOG, question="어떻게 해결해?")

    response = generate_incident_guide(request, "# 장애 해결 가이드\n## 원인\n## 조치")

    assert response["generation_status"] == "completed"
    assert "results" not in response
    assert response["analysis"]["error_codes"] == ["ORA-00942"]
    assert captured["results"][0]["title"] == "사용자 입력 장애 로그"
    assert captured["results"][1] == search_result
    assert "관리 템플릿" in captured["system_prompt"]
    assert "타임스탬프" in captured["system_prompt"]
    assert "어떻게 해결해?" in captured["query"]
    assert "내부 RAG 교차 검증 근거: 1건" in captured["query"]
    method_nodes = response["diagram"]["nodes"][:-1]
    assert method_nodes[0]["value"].startswith("PaymentService.run()")
    assert method_nodes[-1]["value"].startswith("PaymentRepository.load()")
    assert method_nodes[-1]["label"] == "오류 발생 메서드"
    assert method_nodes[-1]["rag_matched"] is True
    assert response["diagram"]["nodes"][-1]["label"] == "예외 전파"


def test_natural_language_question_without_log_returns_out_of_scope(monkeypatch):
    monkeypatch.setattr(guidance_module, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        guidance_module,
        "get_settings",
        _settings,
    )
    request = IncidentGuideRequest(question="A서버 장애 조치 방법을 알려줘")

    response = generate_incident_guide(request, "# 템플릿")

    assert response["generation_status"] == "out_of_scope"
    assert "results" not in response


def test_natural_language_question_without_log_uses_search_and_template(monkeypatch):
    search_result = {
        "title": "A서버 운영 지침",
        "content": "A서버 연결 장애 시 방화벽과 포트를 확인한다.",
        "metadata": {},
        "kind": "text",
    }
    captured = {}
    monkeypatch.setattr(guidance_module, "search", lambda *args, **kwargs: [search_result])
    monkeypatch.setattr(
        guidance_module,
        "get_settings",
        _settings,
    )

    def fake_generate(query, results, history, *, system_prompt):
        captured.update(query=query, results=results, system_prompt=system_prompt)
        return "# 답변\nA서버의 방화벽과 포트를 확인하세요.\n\n## 확인 절차\n- 연결 시험"

    monkeypatch.setattr(guidance_module, "generate_answer", fake_generate)
    request = IncidentGuideRequest(question="A서버 연결 장애는 무엇을 확인해야 해?")

    response = generate_incident_guide(request, "# 답변\n\n## 확인 절차")

    assert response["generation_status"] == "completed"
    assert response["answer"].startswith("# 답변")
    assert captured["results"] == [search_result]
    assert "A서버 연결 장애" in captured["query"]
    assert response["analysis"]["total_lines"] == 0
    assert "results" not in response


def test_template_headings_are_validated_and_server_can_restore_structure():
    template = "# 장애 분석\n\n## 원인\n\n## 조치\n\n## 추가 정보"
    malformed = "# 장애 분석\nORA 오류입니다.\n\n## 조치\n권한을 확인합니다."

    assert _missing_template_headings(template, malformed) == ["## 원인", "## 추가 정보"]

    restored = _coerce_answer_to_template(template, malformed)
    assert _missing_template_headings(template, restored) == []
    assert restored.index("## 원인") < restored.index("## 조치")
    assert "## 추가 정보\n- 추가 확인 필요" in restored
