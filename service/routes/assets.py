"""PostgreSQL Asset/Version/Relation CRUD endpoint."""
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
    return assets.create_asset(payload)


@router.get("")
def list_assets(
    asset_type: AssetType | None = None,
    system_name: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return {"items": assets.list_assets(
        asset_type=asset_type, system_name=system_name, limit=limit, offset=offset
    )}


@router.get("/{asset_id}")
def get_asset(asset_id: UUID):
    result = assets.get_asset(asset_id)
    if not result:
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return result


@router.patch("/{asset_id}")
def update_asset(asset_id: UUID, payload: AssetUpdate):
    result = assets.update_asset(asset_id, payload)
    if not result:
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return result


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: UUID):
    if not assets.soft_delete_asset(asset_id):
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{asset_id}/versions")
def list_versions(asset_id: UUID):
    if not assets.get_asset(asset_id):
        raise HTTPException(404, "asset을 찾을 수 없습니다")
    versions = assets.list_versions(asset_id)
    return {"items": [
        {key: value for key, value in version.items() if key != "source_path"}
        for version in versions
    ]}


@router.post("/{asset_id}/relations", status_code=status.HTTP_201_CREATED)
def create_relation(asset_id: UUID, payload: RelationCreate):
    if not assets.get_asset(asset_id) or not assets.get_asset(payload.target_asset_id):
        raise HTTPException(404, "source 또는 target asset을 찾을 수 없습니다")
    try:
        return assets.create_relation(asset_id, payload)
    except (CheckViolation, ForeignKeyViolation, UniqueViolation) as exc:
        raise HTTPException(409, "동일한 관계가 이미 존재하거나 관계가 유효하지 않습니다") from exc


@router.get("/{asset_id}/relations")
def list_relations(asset_id: UUID):
    return {"items": assets.list_relations(asset_id)}


@router.delete("/{asset_id}/relations/{target_asset_id}/{relation_type}", status_code=204)
def delete_relation(asset_id: UUID, target_asset_id: UUID, relation_type: str):
    if not assets.delete_relation(asset_id, target_asset_id, relation_type):
        raise HTTPException(404, "관계를 찾을 수 없습니다")
    return Response(status_code=204)
