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


def try_acquire_upload_lock(sha256: str):
    """같은 원본 해시에 대한 동시 등록을 PostgreSQL session lock으로 직렬화한다.

    대기하지 않는 try-lock을 사용하므로 동일 파일 요청이 몰려도 API worker가 잠긴 채
    쌓이지 않는다. 반환된 connection은 반드시 ``release_upload_lock``으로 닫아야 한다.
    """
    lock_key = int(sha256[:16], 16)
    if lock_key >= 2**63:
        lock_key -= 2**64
    conn = connect()
    try:
        acquired = conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key,)
        ).fetchone()["acquired"]
        if not acquired:
            conn.close()
            return None
        return conn
    except Exception:
        conn.close()
        raise


def release_upload_lock(connection) -> None:
    """원본 해시 등록 lock을 connection 종료로 확실히 해제한다."""
    connection.close()


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


def count_assets_by_type() -> dict[str, int]:
    """삭제되지 않은 논리 문서자산 수를 자산 유형별로 집계한다."""
    counts = {
        "document": 0,
        "incident": 0,
        "error_trace": 0,
        "source_code": 0,
    }
    with connect() as conn:
        rows = conn.execute(
            """SELECT asset_type, count(*) AS count
                 FROM assets
                WHERE deleted_at IS NULL
                GROUP BY asset_type"""
        ).fetchall()
    for row in rows:
        if row["asset_type"] in counts:
            counts[row["asset_type"]] = int(row["count"])
    return counts


def find_duplicate_candidates(
    *, asset_type: str, title: str, source_filename: str, sha256: str,
    system_name: str, service_name: str, repository: str, logical_path: str,
    title_similarity_threshold: float, limit: int,
) -> list[dict[str, Any]]:
    """업로드 원본과 같은 논리 문서일 가능성이 있는 Asset 후보를 찾는다.

    전체 버전의 동일 내용 해시, 현재 버전의 파일명·소스 경로, 공백을 제거한 제목,
    pg_trgm 제목 유사도를 함께 사용한다. 결과는 자동 병합 결정이 아니라 사용자 확인
    화면에 제공할 후보이며 각 행에는 판별 근거 boolean/유사도 값이 포함된다.
    """
    with connect() as conn:
        rows = conn.execute(
            """WITH input AS (
                   SELECT %s::text AS asset_type, %s::text AS title,
                          %s::text AS source_filename, %s::char(64) AS sha256,
                          %s::text AS system_name, %s::text AS service_name,
                          %s::text AS repository, %s::text AS logical_path
                 ), latest_version AS (
                   SELECT DISTINCT ON (v.asset_id)
                          v.asset_id, v.version_id, v.version_label,
                          v.source_filename, v.sha256, v.repository,
                          v.logical_path, v.created_at
                     FROM asset_versions v
                    ORDER BY v.asset_id, v.is_current DESC, v.created_at DESC
                 )
               SELECT a.asset_id, a.asset_type, a.title, a.system_name, a.service_name,
                      a.owner_org, a.updated_at, v.version_id AS current_version_id,
                      v.version_label AS current_version_label,
                      v.source_filename, v.created_at AS version_created_at,
                      EXISTS (
                        SELECT 1 FROM asset_versions ev
                         WHERE ev.asset_id=a.asset_id AND ev.sha256=i.sha256
                      ) AS exact_content,
                      (lower(v.source_filename)=lower(i.source_filename)) AS exact_filename,
                      (i.repository <> '' AND i.logical_path <> '' AND
                       lower(coalesce(v.repository, ''))=lower(i.repository) AND
                       lower(coalesce(v.logical_path, ''))=lower(i.logical_path)) AS exact_source_path,
                      (regexp_replace(lower(trim(a.title)), '\\s+', '', 'g') =
                       regexp_replace(lower(trim(i.title)), '\\s+', '', 'g')) AS exact_title,
                      (i.system_name <> '' AND
                       lower(coalesce(a.system_name, ''))=lower(i.system_name)) AS same_system,
                      (i.service_name <> '' AND
                       lower(coalesce(a.service_name, ''))=lower(i.service_name)) AS same_service,
                      similarity(lower(a.title), lower(i.title)) AS title_similarity
                 FROM assets a
                 JOIN latest_version v USING (asset_id)
                CROSS JOIN input i
                WHERE a.deleted_at IS NULL
                  AND a.asset_type=i.asset_type
                  AND (
                    EXISTS (
                      SELECT 1 FROM asset_versions ev
                       WHERE ev.asset_id=a.asset_id AND ev.sha256=i.sha256
                    )
                    OR (i.repository <> '' AND i.logical_path <> '' AND
                        lower(coalesce(v.repository, ''))=lower(i.repository) AND
                        lower(coalesce(v.logical_path, ''))=lower(i.logical_path))
                    OR lower(v.source_filename)=lower(i.source_filename)
                    OR regexp_replace(lower(trim(a.title)), '\\s+', '', 'g') =
                       regexp_replace(lower(trim(i.title)), '\\s+', '', 'g')
                    OR similarity(lower(a.title), lower(i.title)) >= %s
                  )
                ORDER BY EXISTS (
                           SELECT 1 FROM asset_versions ev
                            WHERE ev.asset_id=a.asset_id AND ev.sha256=i.sha256
                         ) DESC,
                         (i.repository <> '' AND i.logical_path <> '' AND
                          lower(coalesce(v.repository, ''))=lower(i.repository) AND
                          lower(coalesce(v.logical_path, ''))=lower(i.logical_path)) DESC,
                         (regexp_replace(lower(trim(a.title)), '\\s+', '', 'g') =
                          regexp_replace(lower(trim(i.title)), '\\s+', '', 'g')) DESC,
                         (lower(v.source_filename)=lower(i.source_filename)) DESC,
                         similarity(lower(a.title), lower(i.title)) DESC,
                         v.created_at DESC
                LIMIT %s""",
            (
                asset_type, title, source_filename, sha256,
                system_name, service_name, repository, logical_path,
                title_similarity_threshold, limit,
            ),
        ).fetchall()

    for row in rows:
        reasons = []
        if row["exact_content"]:
            reasons.append("동일한 파일 내용")
        if row["exact_source_path"]:
            reasons.append("동일한 저장소 경로")
        if row["exact_title"]:
            reasons.append("동일한 제목")
        elif row["title_similarity"] >= title_similarity_threshold:
            reasons.append(f"유사한 제목 ({row['title_similarity']:.0%})")
        if row["exact_filename"]:
            reasons.append("동일한 파일명")
        if row["same_system"]:
            reasons.append("동일한 시스템")
        if row["same_service"]:
            reasons.append("동일한 서비스")
        row["match_reasons"] = reasons
    return rows


def find_exact_content_job(asset_type: str, sha256: str) -> dict[str, Any] | None:
    """동일 원본의 가장 최근 Version과 처리 작업을 반환한다.

    처리 실패 원본을 정상 등록 문서 후보로 오인하지 않도록 중복 후보 판별보다 먼저
    사용한다. sha256 advisory lock을 보유한 route에서 호출하므로 같은 원본의 재접수
    판단도 요청 간 직렬화된다.
    """
    with connect() as conn:
        return conn.execute(
            """SELECT a.asset_id, v.version_id, v.source_path, v.source_filename,
                      j.job_id, j.status, j.attempts, j.max_attempts,
                      j.error_detail, j.report
                 FROM asset_versions v
                 JOIN assets a USING(asset_id)
                 JOIN ingestion_jobs j USING(version_id)
                WHERE a.deleted_at IS NULL
                  AND a.asset_type=%s
                  AND v.sha256=%s
                ORDER BY v.created_at DESC
                LIMIT 1""",
            (asset_type, sha256),
        ).fetchone()


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
    max_attempts: int, audit_action: str, employee_id: str, employee_name: str,
    department: str, client_ip: str | None, user_agent: str | None,
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
        # Version/Job과 같은 트랜잭션에 사원정보 snapshot을 기록해 접수 이력이
        # 누락되거나 다른 문서에 연결되는 것을 방지한다.
        conn.execute(
            """INSERT INTO upload_audit
               (audit_id, asset_id, version_id, job_id, action, employee_id,
                employee_name, department, client_ip, user_agent)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid4(), UUID(str(asset_id)), version_id, job_id, audit_action,
                employee_id, employee_name, department or None,
                client_ip, user_agent,
            ),
        )
    return version, job


def list_versions(asset_id: UUID | str) -> list[dict[str, Any]]:
    """Asset의 모든 버전을 최신 등록 순으로 조회한다."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM asset_versions WHERE asset_id=%s ORDER BY created_at DESC",
            (UUID(str(asset_id)),),
        ).fetchall()


def get_version(asset_id: UUID | str, version_id: UUID | str) -> dict[str, Any] | None:
    """활성 Asset에 속한 특정 버전과 원본 저장 정보를 반환한다."""
    with connect() as conn:
        return conn.execute(
            """SELECT v.*
                 FROM asset_versions v
                 JOIN assets a USING (asset_id)
                WHERE v.asset_id=%s AND v.version_id=%s AND a.deleted_at IS NULL""",
            (UUID(str(asset_id)), UUID(str(version_id))),
        ).fetchone()


def list_upload_audit(asset_id: UUID | str) -> list[dict[str, Any]]:
    """문서 접수 당시 사원정보와 신규/버전등록 이력을 시간 역순으로 반환한다."""
    with connect() as conn:
        return conn.execute(
            """SELECT audit_id, asset_id, version_id, job_id, action,
                      employee_id, employee_name, department, created_at
                 FROM upload_audit WHERE asset_id=%s ORDER BY created_at DESC""",
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
