"""ECM Asset/Version/Relation HTTP API.

router는 HTTP 상태 코드와 입력 DTO 변환을 담당하고 실제 SQL은 repository로 위임한다.
모든 endpoint에는 router 수준의 ``authorize`` 의존성이 적용된다.
"""
from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from service.dependencies import authorize
from service.repositories import assets
from service.schemas import AssetCreate, AssetType, AssetUpdate, RelationCreate


router = APIRouter(prefix="/assets", tags=["assets"], dependencies=[Depends(authorize)])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_asset(payload: AssetCreate):
    """파일 없이 메타데이터 Asset을 먼저 생성한다."""
    return assets.create_asset(payload)


@router.get("")
def list_assets(
    asset_type: AssetType | None = None,
    system_name: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """삭제되지 않은 Asset 목록을 선택 필터와 페이지 단위로 반환한다."""
    return {"items": assets.list_assets(
        asset_type=asset_type, system_name=system_name, limit=limit, offset=offset
    )}


@router.get("/{asset_id}")
def get_asset(asset_id: UUID):
    """단일 Asset을 조회한다. soft delete된 Asset은 404로 취급한다."""
    result = assets.get_asset(asset_id)
    if not result:
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return result


@router.patch("/{asset_id}")
def update_asset(asset_id: UUID, payload: AssetUpdate):
    """전달된 필드만 변경하는 PATCH endpoint다."""
    result = assets.update_asset(asset_id, payload)
    if not result:
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return result


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: UUID):
    """물리 삭제 대신 deleted_at을 기록하고 대기/처리 중 작업을 취소한다."""
    if not assets.soft_delete_asset(asset_id):
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{asset_id}/versions")
def list_versions(asset_id: UUID):
    """Asset의 버전 이력을 반환하되 내부 서버 경로(source_path)는 숨긴다."""
    if not assets.get_asset(asset_id):
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    versions = assets.list_versions(asset_id)
    return {"items": [
        {key: value for key, value in version.items() if key != "source_path"}
        for version in versions
    ]}


@router.post("/{asset_id}/relations", status_code=status.HTTP_201_CREATED)
def create_relation(asset_id: UUID, payload: RelationCreate):
    """두 Asset 사이에 참조·대체·파생 등의 방향성 관계를 만든다."""
    if not assets.get_asset(asset_id) or not assets.get_asset(payload.target_asset_id):
        raise HTTPException(404, "source 또는 target asset을 찾을 수 없습니다")
    try:
        return assets.create_relation(asset_id, payload)
    except (CheckViolation, ForeignKeyViolation, UniqueViolation) as exc:
        raise HTTPException(409, "동일한 관계가 이미 존재하거나 관계가 유효하지 않습니다") from exc


@router.get("/{asset_id}/relations")
def list_relations(asset_id: UUID):
    """Asset이 source 또는 target으로 참여하는 모든 관계를 조회한다."""
    return {"items": assets.list_relations(asset_id)}


@router.delete("/{asset_id}/relations/{target_asset_id}/{relation_type}", status_code=204)
def delete_relation(asset_id: UUID, target_asset_id: UUID, relation_type: str):
    """source/target/type이 모두 일치하는 관계 하나를 삭제한다."""
    if not assets.delete_relation(asset_id, target_asset_id, relation_type):
        raise HTTPException(404, "관계를 찾을 수 없습니다")
    return Response(status_code=204)
