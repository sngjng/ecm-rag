"""HTTP와 repository 사이에서 사용하는 명시적 데이터 계약."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


AssetType = Literal["document", "incident", "error_trace", "source_code"]
LifecycleStatus = Literal["draft", "approved", "obsolete"]
RelationType = Literal[
    "supersedes", "references", "related_to", "derived_from", "resolved_by", "applies_to"
]


class AssetCreate(BaseModel):
    asset_type: AssetType
    subtype: str = Field(default="general", min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    vendor: str | None = Field(default=None, max_length=200)
    product: str | None = Field(default=None, max_length=200)
    system_name: str | None = Field(default=None, max_length=200)
    service_name: str | None = Field(default=None, max_length=200)
    component: str | None = Field(default=None, max_length=200)
    owner_org: str | None = Field(default=None, max_length=200)
    security_level: str = Field(default="internal", min_length=1, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetUpdate(BaseModel):
    subtype: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    vendor: str | None = Field(default=None, max_length=200)
    product: str | None = Field(default=None, max_length=200)
    system_name: str | None = Field(default=None, max_length=200)
    service_name: str | None = Field(default=None, max_length=200)
    component: str | None = Field(default=None, max_length=200)
    owner_org: str | None = Field(default=None, max_length=200)
    security_level: str | None = Field(default=None, min_length=1, max_length=50)
    metadata: dict[str, Any] | None = None

    @classmethod
    def _required_patch_fields(cls) -> tuple[str, ...]:
        return ("subtype", "title", "security_level")

    def model_post_init(self, __context: Any) -> None:
        for name in self._required_patch_fields():
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name}은 null로 변경할 수 없습니다")


class RelationCreate(BaseModel):
    target_asset_id: UUID
    relation_type: RelationType
    evidence: str | None = Field(default=None, max_length=4000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    asset_type: AssetType | None = None
    system_name: str | None = Field(default=None, max_length=200)
    vendor: str | None = Field(default=None, max_length=200)
    product: str | None = Field(default=None, max_length=200)
    include_obsolete: bool = False
    limit: int | None = Field(default=None, ge=1, le=200)
