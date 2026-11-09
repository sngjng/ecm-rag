"""PostgreSQL/pgvector 검색 근거와 LLM을 결합하는 공통 질의응답 로직."""
from __future__ import annotations

import logging
import re
from typing import Any

from service.config import get_settings
from service.document_diagram import build_document_diagram, graph_context_text
from service.llm import LlmGenerationError, generate_answer
from service.schemas import ChatRequest
from service.search import search
from service.repositories.knowledge_graph import fetch_subgraph


logger = logging.getLogger(__name__)


DECOMPILED_LINE_NOTICE = (
    "참고: 답변 근거에 JAR 디컴파일 소스가 포함되어 있어 스택트레이스의 원본 소스 "
    "라인 번호와 표시된 라인 범위가 다를 수 있습니다. 패키지·클래스·메서드를 기준으로 "
    "확인해 주세요."
)

# 벡터 검색은 항상 가장 가까운 청크를 반환할 수 있으므로 검색 결과가 존재한다는 사실만으로
# 질문과 문서가 관련 있다고 단정할 수 없다. LLM이 근거 부족을 명시한 답변에는 해당 문서의
# GraphRAG 관계도를 붙이지 않아, 무관한 문서 구성도가 답변 근거처럼 보이는 것을 막는다.
INSUFFICIENT_EVIDENCE_PATTERNS = (
    r"확인할 수 없",
    r"찾을 수 없",
    r"포함(?:되어|돼) 있지 않",
    r"포함되지 않",
    r"관련 (?:내용|정보|문서).{0,20}(?:없|부족)",
    r"(?:문서|근거|정보).{0,20}부족",
    r"does not contain",
    r"not found in (?:the )?(?:documents|sources)",
    r"insufficient (?:evidence|context)",
)


def _answer_indicates_insufficient_evidence(answer: str) -> bool:
    """LLM 답변이 등록 문서에서 근거를 찾지 못했다고 명시했는지 판별한다."""
    normalized = " ".join(str(answer or "").split()).lower()
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in INSUFFICIENT_EVIDENCE_PATTERNS)


def _uses_decompiled_source(results: list[dict[str, Any]]) -> bool:
    """검색 근거 중 CFR로 복원한 JAR 소스가 포함됐는지 확인한다."""
    for result in results:
        metadata = result.get("metadata")
        if isinstance(metadata, dict) and metadata.get("decompiled") is True:
            return True
        # 과거 데이터에 metadata 표시가 없더라도 저장 kind로 안전하게 판별한다.
        if str(result.get("kind") or "").startswith("decompiled_code"):
            return True
    return False


def answer_documents(request: ChatRequest) -> dict[str, Any]:
    """질문을 검색한 뒤 근거 기반 LLM 답변과 출처를 함께 반환한다.

    브라우저 채팅과 외부 연동 API가 완전히 같은 검색·답변 정책을 사용하도록 HTTP
    router에서 분리했다. LLM 장애가 발생해도 PostgreSQL 검색 결과는 보존한다.
    """
    settings = get_settings()
    llm_settings = settings.models.llm
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
        preferred_asset_ids=request.preferred_asset_ids,
    )
    if not results:
        return {
            "answer": (
                "등록된 문서에서 질문과 관련된 내용을 찾지 못했습니다. "
                "본 서비스는 등록 문서와 관련된 질문에만 답변할 수 있습니다."
            ),
            "generation_status": "out_of_scope",
            "evidence_status": "insufficient",
            "notices": [],
            "results": [],
            "diagram": None,
            "preferred_asset_ids_applied": [
                str(asset_id) for asset_id in request.preferred_asset_ids
            ],
        }
    graph = {"entities": [], "relations": []}
    if settings.graph_rag.enabled:
        graph = fetch_subgraph(
            [str(result["chunk_id"]) for result in results if result.get("chunk_id")],
            max_entities=settings.graph_rag.query_max_entities,
            max_relations=settings.graph_rag.query_max_relations,
        )
        relation_context = graph_context_text(graph)
        if relation_context:
            # 동일 관계를 모든 청크에 반복하지 않고 최상위 근거에 한 번만 추가한다.
            results[0].setdefault("neighbor_context", []).append(relation_context)
    # 검색 성공과 LLM 장애를 분리한다. LLM만 실패하면 HTTP 오류로 근거까지 버리지 않고
    # generation_status=unavailable과 검색 결과를 반환해 호출 시스템이 대체 표시할 수 있다.
    try:
        answer = generate_answer(request.query, results, request.history)
        generation_status = "completed"
    except LlmGenerationError as exc:
        # provider 오설정이나 prompt 예산 검증처럼 HTTP 요청 전에 발생한 실패도 API
        # 로그에 남긴다. 기존에는 사용자 fallback만 반환되어 원인 확인이 어려웠다.
        logger.warning("LLM answer unavailable: %s", str(exc))
        answer = (
            "관련 문서는 찾았지만 현재 LLM 답변 서버를 사용할 수 없습니다. "
            "아래 검색 결과에서 원문 내용을 확인해 주세요."
        )
        generation_status = "unavailable"
    evidence_sufficient = (
        generation_status == "completed"
        and not _answer_indicates_insufficient_evidence(answer)
    )
    diagram = None
    if (
        request.include_diagram
        and evidence_sufficient
    ):
        diagram = build_document_diagram(graph)
    notices = []
    if _uses_decompiled_source(results):
        notices.append(DECOMPILED_LINE_NOTICE)
        # 브라우저와 단순 연동 시스템이 notices 필드를 별도로 표시하지 않아도 사용자가
        # 라인 번호의 한계를 놓치지 않도록 답변 본문에도 같은 안내를 붙인다.
        answer = f"{answer}\n\n{DECOMPILED_LINE_NOTICE}"
    return {
        "answer": answer,
        "generation_status": generation_status,
        "evidence_status": "sufficient" if evidence_sufficient else "insufficient",
        "notices": notices,
        "results": results,
        "diagram": diagram,
        "preferred_asset_ids_applied": [
            str(asset_id) for asset_id in request.preferred_asset_ids
        ],
    }
