"""YAML 기반 애플리케이션 설정 로더.

코드는 특정 OS, 모델 이름, 포트, 디렉터리 또는 외부 솔루션 주소를 알지 못한다.
기본 설정(`config/settings.yaml`) 위에 환경별 profile을 deep-merge하고, 마지막으로
`${ENV_NAME}` 또는 `${ENV_NAME:-default}` 표현식을 환경 변수로 치환한다.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ROOT = Path(__file__).resolve().parents[1]
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeSettings(SettingsModel):
    environment: str = "local"
    os: str = "unknown"
    python: str = "3.11"


class ServerSettings(SettingsModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8300, ge=1, le=65535)
    api_prefix: str = "/api/v1"
    docs_enabled: bool = False
    workers: int = Field(default=2, ge=1, le=32)
    limit_concurrency: int = Field(default=100, ge=1, le=10000)
    backlog: int = Field(default=256, ge=1, le=65535)
    max_upload_mb: int = Field(default=5, ge=1)

    @field_validator("api_prefix")
    @classmethod
    def normalize_prefix(cls, value: str) -> str:
        value = "/" + value.strip("/")
        return "" if value == "/" else value


class PathSettings(SettingsModel):
    upload_root: Path = Path("artifacts/uploads")
    artifact_root: Path = Path("artifacts/processed")
    web_root: Path = Path("web")


class VectorStoreSettings(SettingsModel):
    provider: Literal["pgvector"] = "pgvector"
    distance_metric: Literal["cosine"] = "cosine"
    hnsw_ef_search: int = Field(default=100, ge=1)


class DatabaseSettings(SettingsModel):
    engine: Literal["postgresql"] = "postgresql"
    dsn: str = ""
    connect_timeout_seconds: int = Field(default=10, ge=1)
    schema_name: str = "rag"
    vector: VectorStoreSettings = Field(default_factory=VectorStoreSettings)


class EndpointSettings(SettingsModel):
    provider: str = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    timeout_seconds: float = Field(default=120.0, gt=0)


class LlmSettings(EndpointSettings):
    model: str = "gemma-4"
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1200, ge=64, le=8192)
    context_top_k: int = Field(default=8, ge=1, le=30)
    max_context_characters: int = Field(default=24000, ge=1000, le=200000)


class EmbeddingSettings(EndpointSettings):
    model: str = "bge-m3"
    dimension: int = Field(default=1024, ge=1)
    batch_size: int = Field(default=8, ge=1)
    max_input_characters: int = Field(default=24000, ge=1)


class RerankerSettings(SettingsModel):
    enabled: bool = False
    provider: str = "local_flag_embedding"
    model_path: str = ""
    use_fp16: bool = False
    top_k: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def require_model_when_enabled(self) -> "RerankerSettings":
        if self.enabled and not self.model_path:
            raise ValueError("models.reranker.enabled=true이면 model_path가 필요합니다")
        return self


class ModelSettings(SettingsModel):
    llm: LlmSettings = Field(default_factory=LlmSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)


class DoclingSettings(SettingsModel):
    artifacts_path: str = ""
    require_local_artifacts: bool = False
    table_mode: Literal["fast", "accurate"] = "accurate"
    do_cell_matching: bool = True


class DrmSettings(SettingsModel):
    enabled: bool = False
    command: str = ""
    timeout_seconds: int = Field(default=120, ge=1)

    @model_validator(mode="after")
    def require_command_when_enabled(self) -> "DrmSettings":
        if self.enabled and not self.command:
            raise ValueError("solutions.drm.enabled=true이면 command가 필요합니다")
        return self


class JarDecompilerSettings(SettingsModel):
    """JAR 디컴파일 실행 정책과 압축 해제 자원 상한."""

    backend: Literal["cfr"] = "cfr"
    java_command: str = "java"
    cfr_jar_path: str = ""
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    max_class_files: int = Field(default=2000, ge=1, le=100000)
    max_uncompressed_mb: int = Field(default=200, ge=1, le=10240)
    max_java_file_kb: int = Field(default=2048, ge=64, le=102400)


class SolutionSettings(SettingsModel):
    document_parser: Literal["docling"] = "docling"
    ocr: Literal["easyocr", "rapidocr", "disabled"] = "easyocr"
    easyocr_languages: list[str] = Field(
        default_factory=lambda: ["ko", "en"], min_length=1
    )
    docling: DoclingSettings = Field(default_factory=DoclingSettings)
    drm: DrmSettings = Field(default_factory=DrmSettings)
    jar_decompiler: JarDecompilerSettings = Field(default_factory=JarDecompilerSettings)


class WorkerSettings(SettingsModel):
    poll_seconds: float = Field(default=3.0, ge=0.1)
    claim_batch_size: int = Field(default=1, ge=1, le=100)
    processes: int = Field(default=2, ge=1, le=32)
    lease_seconds: int = Field(default=900, ge=30)
    max_attempts: int = Field(default=3, ge=1)
    worker_name: str = ""


class DuplicateDetectionSettings(SettingsModel):
    """기존 문서 후보 판별 정책.

    판별 결과는 자동 병합에 사용하지 않고 사용자 확인 후보로만 제공한다. 환경별 문서
    명명 품질에 맞춰 YAML에서 임계값과 후보 수를 조정할 수 있다.
    """

    enabled: bool = True
    title_similarity_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    max_candidates: int = Field(default=5, ge=1, le=20)


class IngestionSettings(SettingsModel):
    default_lifecycle_status: Literal["draft", "approved"] = "approved"
    max_pending_jobs: int = Field(default=100, ge=1, le=100000)
    allowed_extensions: dict[str, list[str]] = Field(default_factory=dict)
    duplicate_detection: DuplicateDetectionSettings = Field(
        default_factory=DuplicateDetectionSettings
    )


class RetrievalSettings(SettingsModel):
    exact_top_k: int = Field(default=30, ge=1)
    lexical_top_k: int = Field(default=40, ge=1)
    vector_top_k: int = Field(default=40, ge=1)
    fusion_top_k: int = Field(default=30, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    default_limit: int = Field(default=10, ge=1, le=100)
    max_limit: int = Field(default=30, ge=1, le=200)
    current_versions_only: bool = True
    approved_versions_only: bool = True
    expand_neighbors: int = Field(default=1, ge=0, le=1)


class AuthenticationSettings(SettingsModel):
    provider: Literal["java_command", "development_stub"] = "java_command"
    java_command: str = ""
    timeout_seconds: int = Field(default=15, ge=1, le=120)
    session_hours: int = Field(default=8, ge=1, le=168)
    cookie_name: str = "ecm_rag_session"
    cookie_secure: bool = True
    cookie_samesite: Literal["strict", "lax"] = "strict"


class SecuritySettings(SettingsModel):
    authentication: AuthenticationSettings = Field(default_factory=AuthenticationSettings)


class AppSettings(SettingsModel):
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    solutions: SolutionSettings = Field(default_factory=SolutionSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """중첩 mapping은 재귀 병합하고 scalar/list는 profile 값으로 교체한다."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _expand_env(value: Any) -> Any:
    """YAML 전체에서 환경 변수 표현식을 치환한다. 비밀값을 YAML에 저장하지 않는다."""
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise RuntimeError(f"필수 환경 변수 {name}가 설정되지 않았습니다")

    return _ENV_PATTERN.sub(replace, value)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"설정 파일의 최상위 값은 mapping이어야 합니다: {path}")
    return payload


def _resolve_paths(settings: AppSettings) -> AppSettings:
    """상대 경로는 실행 위치가 아니라 프로젝트 루트를 기준으로 고정한다."""
    for field_name in ("upload_root", "artifact_root", "web_root"):
        value = getattr(settings.paths, field_name)
        value = value.resolve() if value.is_absolute() else (ROOT / value).resolve()
        setattr(settings.paths, field_name, value)
    return settings


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """공통 YAML, 환경 profile, 환경 변수 순서로 최종 설정을 생성한다.

    우선순위는 ``settings.yaml < profiles/{profile}.yaml < 환경 변수 치환값``이다.
    Pydantic의 ``extra=forbid`` 정책으로 오탈자 설정을 조용히 무시하지 않고 시작 시
    실패시킨다. 결과는 프로세스 동안 캐시해 매 요청마다 파일을 읽지 않는다.
    """
    config_path = Path(os.environ.get("RAG_CONFIG_FILE", ROOT / "config" / "settings.yaml"))
    data = _read_yaml(config_path)
    profile = os.environ.get("RAG_PROFILE", str(data.pop("default_profile", "local"))).strip()
    if profile:
        profile_path = config_path.parent / "profiles" / f"{profile}.yaml"
        if profile_path.is_file():
            data = _deep_merge(data, _read_yaml(profile_path))
        elif "RAG_PROFILE" in os.environ:
            raise FileNotFoundError(f"설정 profile을 찾을 수 없습니다: {profile_path}")
    settings = AppSettings.model_validate(_expand_env(data))
    settings.runtime.environment = profile or settings.runtime.environment
    return _resolve_paths(settings)


def reload_settings() -> AppSettings:
    """테스트 또는 관리 도구가 환경 변수를 바꾼 뒤 설정을 다시 읽을 때 사용한다."""
    get_settings.cache_clear()
    return get_settings()
