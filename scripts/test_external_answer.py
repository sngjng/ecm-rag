"""실행 중인 ``POST /api/v1/external/answer`` 계약을 점검하는 독립 스크립트.

추가 pip 패키지 없이 Python 3.11 표준 라이브러리만 사용한다. 정상 질문은 DB·임베딩·
LLM까지 실제 호출하고, 잘못된 요청은 FastAPI 입력 검증 상태 코드를 확인한다. 인증
헤더나 세션 쿠키를 의도적으로 보내지 않아 내부 시스템의 무인증 연동 조건도 검증한다.

사용 예:
    python scripts/test_external_answer.py --base-url http://127.0.0.1:8300
    python scripts/test_external_answer.py --base-url http://10.0.0.20:8300 \
        --query "A서버 설정 관련 문서를 찾아줘" --expect-results
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


ResponseValidator = Callable[[dict[str, Any]], tuple[bool, str]]


@dataclass(frozen=True)
class Case:
    """한 건의 HTTP 요청과 기대 결과를 나타낸다."""

    name: str
    description: str
    content_type: str
    body: bytes
    expected_status: int
    validator: ResponseValidator | None = None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _validate_answer(
    payload: dict[str, Any], *, request_type: str, include_chunks: bool = True,
    expect_results: bool = False,
) -> tuple[bool, str]:
    """정상 답변의 공통 필드와 타입을 검사한다."""
    required = {"answer", "generation_status", "request_type", "chunks_included"}
    missing = sorted(required - payload.keys())
    if missing:
        return False, "필수 응답 필드 누락: " + ", ".join(missing)
    if payload["request_type"] != request_type:
        return False, f"request_type={payload['request_type']!r}, 예상={request_type!r}"
    if payload["generation_status"] not in {"completed", "unavailable", "out_of_scope"}:
        return False, f"알 수 없는 generation_status={payload['generation_status']!r}"
    if not isinstance(payload["answer"], str):
        return False, "answer는 문자열이어야 합니다"
    if payload["chunks_included"] is not include_chunks:
        return False, f"chunks_included={payload['chunks_included']!r}, 예상={include_chunks!r}"
    if include_chunks and "results" not in payload:
        return False, "include_chunks=true 응답에 results가 없습니다"
    if not include_chunks and "results" in payload:
        return False, "include_chunks=false 응답에 results가 포함됐습니다"
    results = payload.get("results", [])
    if include_chunks and not isinstance(results, list):
        return False, "results는 배열이어야 합니다"
    if expect_results and not results:
        return False, "--expect-results를 지정했지만 검색 결과가 없습니다"
    return True, (
        f"type={request_type}, generation={payload['generation_status']}, "
        f"chunks_included={include_chunks}, results={len(results)}"
    )


def _question_validator(expect_results: bool) -> ResponseValidator:
    return lambda payload: _validate_answer(
        payload, request_type="question", expect_results=expect_results
    )


def _answer_only_validator(payload: dict[str, Any]) -> tuple[bool, str]:
    return _validate_answer(
        payload, request_type="question", include_chunks=False
    )


def _stack_validator(payload: dict[str, Any]) -> tuple[bool, str]:
    valid, detail = _validate_answer(payload, request_type="stack_trace")
    if not valid:
        return valid, detail
    analysis = payload.get("stack_trace_analysis")
    if not isinstance(analysis, dict):
        return False, "stack_trace_analysis 객체가 없습니다"
    required = {
        "fingerprint",
        "primary_exception",
        "root_cause",
        "error_codes",
        "frames",
        "application_frames",
    }
    missing = sorted(required - analysis.keys())
    if missing:
        return False, "스택 분석 필드 누락: " + ", ".join(missing)
    return True, (
        f"{detail}, exception={analysis.get('primary_exception')}, "
        f"codes={analysis.get('error_codes')}, frames={analysis.get('frame_count')}"
    )


def build_cases(query: str, expect_results: bool) -> list[Case]:
    """외부 연동에서 지원하거나 거부해야 할 대표 입력을 만든다."""
    trace = (
        "java.lang.RuntimeException: DB query failed APP1001\n"
        "  at com.company.asset.AssetService.load(AssetService.java:143)\n"
        "  at org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:933)\n"
        "Caused by: java.sql.SQLSyntaxErrorException: ORA-00942: table or view does not exist\n"
        "  at com.company.asset.AssetRepository.find(AssetRepository.java:81)"
    )
    auto_trace = (
        "java.lang.IllegalStateException: response already committed\n"
        "  at com.company.gateway.ResponseWriter.write(ResponseWriter.java:107)\n"
        "  at com.company.gateway.GatewayService.send(GatewayService.java:63)"
    )
    return [
        Case(
            "JSON_OBJECT",
            "JSON 객체 질문과 청크 포함 응답",
            "application/json; charset=utf-8",
            _json_bytes({"query": query, "limit": 5, "include_chunks": True}),
            200,
            _question_validator(expect_results),
        ),
        Case(
            "ANSWER_ONLY",
            "LLM 답변만 반환하고 검색 청크 제외",
            "application/json; charset=utf-8",
            _json_bytes({"query": query, "limit": 5, "include_chunks": False}),
            200,
            _answer_only_validator,
        ),
        Case(
            "JSON_STRING",
            "JSON 문자열을 질문으로 변환",
            "application/json",
            _json_bytes(query),
            200,
            _question_validator(False),
        ),
        Case(
            "TEXT_PLAIN",
            "일반 UTF-8 문자열 질문",
            "text/plain; charset=utf-8",
            query.encode("utf-8"),
            200,
            _question_validator(False),
        ),
        Case(
            "STACK_TRACE_FIELD",
            "stack_trace 필드의 예외·오류코드·프레임 구조화",
            "application/json",
            _json_bytes(
                {
                    "query": "원인과 관련 소스 및 조치 방법을 알려줘",
                    "stack_trace": trace,
                    "limit": 5,
                }
            ),
            200,
            _stack_validator,
        ),
        Case(
            "STACK_TRACE_AUTO",
            "text/plain 스택트레이스 자동 판별",
            "text/plain; charset=utf-8",
            auto_trace.encode("utf-8"),
            200,
            _stack_validator,
        ),
        Case(
            "EMPTY_BODY",
            "빈 요청 거부",
            "text/plain",
            b"",
            422,
        ),
        Case(
            "MALFORMED_JSON",
            "손상된 JSON 거부",
            "application/json",
            b'{"query":',
            400,
        ),
        Case(
            "JSON_ARRAY",
            "객체·문자열 이외 JSON 거부",
            "application/json",
            b'["question"]',
            422,
        ),
        Case(
            "INVALID_STACK_TYPE",
            "문자열이 아닌 stack_trace 거부",
            "application/json",
            _json_bytes({"query": "오류 분석", "stack_trace": ["invalid"]}),
            422,
        ),
        Case(
            "INVALID_LIMIT",
            "허용 범위를 넘은 limit 거부",
            "application/json",
            _json_bytes({"query": query, "limit": 999}),
            422,
        ),
        Case(
            "BODY_TOO_LARGE",
            "16 KiB 초과 본문 거부",
            "text/plain; charset=utf-8",
            b"x" * (16 * 1024 + 1),
            413,
        ),
    ]


def request_case(endpoint: str, case: Case, timeout: float) -> tuple[int, bytes]:
    """HTTP 오류 응답도 본문과 상태를 정상적인 테스트 결과로 반환한다."""
    request = urllib.request.Request(
        endpoint,
        data=case.body,
        method="POST",
        headers={"Content-Type": case.content_type, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def execute_case(endpoint: str, case: Case, timeout: float) -> tuple[bool, str]:
    try:
        status, body = request_case(endpoint, case, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"연결 실패: {exc}"
    if status != case.expected_status:
        preview = body.decode("utf-8", errors="replace")[:500]
        return False, f"HTTP {status}, 예상 {case.expected_status}, body={preview}"
    if case.validator is None:
        return True, f"HTTP {status}"
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, f"JSON 응답 해석 실패: {exc}"
    if not isinstance(payload, dict):
        return False, "응답 최상위 값이 JSON 객체가 아닙니다"
    return case.validator(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ECM-RAG 외부 질의응답 endpoint 실호출 테스트"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("ECM_RAG_BASE_URL", "http://127.0.0.1:8300"),
        help="FastAPI base URL (기본값: %(default)s)",
    )
    parser.add_argument(
        "--query",
        default="업로드한 문서 중 A서버 설정 관련 문서를 찾아줘",
        help="정상 질문 케이스에 사용할 문장",
    )
    parser.add_argument(
        "--expect-results",
        action="store_true",
        help="JSON 객체 질문에서 검색 결과가 한 건 이상이어야 성공으로 판정",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="요청 한 건의 제한 시간(초)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="네트워크 호출 없이 테스트 케이스 목록만 표시",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    endpoint = args.base_url.rstrip("/") + "/api/v1/external/answer"
    cases = build_cases(args.query, args.expect_results)
    if args.list:
        for index, case in enumerate(cases, 1):
            print(f"{index:02d}. {case.name:<20} HTTP {case.expected_status} - {case.description}")
        return 0

    print(f"Endpoint: {endpoint}")
    print("인증 헤더/세션: 사용하지 않음")
    print("-" * 88)
    passed = 0
    for index, case in enumerate(cases, 1):
        success, detail = execute_case(endpoint, case, args.timeout)
        marker = "PASS" if success else "FAIL"
        print(f"[{marker}] {index:02d} {case.name}: {case.description}")
        print(f"       {detail}")
        passed += int(success)
    print("-" * 88)
    print(f"결과: {passed}/{len(cases)} 성공")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
