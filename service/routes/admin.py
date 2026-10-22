"""PostgreSQL 자산과 pgvector 청크를 관리하는 운영자 전용 HTTP API.

일반 사용자 화면보다 넓은 조회 범위(삭제 자료 포함)를 제공하므로 모든 endpoint에
공통 인증 dependency를 건다. 현재 임시 인증 비활성 모드에서는 내부망 ACL이 유일한
보호 계층이므로 외부에 공개해서는 안 된다.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from service.config import get_settings
from service.dependencies import authorize_admin
from service.embedding import embed
from service.repositories import admin as admin_repository
from service.repositories import assets, jobs
from service.schemas import AdminChunkCreate, AdminChunkUpdate, AssetCreate, AssetType, AssetUpdate


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(authorize_admin)])
JobStatus = Literal["queued", "processing", "completed", "failed", "cancelled"]


def _create_embedding(text: str) -> tuple[list[float], str]:
    """현재 profile의 임베딩 서버를 사용하고 운영 세부정보는 HTTP 응답에서 숨긴다."""
    try:
        vector = embed([text])[0]
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "임베딩 생성에 실패했습니다. 임베딩 서버와 모델 설정을 확인해 주세요.",
        ) from exc
    embedding_settings = get_settings().models.embedding
    if len(vector) != embedding_settings.dimension:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"임베딩 차원이 설정과 다릅니다(응답 {len(vector)}, 설정 {embedding_settings.dimension}).",
        )
    return vector, embedding_settings.model


@router.get("/stats")
def get_admin_stats():
    """관리 화면 상단의 자산·버전·청크·작업 상태 건수를 반환한다."""
    return admin_repository.get_stats()


@router.get("/assets")
def list_admin_assets(
    q: str | None = Query(default=None, max_length=300),
    asset_type: AssetType | None = None,
    include_deleted: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """관리 목적의 자산 목록과 버전·청크 건수를 조회한다."""
    return admin_repository.list_assets(
        query=q,
        asset_type=asset_type,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )


@router.post("/assets", status_code=status.HTTP_201_CREATED)
def create_admin_asset(payload: AssetCreate):
    """원본 파일 없이 RDB 자산 메타데이터를 생성한다."""
    return assets.create_asset(payload)


@router.patch("/assets/{asset_id}")
def update_admin_asset(asset_id: UUID, payload: AssetUpdate):
    """활성 자산의 전달된 메타데이터 필드만 수정한다."""
    result = assets.update_asset(asset_id, payload)
    if not result:
        raise HTTPException(404, "활성 자산을 찾을 수 없습니다")
    return result


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin_asset(asset_id: UUID):
    """감사 이력을 유지하기 위해 자산을 soft delete한다."""
    if not assets.soft_delete_asset(asset_id):
        raise HTTPException(404, "활성 자산을 찾을 수 없습니다")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/assets/{asset_id}/restore")
def restore_admin_asset(asset_id: UUID):
    """soft delete된 자산을 복구한다. 취소된 처리 작업은 별도로 재시도해야 한다."""
    result = admin_repository.restore_asset(asset_id)
    if not result:
        raise HTTPException(404, "삭제된 자산을 찾을 수 없습니다")
    return result


@router.get("/versions")
def list_admin_versions(
    asset_id: UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
):
    """수동 청크 등록에 사용할 버전 목록을 반환한다."""
    return {"items": admin_repository.list_versions(asset_id=asset_id, limit=limit)}


@router.get("/chunks")
def list_admin_chunks(
    q: str | None = Query(default=None, max_length=1000),
    asset_id: UUID | None = None,
    version_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """검색 청크를 조회하되 큰 raw vector 대신 차원과 모델명만 노출한다."""
    return admin_repository.list_chunks(
        query=q,
        asset_id=asset_id,
        version_id=version_id,
        limit=limit,
        offset=offset,
    )


@router.post("/chunks", status_code=status.HTTP_201_CREATED)
def create_admin_chunk(payload: AdminChunkCreate):
    """청크 본문을 저장하고 임베딩을 생성해 pgvector 컬럼에도 함께 기록한다."""
    values = payload.model_dump()
    embedding_text = (payload.embedding_content or payload.display_content).strip()
    values["embedding_content"] = embedding_text
    vector, model = _create_embedding(embedding_text)
    try:
        return admin_repository.create_chunk(values, vector, model)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (CheckViolation, ForeignKeyViolation, UniqueViolation) as exc:
        raise HTTPException(409, "청크를 등록할 수 없습니다. 버전과 입력값을 확인해 주세요.") from exc


@router.patch("/chunks/{chunk_id}")
def update_admin_chunk(chunk_id: str, payload: AdminChunkUpdate):
    """청크를 수정하고 검색 텍스트 변경 시 pgvector를 자동으로 다시 만든다."""
    current = admin_repository.get_chunk(chunk_id)
    if not current:
        raise HTTPException(404, "청크를 찾을 수 없습니다")

    changes = payload.model_dump(exclude_unset=True)
    # 화면에서 별도 임베딩용 문장을 입력하지 않은 채 표시 본문만 고치면 두 본문을
    # 동기화한다. 명시적으로 embedding_content를 보낸 경우에는 그 값을 우선한다.
    if "display_content" in changes and "embedding_content" not in changes:
        changes["embedding_content"] = changes["display_content"]
    if changes.get("embedding_content") is None and "embedding_content" in changes:
        changes["embedding_content"] = changes.get("display_content") or current["display_content"]

    vector = None
    model = None
    if "embedding_content" in changes:
        vector, model = _create_embedding(changes["embedding_content"].strip())
    result = admin_repository.update_chunk(chunk_id, changes, vector, model)
    if not result:
        raise HTTPException(404, "청크를 찾을 수 없습니다")
    return result


@router.delete("/chunks/{chunk_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_admin_chunk(chunk_id: str):
    """청크와 FK로 연결된 오류 이벤트·코드 심볼을 물리 삭제한다."""
    if not admin_repository.delete_chunk(chunk_id):
        raise HTTPException(404, "청크를 찾을 수 없습니다")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/jobs")
def list_admin_jobs(
    job_status: JobStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """문서 처리 작업과 최근 오류·진행 단계를 조회한다."""
    return admin_repository.list_jobs(job_status, limit, offset)


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_admin_job(job_id: UUID):
    """failed 또는 cancelled 작업의 시도 횟수를 초기화해 다시 대기열에 넣는다."""
    result = jobs.retry_job(job_id)
    if not result:
        raise HTTPException(409, "실패 또는 취소 상태의 작업만 재시도할 수 있습니다")
    return result
