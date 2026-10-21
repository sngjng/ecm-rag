"""내부망의 다른 시스템이 인증 없이 호출하는 문서 질의응답 endpoint.

브라우저용 ``/chat``은 사내 로그인 세션을 요구하지만 이 router는 배치·업무 시스템
연동을 위해 의도적으로 세션/API key 의존성을 두지 않는다. 따라서 운영자는 애플리케이션
인증 대신 방화벽·reverse proxy ACL로 호출 가능한 사내 IP 대역을 반드시 제한해야 한다.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from service.answering import answer_documents
from service.schemas import ChatRequest
from service.stacktrace import analyze_stack_trace, build_stack_trace_query


router = APIRouter(prefix="/external", tags=["external"])

MAX_REQUEST_BYTES = 16 * 1024


def _parse_external_payload(body: bytes, content_type: str) -> dict:
    """JSON 객체·JSON 문자열·일반 문자열을 공통 ChatRequest로 정규화한다.

    ``ChatRequest``로 최종 검증하기 때문에 query 길이, limit 범위, asset_type 값은
    브라우저 API와 동일하다. 16 KiB 제한은 작은 질문 endpoint로 대용량 본문이 유입돼
    API worker 메모리를 점유하는 것을 막는 1차 보호 장치다.
    """
    if not body or not body.strip():
        raise HTTPException(422, "질문을 입력해 주세요")
    if len(body) > MAX_REQUEST_BYTES:
        raise HTTPException(413, "요청 본문은 16 KiB를 초과할 수 없습니다")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "요청 본문은 UTF-8이어야 합니다") from exc

    # charset 같은 부가 파라미터를 제거한 media type만 비교한다. Content-Type이 없으면
    # route에서 text/plain을 기본값으로 전달하므로 레거시 시스템도 문자열 호출이 가능하다.
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "JSON 형식이 올바르지 않습니다") from exc
        # JSON body가 객체가 아니라 "질문" 문자열인 연동 방식도 지원한다.
        if isinstance(payload, str):
            payload = {"query": payload}
        if not isinstance(payload, dict):
            raise HTTPException(422, "JSON 객체 또는 JSON 문자열을 입력해 주세요")
    else:
        payload = {"query": text.strip()}

    return payload


def parse_external_question(body: bytes, content_type: str) -> ChatRequest:
    """기존 일반 질의 호출 형식을 ``ChatRequest``로 변환한다."""
    payload = _parse_external_payload(body, content_type)
    # 아래 모델 검증을 통과한 뒤부터 내부 /chat과 같은 검색·LLM 경로를 사용한다.
    try:
        return ChatRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_url=False)) from exc


def prepare_external_answer(
    body: bytes, content_type: str
) -> tuple[ChatRequest, dict | None]:
    """외부 요청을 검색용 질문과 선택적인 스택트레이스 분석 결과로 변환한다."""
    payload = _parse_external_payload(body, content_type)
    trace = payload.pop("stack_trace", None)
    if trace is not None and not isinstance(trace, str):
        raise HTTPException(422, "stack_trace는 문자열이어야 합니다")
    candidate = trace or payload.get("query")
    analysis = analyze_stack_trace(candidate) if isinstance(candidate, str) else None
    if trace is not None:
        if not trace.strip():
            raise HTTPException(422, "스택트레이스를 입력해 주세요")
        # 호출자가 명시적으로 stack_trace 필드를 사용한 경우 프레임이 일부 잘린
        # 한 줄짜리 예외여도 분석 요청으로 취급한다.
        analysis["detected"] = True
    if analysis and analysis["detected"]:
        payload["query"] = build_stack_trace_query(
            candidate,
            analysis,
            payload.get("query") if trace is not None else None,
        )
    try:
        return ChatRequest.model_validate(payload), analysis
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_url=False)) from exc


@router.post("/answer")
async def answer(request: Request):
    """내부망 외부 시스템의 질문을 검색하고 로컬 LLM 답변과 출처를 반환한다.

    지원 입력은 ``application/json``의 ChatRequest 객체 또는 JSON 문자열, 그리고
    ``text/plain`` 일반 문자열이다. 별도 세션/API 인증 없이 호출할 수 있으며 접근
    통제는 내부망 방화벽과 서버 바인딩 정책에서 수행한다.
    """
    body = await request.body()
    content_type = request.headers.get("content-type", "text/plain")
    # JSON 객체는 stack_trace 전용 필드를 사용할 수 있다. 레거시 연동 시스템이 원문을
    # query 또는 text/plain으로 보내는 경우에도 동일하게 자동 판별한다.
    question, analysis = prepare_external_answer(body, content_type)

    response = answer_documents(question)
    if analysis and analysis["detected"]:
        response["request_type"] = "stack_trace"
        response["stack_trace_analysis"] = analysis
    else:
        response["request_type"] = "question"
    return response
