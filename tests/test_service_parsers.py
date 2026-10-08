"""문서 외 자산 유형의 구조화/검색 표현 회귀 테스트."""
from service.parsers import parse_code, parse_incident, parse_logs


def test_incident_fields_are_preserved_and_added_to_embedding_text(tmp_path):
    source = tmp_path / "incident.md"
    source.write_text(
        "장애번호: INC-20261007-001\n시스템: 계약계\n제품: WebSphere 9\n"
        "증상: OutOfMemoryError\n원인: 세션 객체 과다\n조치: timeout 축소\n결과: 정상화",
        encoding="utf-8",
    )
    items, report = parse_incident(source)
    assert report["structured_incidents"] == 1
    assert items[0].metadata["incident"]["cause"] == "세션 객체 과다"
    assert "[장애]" in items[0].embedding_text
    assert "조치: timeout 축소" in items[0].embedding_text


def test_non_ast_code_is_not_duplicated(tmp_path):
    source = tmp_path / "schema.sql"
    source.write_text("SELECT 1;\n" * 150, encoding="utf-8")
    items, report = parse_code(source, "repository", "main", "sql/schema.sql")
    assert report["symbols"] == 0
    assert len(items) == 2
    assert items[0].line_start == 1
    assert items[1].line_start == 121


def test_stack_trace_extracts_exact_fields(tmp_path):
    source = tmp_path / "error.log"
    source.write_text(
        "2026-10-07 10:20:30 ERROR SRVE0255E java.lang.NullPointerException\n"
        "  at com.company.payment.PaymentService.pay(PaymentService.java:143)\n"
        "Caused by: java.sql.SQLException: Connection reset\n",
        encoding="utf-8",
    )
    items, _ = parse_logs(source)
    assert items[0].identifier == "SRVE0255E"
    assert items[0].error["severity"] == "ERROR"
    assert items[0].error["frames"][0]["line_number"] == 143
    assert items[0].error["root_cause"].startswith("Caused by:")
