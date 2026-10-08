"""Asset/Version/Relation PostgreSQL repository.

이 계층은 HTTP를 알지 못하며 SQL과 트랜잭션만 담당한다. 반환값은 ``dict_row``로
변환된 DB row이고, UUID·datetime 직렬화는 FastAPI 응답 계층이 처리한다.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from service.db import connect
from service.schemas import AssetCreate, AssetUpdate, RelationCreate


def create_asset(payload: AssetCreate) -> dict[str, Any]:
    """논리 문서 자산의 식별자와 업무 메타데이터를 생성한다."""
    asset_id = uuid4()
    with connect() as conn:
        return conn.execute(
            """INSERT INTO assets (asset_id, asset_type, subtype, title, vendor, product,
                   system_name, service_name, component, owner_org, security_level, metadata)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING *""",
            (
                asset_id, payload.asset_type, payload.subtype, payload.title, payload.vendor,
                payload.product, payload.system_name, payload.service_name, payload.component,
                payload.owner_org, payload.security_level, Jsonb(payload.metadata),
            ),
        ).fetchone()


def get_asset(asset_id: UUID | str, *, include_deleted: bool = False) -> dict[str, Any] | None:
    """Asset 단건 조회. 기본값은 soft delete된 행을 제외한다."""
    condition = "" if include_deleted else " AND deleted_at IS NULL"
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM assets WHERE asset_id=%s" + condition,
            (UUID(str(asset_id)),),
        ).fetchone()


def list_assets(
    *, asset_type: str | None = None, system_name: str | None = None,
    limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    """선택 조건과 offset pagination으로 활성 Asset을 조회한다.

    nullable filter의 첫 placeholder에 ``::text``를 명시한 이유는 값이 ``None``일 때
    PostgreSQL이 파라미터 타입을 추론하지 못하는 오류를 방지하기 위해서다.
    """
    with connect() as conn:
        return conn.execute(
            """SELECT * FROM assets
               WHERE deleted_at IS NULL
                 AND (%s::text IS NULL OR asset_type=%s)
                 AND (%s::text IS NULL OR system_name=%s)
               ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            (asset_type, asset_type, system_name, system_name, limit, offset),
        ).fetchall()


def update_asset(asset_id: UUID | str, payload: AssetUpdate) -> dict[str, Any] | None:
    """Pydantic의 ``exclude_unset``을 이용해 요청에 포함된 필드만 동적으로 갱신한다."""
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return get_asset(asset_id)
    assignments = []
    values: list[Any] = []
    for name, value in changes.items():
        # 컬럼명은 값 placeholder로 바인딩할 수 없으므로 psycopg.sql.Identifier로
        # 안전하게 조립한다. DTO가 허용한 필드만 changes에 들어온다.
        assignments.append(sql.SQL("{}=%s").format(sql.Identifier(name)))
        values.append(Jsonb(value) if name == "metadata" else value)
    values.append(UUID(str(asset_id)))
    statement = sql.SQL("UPDATE assets SET {}, updated_at=now() "
                        "WHERE asset_id=%s AND deleted_at IS NULL RETURNING *").format(
        sql.SQL(", ").join(assignments)
    )
    with connect() as conn:
        return conn.execute(statement, values).fetchone()


def soft_delete_asset(asset_id: UUID | str) -> bool:
    """Asset을 soft delete하고 아직 끝나지 않은 ingestion job을 함께 취소한다.

    두 UPDATE는 같은 connection context 안에서 실행되어 하나의 트랜잭션으로 commit된다.
    이미 처리 완료된 청크와 버전은 감사·계보 보존을 위해 물리 삭제하지 않는다.
    """
    with connect() as conn:
        row = conn.execute(
            """UPDATE assets SET deleted_at=now(), updated_at=now()
               WHERE asset_id=%s AND deleted_at IS NULL RETURNING asset_id""",
            (UUID(str(asset_id)),),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE ingestion_jobs SET status='cancelled', completed_at=now(), updated_at=now()
                   WHERE version_id IN (SELECT version_id FROM asset_versions WHERE asset_id=%s)
                     AND status IN ('queued','processing')""",
                (UUID(str(asset_id)),),
            )
        return row is not None


def create_version_and_job(
    *, asset_id: UUID | str, version_label: str, source_filename: str, source_path: str,
    sha256: str, lifecycle_status: str, is_current: bool, effective_from: date | None,
    effective_to: date | None, repository: str, branch: str, logical_path: str,
    max_attempts: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """새 Version과 ingestion job을 원자적으로 생성한다.

    현재 버전으로 등록할 때 기존 현재 버전을 먼저 해제한다. DB의 partial unique index가
    동시 요청에서도 Asset당 현재 버전이 하나뿐이라는 규칙을 최종 보장한다.
    """
    version_id, job_id = uuid4(), uuid4()
    with connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM assets WHERE asset_id=%s AND deleted_at IS NULL",
            (UUID(str(asset_id)),),
        ).fetchone():
            raise LookupError("asset을 찾을 수 없습니다")
        if is_current:
            conn.execute("UPDATE asset_versions SET is_current=false WHERE asset_id=%s AND is_current",
                         (UUID(str(asset_id)),))
        version = conn.execute(
            """INSERT INTO asset_versions
               (version_id, asset_id, version_label, source_filename, source_path, sha256,
                repository, branch, logical_path, lifecycle_status, is_current,
                effective_from, effective_to)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                version_id, UUID(str(asset_id)), version_label, source_filename, source_path,
                sha256, repository or None, branch or None, logical_path or None,
                lifecycle_status, is_current, effective_from, effective_to,
            ),
        ).fetchone()
        job = conn.execute(
            """INSERT INTO ingestion_jobs (job_id, version_id, max_attempts)
               VALUES (%s,%s,%s) RETURNING *""",
            (job_id, version_id, max_attempts),
        ).fetchone()
    return version, job


def list_versions(asset_id: UUID | str) -> list[dict[str, Any]]:
    """Asset의 모든 버전을 최신 등록 순으로 조회한다."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM asset_versions WHERE asset_id=%s ORDER BY created_at DESC",
            (UUID(str(asset_id)),),
        ).fetchall()


def create_relation(source_asset_id: UUID | str, payload: RelationCreate) -> dict[str, Any]:
    """Asset 간 방향성 관계를 생성한다. 무결성 위반은 route가 409로 변환한다."""
    with connect() as conn:
        return conn.execute(
            """INSERT INTO asset_relations
               (source_asset_id, target_asset_id, relation_type, evidence)
               VALUES (%s,%s,%s,%s) RETURNING *""",
            (UUID(str(source_asset_id)), payload.target_asset_id, payload.relation_type, payload.evidence),
        ).fetchone()


def list_relations(asset_id: UUID | str) -> list[dict[str, Any]]:
    """source/target 양쪽 관점에서 Asset 관계를 반환한다."""
    with connect() as conn:
        return conn.execute(
            """SELECT * FROM asset_relations
               WHERE source_asset_id=%s OR target_asset_id=%s ORDER BY created_at""",
            (UUID(str(asset_id)), UUID(str(asset_id))),
        ).fetchall()


def delete_relation(source_asset_id: UUID | str, target_asset_id: UUID | str, relation_type: str) -> bool:
    """정확히 일치하는 관계를 삭제하고 실제 삭제 여부를 반환한다."""
    with connect() as conn:
        row = conn.execute(
            """DELETE FROM asset_relations
               WHERE source_asset_id=%s AND target_asset_id=%s AND relation_type=%s
               RETURNING source_asset_id""",
            (UUID(str(source_asset_id)), UUID(str(target_asset_id)), relation_type),
        ).fetchone()
        return row is not None
