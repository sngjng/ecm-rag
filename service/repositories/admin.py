"""관리자 화면용 PostgreSQL/pgvector 조회·수정 repository.

관리 화면이 임의 SQL이나 raw vector를 직접 다루지 않도록 허용 컬럼과 트랜잭션을 이
모듈에 고정한다. 청크 삭제 시 FK cascade로 연결된 오류 이벤트·코드 심볼도 함께
삭제되므로 route와 화면에서 사용자 확인을 거친 뒤 호출해야 한다.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import numpy as np
from psycopg import sql
from psycopg.types.json import Jsonb

from service.db import connect


def get_stats() -> dict[str, int]:
    """관리 화면 상단에 표시할 핵심 테이블 건수를 한 번에 조회한다."""
    with connect() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT count(*) FROM assets WHERE deleted_at IS NULL) AS assets,
                 (SELECT count(*) FROM assets WHERE deleted_at IS NOT NULL) AS deleted_assets,
                 (SELECT count(*) FROM asset_versions) AS versions,
                 (SELECT count(*) FROM chunks) AS chunks,
                 (SELECT count(*) FROM ingestion_jobs WHERE status='queued') AS queued_jobs,
                 (SELECT count(*) FROM ingestion_jobs WHERE status='processing') AS processing_jobs,
                 (SELECT count(*) FROM ingestion_jobs WHERE status='failed') AS failed_jobs"""
        ).fetchone()
    return {key: int(value) for key, value in row.items()}


def list_assets(
    *, query: str | None, asset_type: str | None, include_deleted: bool,
    limit: int, offset: int,
) -> dict[str, Any]:
    """삭제 자료 포함 여부와 검색어를 적용해 Asset과 하위 건수를 조회한다."""
    pattern = f"%{query.strip()}%" if query and query.strip() else None
    deleted_filter = "TRUE" if include_deleted else "a.deleted_at IS NULL"
    where = f"""{deleted_filter}
        AND (%s::text IS NULL OR a.asset_type=%s)
        AND (%s::text IS NULL OR a.title ILIKE %s OR a.system_name ILIKE %s)"""
    params = (asset_type, asset_type, pattern, pattern, pattern)
    with connect() as conn:
        total = conn.execute(
            f"SELECT count(*) AS count FROM assets a WHERE {where}", params
        ).fetchone()["count"]
        items = conn.execute(
            f"""SELECT a.*,
                       count(DISTINCT v.version_id) AS version_count,
                       count(DISTINCT c.chunk_id) AS chunk_count
                  FROM assets a
                  LEFT JOIN asset_versions v USING(asset_id)
                  LEFT JOIN chunks c USING(asset_id)
                 WHERE {where}
                 GROUP BY a.asset_id
                 ORDER BY a.created_at DESC
                 LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        ).fetchall()
    return {"items": items, "total": int(total)}


def restore_asset(asset_id: UUID | str) -> dict[str, Any] | None:
    """soft delete된 Asset을 복구한다. 취소된 작업은 자동 재시도하지 않는다."""
    with connect() as conn:
        return conn.execute(
            """UPDATE assets SET deleted_at=NULL, updated_at=now()
                 WHERE asset_id=%s AND deleted_at IS NOT NULL RETURNING *""",
            (UUID(str(asset_id)),),
        ).fetchone()


def list_versions(asset_id: UUID | str | None = None, limit: int = 200) -> list[dict]:
    """청크 등록 대상 선택에 필요한 Version과 Asset 제목을 반환한다."""
    with connect() as conn:
        return conn.execute(
            """SELECT v.version_id, v.asset_id, a.title, a.asset_type, v.version_label,
                      v.source_filename, v.lifecycle_status, v.is_current, v.indexed_at,
                      v.created_at, count(c.chunk_id) AS chunk_count
                 FROM asset_versions v
                 JOIN assets a USING(asset_id)
                 LEFT JOIN chunks c USING(version_id)
                WHERE a.deleted_at IS NULL
                  AND (%s::uuid IS NULL OR v.asset_id=%s)
                GROUP BY v.version_id, a.title, a.asset_type
                ORDER BY v.created_at DESC LIMIT %s""",
            (UUID(str(asset_id)) if asset_id else None,
             UUID(str(asset_id)) if asset_id else None, limit),
        ).fetchall()


def list_chunks(
    *, query: str | None, asset_id: UUID | None, version_id: UUID | None,
    limit: int, offset: int,
) -> dict[str, Any]:
    """vector 본체 대신 차원만 노출하고 청크 본문·metadata를 페이지 조회한다."""
    pattern = f"%{query.strip()}%" if query and query.strip() else None
    params = (asset_id, asset_id, version_id, version_id, pattern, pattern, pattern)
    where = """(%s::uuid IS NULL OR c.asset_id=%s)
        AND (%s::uuid IS NULL OR c.version_id=%s)
        AND (%s::text IS NULL OR c.display_content ILIKE %s OR c.identifier ILIKE %s)"""
    with connect() as conn:
        total = conn.execute(
            f"SELECT count(*) AS count FROM chunks c WHERE {where}", params
        ).fetchone()["count"]
        items = conn.execute(
            f"""SELECT c.chunk_id, c.asset_id, c.version_id, c.chunk_seq, c.kind,
                       c.display_content, c.embedding_content, c.identifier, c.heading_path,
                       c.page_start, c.page_end, c.line_start, c.line_end, c.metadata,
                       c.embedding_model, vector_dims(c.embedding) AS embedding_dimensions,
                       c.created_at, a.title, v.version_label, v.source_filename
                  FROM chunks c
                  JOIN assets a USING(asset_id)
                  JOIN asset_versions v USING(version_id)
                 WHERE {where}
                 ORDER BY c.created_at DESC, c.chunk_seq
                 LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        ).fetchall()
    return {"items": items, "total": int(total)}


def get_chunk(chunk_id: str) -> dict[str, Any] | None:
    """수정 전에 사용할 청크 한 건을 vector 본체 없이 반환한다."""
    with connect() as conn:
        return conn.execute(
            """SELECT chunk_id, asset_id, version_id, chunk_seq, kind, display_content,
                      embedding_content, identifier, heading_path, page_start, page_end,
                      line_start, line_end, metadata, embedding_model,
                      vector_dims(embedding) AS embedding_dimensions, created_at
                 FROM chunks WHERE chunk_id=%s""",
            (chunk_id,),
        ).fetchone()


def create_chunk(payload: dict[str, Any], vector: list[float], model: str) -> dict[str, Any]:
    """Version row lock으로 sequence 충돌을 막고 청크와 vector를 함께 생성한다."""
    version_id = UUID(str(payload["version_id"]))
    with connect() as conn:
        version = conn.execute(
            """SELECT v.asset_id FROM asset_versions v JOIN assets a USING(asset_id)
                 WHERE v.version_id=%s AND a.deleted_at IS NULL FOR UPDATE OF v""",
            (version_id,),
        ).fetchone()
        if not version:
            raise LookupError("활성 Asset의 Version을 찾을 수 없습니다")
        sequence = conn.execute(
            "SELECT coalesce(max(chunk_seq), -1) + 1 AS seq FROM chunks WHERE version_id=%s",
            (version_id,),
        ).fetchone()["seq"]
        chunk_id = f"{version_id}:admin:{uuid4().hex}"
        row = conn.execute(
            """INSERT INTO chunks
               (chunk_id, asset_id, version_id, chunk_seq, kind, display_content,
                embedding_content, page_start, page_end, line_start, line_end, identifier,
                heading_path, metadata, embedding_model, embedding)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING chunk_id, asset_id, version_id, chunk_seq, kind, display_content,
                         embedding_content, identifier, heading_path, page_start, page_end,
                         line_start, line_end, metadata, embedding_model,
                         vector_dims(embedding) AS embedding_dimensions, created_at""",
            (
                chunk_id, version["asset_id"], version_id, sequence, payload["kind"],
                payload["display_content"], payload["embedding_content"],
                payload.get("page_start"), payload.get("page_end"),
                payload.get("line_start"), payload.get("line_end"),
                payload.get("identifier"), payload.get("heading_path"),
                Jsonb(payload.get("metadata") or {}), model,
                np.asarray(vector, dtype=np.float32),
            ),
        ).fetchone()
        conn.execute(
            """UPDATE ingestion_jobs SET chunk_count=(SELECT count(*) FROM chunks WHERE version_id=%s),
                      updated_at=now() WHERE version_id=%s""",
            (version_id, version_id),
        )
        return row


def update_chunk(
    chunk_id: str, changes: dict[str, Any], vector: list[float] | None, model: str | None,
) -> dict[str, Any] | None:
    """허용 필드만 수정하고 본문 변경 시 전달받은 vector도 같은 트랜잭션에 교체한다."""
    if not changes and vector is None:
        return get_chunk(chunk_id)
    assignments = []
    values: list[Any] = []
    for name, value in changes.items():
        assignments.append(sql.SQL("{}=%s").format(sql.Identifier(name)))
        values.append(Jsonb(value) if name == "metadata" else value)
    if vector is not None:
        assignments.extend([sql.SQL("embedding=%s"), sql.SQL("embedding_model=%s")])
        values.extend([np.asarray(vector, dtype=np.float32), model])
    values.append(chunk_id)
    statement = sql.SQL(
        "UPDATE chunks SET {} WHERE chunk_id=%s RETURNING chunk_id, asset_id, version_id, "
        "chunk_seq, kind, display_content, embedding_content, identifier, heading_path, "
        "page_start, page_end, line_start, line_end, metadata, embedding_model, "
        "vector_dims(embedding) AS embedding_dimensions, created_at"
    ).format(sql.SQL(", ").join(assignments))
    with connect() as conn:
        return conn.execute(statement, values).fetchone()


def delete_chunk(chunk_id: str) -> bool:
    """청크를 삭제하고 연결 job의 표시용 chunk_count를 다시 계산한다."""
    with connect() as conn:
        row = conn.execute(
            "DELETE FROM chunks WHERE chunk_id=%s RETURNING version_id", (chunk_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """UPDATE ingestion_jobs SET chunk_count=(SELECT count(*) FROM chunks WHERE version_id=%s),
                      updated_at=now() WHERE version_id=%s""",
            (row["version_id"], row["version_id"]),
        )
        return True


def list_jobs(status: str | None, limit: int, offset: int) -> dict[str, Any]:
    """작업 상태와 현재 처리 단계를 관리자 화면에 반환한다."""
    params = (status, status)
    with connect() as conn:
        total = conn.execute(
            "SELECT count(*) AS count FROM ingestion_jobs WHERE (%s::text IS NULL OR status=%s)",
            params,
        ).fetchone()["count"]
        items = conn.execute(
            """SELECT j.job_id, j.version_id, a.asset_id, a.title, v.source_filename,
                      j.status, j.attempts, j.max_attempts, j.chunk_count,
                      j.report->>'processing_stage' AS processing_stage,
                      j.report->>'progress_detail' AS progress_detail,
                      j.error_detail, j.created_at, j.updated_at
                 FROM ingestion_jobs j
                 JOIN asset_versions v USING(version_id)
                 JOIN assets a USING(asset_id)
                WHERE (%s::text IS NULL OR j.status=%s)
                ORDER BY j.created_at DESC LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        ).fetchall()
    return {"items": items, "total": int(total)}
