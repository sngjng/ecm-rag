"""PostgreSQL/pgvector 검색 근거와 LLM을 결합하는 공통 질의응답 로직."""
from __future__ import annotations

from typing import Any

from service.config import get_settings
from service.llm import LlmGenerationError, generate_answer
from service.schemas import ChatRequest
from service.search import search


def answer_documents(request: ChatRequest) -> dict[str, Any]:
    """질문을 검색한 뒤 근거 기반 LLM 답변과 출처를 함께 반환한다.

    브라우저 채팅과 외부 연동 API가 완전히 같은 검색·답변 정책을 사용하도록 HTTP
    router에서 분리했다. LLM 장애가 발생해도 PostgreSQL 검색 결과는 보존한다.
    """
    llm_settings = get_settings().models.llm
    # LLM에 전달할 검색 근거 수를 모델 컨텍스트 정책보다 크게 허용하지 않는다. 외부
    # 호출자가 큰 limit를 보내도 context_top_k가 최종 상한으로 동작한다.
    limit = request.limit or llm_settings.context_top_k
    # search() 내부에서 질문 embedding 생성, PostgreSQL exact/FTS/pgvector 검색,
    # RRF 결합과 선택적 reranking이 순서대로 수행된다.
    results = search(
        request.query,
        asset_type=request.asset_type,
        system_name=request.system_name,
        vendor=request.vendor,
        product=request.product,
        include_obsolete=request.include_obsolete,
        limit=min(limit, llm_settings.context_top_k),
    )
    if not results:
        return {
            "answer": "질문과 관련된 문서를 찾지 못했습니다. 다른 표현으로 다시 질문해 주세요.",
            "generation_status": "no_context",
            "results": [],
        }
    # 검색 성공과 LLM 장애를 분리한다. LLM만 실패하면 HTTP 오류로 근거까지 버리지 않고
    # generation_status=unavailable과 검색 결과를 반환해 호출 시스템이 대체 표시할 수 있다.
    try:
        answer = generate_answer(request.query, results, request.history)
        generation_status = "completed"
    except LlmGenerationError:
        answer = (
            "관련 문서는 찾았지만 현재 LLM 답변 서버를 사용할 수 없습니다. "
            "아래 검색 결과에서 원문 내용을 확인해 주세요."
        )
        generation_status = "unavailable"
    return {
        "answer": answer,
        "generation_status": generation_status,
        "results": results,
    }
