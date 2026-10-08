"""PostgreSQL 기반 영속 ingestion queue와 처리 결과 repository.

별도 message broker 없이 DB row를 queue로 사용한다. 여러 worker가 동시에 실행돼도
``FOR UPDATE SKIP LOCKED``로 한 작업을 한 worker만 가져가며, lease/heartbeat로
비정상 종료 작업을 다시 회수한다.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import numpy as np
from psycopg.types.json import Jsonb

from service.db import connect
from service.parsers import Item


def get_job(job_id: UUID | str) -> dict[str, Any] | None:
    """worker 처리에 필요한 Job, Version, Asset 문맥을 한 번에 조회한다."""
    with connect() as conn:
        return conn.execute(
            """SELECT j.*, v.asset_id, v.version_label, v.source_filename, v.source_path,
                      v.repository, v.branch, v.logical_path, v.lifecycle_status, v.is_current,
                      a.asset_type, a.subtype, a.title, a.vendor, a.product, a.system_name,
                      a.service_name, a.component, a.security_level, a.metadata AS asset_metadata
               FROM ingestion_jobs j
               JOIN asset_versions v USING(version_id)
               JOIN assets a USING(asset_id)
               WHERE j.job_id=%s AND a.deleted_at IS NULL""",
            (UUID(str(job_id)),),
        ).fetchone()


def claim_jobs(worker_id: str, *, limit: int, lease_seconds: int) -> list[dict[str, Any]]:
    """대기 작업 또는 lease가 만료된 작업을 여러 워커 사이에서 원자적으로 확보한다."""
    with connect() as conn:
        # lease가 만료됐고 최대 시도 횟수까지 사용한 작업은 더 이상 재획득하지 않고
        # failed로 종결한다. 이 UPDATE와 아래 claim은 같은 트랜잭션에서 실행된다.
        conn.execute(
            """UPDATE ingestion_jobs
               SET status='failed', error_detail='worker lease 만료 및 최대 재시도 초과',
                   completed_at=now(), updated_at=now()
               WHERE status='processing'
                 AND heartbeat_at < now() - (%s * interval '1 second')
                 AND attempts >= max_attempts""",
            (lease_seconds,),
        )
        # SKIP LOCKED는 다른 worker가 잡은 row를 기다리지 않고 다음 후보를 선택한다.
        # attempts는 claim 시점에 증가하므로 프로세스 강제 종료도 재시도 횟수에 포함된다.
        return conn.execute(
            """WITH candidates AS (
                 SELECT job_id
                 FROM ingestion_jobs
                 WHERE (
                   status='queued'
                   OR (status='processing' AND heartbeat_at < now() - (%s * interval '1 second'))
                 )
                 AND attempts < max_attempts
                 ORDER BY priority, created_at
                 FOR UPDATE SKIP LOCKED
                 LIMIT %s
               )
               UPDATE ingestion_jobs AS j
               SET status='processing', worker_id=%s, locked_at=now(), heartbeat_at=now(),
                   attempts=j.attempts+1, started_at=coalesce(j.started_at, now()), updated_at=now()
               FROM candidates c WHERE j.job_id=c.job_id
               RETURNING j.*""",
            (lease_seconds, limit, worker_id),
        ).fetchall()


def heartbeat(job_id: UUID | str, worker_id: str) -> bool:
    """처리 중 lease를 연장한다. 소유 worker가 달라지면 갱신하지 않는다."""
    with connect() as conn:
        row = conn.execute(
            """UPDATE ingestion_jobs SET heartbeat_at=now(), updated_at=now()
               WHERE job_id=%s AND status='processing' AND worker_id=%s RETURNING job_id""",
            (UUID(str(job_id)), worker_id),
        ).fetchone()
        return row is not None


def complete_job(
    job_id: UUID | str,
    worker_id: str,
    items: list[Item],
    vectors: list[list[float]],
    report: dict[str, Any],
    embedding_model: str,
) -> None:
    """모든 검색 청크와 구조화 레코드를 한 트랜잭션에 넣고 작업을 완료한다."""
    if len(items) != len(vectors):
        raise ValueError("청크와 임베딩 수가 다릅니다")
    with connect() as conn:
        # Job row를 잠그고 현재 worker가 실제 소유자인지 다시 확인한다. lease를 잃은
        # worker가 늦게 결과를 덮어쓰는 것을 방지한다.
        job = conn.execute(
            """SELECT j.version_id, v.asset_id, a.system_name
               FROM ingestion_jobs j JOIN asset_versions v USING(version_id)
               JOIN assets a USING(asset_id)
               WHERE j.job_id=%s AND j.status='processing' AND j.worker_id=%s FOR UPDATE""",
            (UUID(str(job_id)), worker_id),
        ).fetchone()
        if not job:
            raise RuntimeError("처리 중인 작업을 찾을 수 없습니다")
        asset_id, version_id = job["asset_id"], job["version_id"]
        # 재시도 시 이전 부분 결과가 남지 않도록 version 단위로 교체한다. 아래 청크,
        # 오류 이벤트, 심볼, job 완료 갱신은 모두 같은 트랜잭션으로 commit된다.
        conn.execute("DELETE FROM chunks WHERE version_id=%s", (version_id,))
        for sequence, (item, vector) in enumerate(zip(items, vectors)):
            chunk_id = f"{version_id}:{sequence:06d}"
            previous_id = f"{version_id}:{sequence - 1:06d}" if sequence else None
            next_id = f"{version_id}:{sequence + 1:06d}" if sequence + 1 < len(items) else None
            heading = item.metadata.get("heading_path") or item.metadata.get("heading")
            if isinstance(heading, list):
                heading = " > ".join(str(value) for value in heading)
            conn.execute(
                """INSERT INTO chunks
                   (chunk_id, asset_id, version_id, chunk_seq, kind, display_content,
                    embedding_content, parent_id, previous_chunk_id, next_chunk_id,
                    page_start, page_end, line_start, line_end, identifier, heading_path,
                    metadata, embedding_model, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    chunk_id, asset_id, version_id, sequence, item.kind, item.text,
                    item.embedding_text or item.text, item.parent_id, previous_id, next_id,
                    item.page_start, item.page_end, item.line_start, item.line_end,
                    item.identifier, heading, Jsonb(item.metadata), embedding_model,
                    np.asarray(vector, dtype=np.float32),
                ),
            )
            # 로그 파서가 구조화한 오류는 원문 청크와 별도 정규화 테이블에 함께 저장한다.
            if item.error:
                event_id = uuid4()
                error = item.error
                conn.execute(
                    """INSERT INTO error_events
                       (error_event_id, asset_id, version_id, chunk_id, occurred_at, error_code,
                        exception_class, severity, message, root_cause, raw_trace, fingerprint,
                        system_name)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        event_id, asset_id, version_id, chunk_id, error.get("occurred_at"),
                        error.get("error_code"), error.get("exception_class"),
                        error.get("severity"), error.get("message"), error.get("root_cause"),
                        error["raw_trace"], error["fingerprint"], job["system_name"],
                    ),
                )
                for frame_sequence, frame in enumerate(error.get("frames", [])):
                    conn.execute(
                        """INSERT INTO stack_frames
                           (error_event_id, frame_seq, class_name, method_name, file_name,
                            line_number, is_application_code)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            event_id, frame_sequence, frame.get("class_name"),
                            frame.get("method_name"), frame.get("file_name"),
                            frame.get("line_number"), frame.get("is_application_code"),
                        ),
                    )
            # 소스 파서가 찾은 클래스/함수 심볼은 파일·라인 탐색용 테이블에 저장한다.
            if item.symbol:
                symbol = item.symbol
                conn.execute(
                    """INSERT INTO code_symbols
                       (symbol_id, asset_id, version_id, chunk_id, repository, branch, file_path,
                        language, symbol_type, package_name, class_name, symbol_name, signature,
                        start_line, end_line)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        uuid4(), asset_id, version_id, chunk_id, symbol.get("repository"),
                        symbol.get("branch"), symbol["file_path"], symbol["language"],
                        symbol["symbol_type"], symbol.get("package_name"),
                        symbol.get("class_name"), symbol["symbol_name"], symbol.get("signature"),
                        symbol["start_line"], symbol["end_line"],
                    ),
                )
        conn.execute(
            """UPDATE asset_versions SET indexed_at=now(), parser_name=%s, metadata=%s
               WHERE version_id=%s""",
            (report.get("parser", "ecm-rag"), Jsonb(report), version_id),
        )
        conn.execute(
            """UPDATE ingestion_jobs SET status='completed', chunk_count=%s, report=%s,
               completed_at=now(), heartbeat_at=now(), error_detail=NULL, updated_at=now()
               WHERE job_id=%s""",
            (len(items), Jsonb(report), UUID(str(job_id))),
        )


def fail_job(job_id: UUID | str, worker_id: str, detail: str) -> str:
    """재시도 여유가 있으면 queued로 복귀하고, 소진했으면 failed로 종료한다."""
    with connect() as conn:
        row = conn.execute(
            """UPDATE ingestion_jobs
               SET status=CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                   error_detail=%s, worker_id=NULL, locked_at=NULL,
                   completed_at=CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
                   updated_at=now()
               WHERE job_id=%s AND worker_id=%s
               RETURNING status""",
            (detail[:4000], UUID(str(job_id)), worker_id),
        ).fetchone()
        return row["status"] if row else "unknown"


def retry_job(job_id: UUID | str) -> dict[str, Any] | None:
    """운영자가 최종 실패/취소 작업을 명시적으로 재접수할 때 상태를 초기화한다."""
    with connect() as conn:
        return conn.execute(
            """UPDATE ingestion_jobs SET status='queued', attempts=0, worker_id=NULL,
                   locked_at=NULL, heartbeat_at=NULL, error_detail=NULL, completed_at=NULL,
                   updated_at=now()
               WHERE job_id=%s AND status IN ('failed','cancelled') RETURNING *""",
            (UUID(str(job_id)),),
        ).fetchone()
