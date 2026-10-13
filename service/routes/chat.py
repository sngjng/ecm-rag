"""검색 근거와 로컬 LLM을 결합하는 문서 질의응답 endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from service.answering import answer_documents
from service.dependencies import authorize
from service.schemas import ChatRequest


router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(authorize)])


@router.post("")
def chat(request: ChatRequest):
    """질문에 대한 근거를 검색한 뒤 LLM 답변과 검색 결과를 함께 반환한다.

    LLM이 일시적으로 사용할 수 없어도 검색 결과는 잃지 않고 반환한다. 화면은
    ``generation_status``를 이용해 답변 장애와 검색 성공을 구분할 수 있다.
    """
    return answer_documents(request)
