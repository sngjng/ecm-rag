"""OpenWebUI/내부 서비스가 호출할 PostgreSQL hybrid search endpoint."""
from fastapi import APIRouter, Depends
from service.dependencies import authorize
from service.schemas import SearchRequest
from service.search import search


router = APIRouter(prefix="/search", tags=["search"], dependencies=[Depends(authorize)])


@router.post("")
def query(request: SearchRequest):
    """검증된 검색 DTO를 hybrid retrieval 서비스에 전달한다.

    HTTP 계층은 파라미터 검증과 응답 형태만 책임지고 검색 lane 결합, DB 조회,
    reranking은 ``service.search.search``에서 수행한다.
    """
    return {"results": search(
        request.query, asset_type=request.asset_type, system_name=request.system_name,
        vendor=request.vendor, product=request.product,
        include_obsolete=request.include_obsolete, limit=request.limit,
    )}
