"""OpenWebUI/내부 서비스가 호출할 PostgreSQL hybrid search endpoint."""
from fastapi import APIRouter, Depends
from service.dependencies import authorize
from service.schemas import SearchRequest
from service.search import search


router = APIRouter(prefix="/search", tags=["search"], dependencies=[Depends(authorize)])


@router.post("")
def query(request: SearchRequest):
    return {"results": search(
        request.query, asset_type=request.asset_type, system_name=request.system_name,
        vendor=request.vendor, product=request.product,
        include_obsolete=request.include_obsolete, limit=request.limit,
    )}
