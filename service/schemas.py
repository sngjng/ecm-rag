"""HTTP 요청과 repository 사이에서 사용하는 명시적 데이터 계약.

Literal과 Field 제약은 잘못된 값이 SQL 계층까지 내려가기 전에 422 응답으로 차단한다.
PATCH DTO는 unset과 명시적 null을 구분해 필수 업무 필드가 null이 되지 않게 한다.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, model_validator

AssetType = Literal["document", "incident", "error_trace", "source_code"]
LifecycleStatus = Literal["draft", "approved", "obsolete"]
RelationType = Literal[
    "supersedes", "references", "related_to", "derived_from", "resolved_by", "applies_to"
]
AccessRole = Literal["viewer", "uploader", "admin"]


class LoginRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    password: SecretStr = Field(min_length=1, max_length=256)


class EmployeeIdentity(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    # 사내 응답의 emp_no와 user_id가 다른 환경을 위해 두 값을 별도로 보존한다.
    user_id: str = Field(default="", max_length=50)
    employee_name: str = Field(min_length=1, max_length=100)
    department: str = Field(default="", max_length=200)
    upper_department: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)
    roles: list[str] = Field(default_factory=list)


class AccessRoleUpsert(BaseModel):
    """관리자가 사번에 부여할 단일 접근 권한과 표시용 조직정보."""

    role: AccessRole
    employee_name: str | None = Field(default=None, max_length=100)
    department: str | None = Field(default=None, max_length=200)
    upper_department: str | None = Field(default=None, max_length=200)


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

    def model_post_init(self, __context: Any, /) -> None:
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


class ChatHistoryMessage(BaseModel):
    """LLM에 전달할 최근 대화 한 건이다."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(SearchRequest):
    """문서 검색과 근거 기반 답변 생성을 한 번에 요청한다."""

    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=10)


class ExternalAnswerRequest(ChatRequest):
    """내부 시스템용 답변 요청과 검색 청크 반환 여부를 정의한다."""

    # false여도 검색과 LLM 답변 생성에는 청크를 사용한다. 단지 HTTP 응답에서 원문
    # 청크 배열을 제외해 연동 트래픽과 불필요한 원문 노출을 줄인다.
    include_chunks: bool = True


class IncidentGuideRequest(BaseModel):
    """대량 장애 로그와 자연어 후속 질문을 함께 받는 분석 계약."""

    log_text: str = Field(default="", max_length=1000000)
    question: str = Field(default="", max_length=2000)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=8, ge=1, le=20)

    @model_validator(mode="after")
    def require_log_or_question(self) -> IncidentGuideRequest:
        self.log_text = self.log_text.strip()
        self.question = self.question.strip()
        if not self.log_text and not self.question:
            raise ValueError("장애 로그 또는 질문을 입력해 주세요")
        return self


class IncidentTemplateUpdate(BaseModel):
    """관리자가 변경할 장애 해결 가이드 출력 템플릿."""

    content: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def reject_blank_content(self) -> IncidentTemplateUpdate:
        self.content = self.content.strip()
        if not self.content:
            raise ValueError("템플릿 내용을 입력해 주세요")
        return self


class AdminChunkCreate(BaseModel):
    """관리자 화면에서 수동으로 생성할 검색 청크와 pgvector 입력 계약."""

    version_id: UUID
    kind: str = Field(default="admin_text", min_length=1, max_length=100)
    display_content: str = Field(min_length=1, max_length=100000)
    embedding_content: str | None = Field(default=None, max_length=100000)
    identifier: str | None = Field(default=None, max_length=1000)
    heading_path: str | None = Field(default=None, max_length=2000)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminChunkUpdate(BaseModel):
    """관리자 청크 수정 계약. 본문 변경 시 route가 vector를 다시 생성한다."""

    kind: str | None = Field(default=None, min_length=1, max_length=100)
    display_content: str | None = Field(default=None, min_length=1, max_length=100000)
    embedding_content: str | None = Field(default=None, min_length=1, max_length=100000)
    identifier: str | None = Field(default=None, max_length=1000)
    heading_path: str | None = Field(default=None, max_length=2000)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] | None = None

    def model_post_init(self, __context: Any, /) -> None:
        """DB의 NOT NULL 컬럼을 PATCH에서 명시적 null로 바꾸지 못하게 한다."""
        for name in ("kind", "display_content"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name}은 null로 변경할 수 없습니다")
        if "metadata" in self.model_fields_set and self.metadata is None:
            raise ValueError("metadata는 null로 변경할 수 없습니다")
