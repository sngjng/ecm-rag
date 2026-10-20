"""API 프로세스와 독립 실행하는 PostgreSQL queue ingestion worker.

업무 흐름은 ``claim → heartbeat → DRM → parse → context 보강 → embedding → DB commit``
순서다. API와 프로세스를 분리했기 때문에 대용량 PDF나 모델 지연이 HTTP 요청 시간을
점유하지 않으며 worker 수를 독립적으로 조절할 수 있다.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Callable

from service.config import get_settings
from service.drm import decrypt
from service.embedding import embed
from service.jar_parser import parse_jar
from service.logging_setup import configure_logging
from service.parsers import (
    Item, parse_code, parse_docx, parse_incident, parse_logs, parse_pdf, parse_text,
)
from service.repositories import jobs


logger = logging.getLogger(__name__)


@contextmanager
def lease_heartbeat(job_id: str, worker_id: str, interval_seconds: float):
    """긴 PDF 처리 중에도 lease가 만료되어 중복 실행되지 않도록 heartbeat를 갱신한다."""
    stop = threading.Event()

    def beat() -> None:
        # daemon thread는 주 처리 스레드가 종료되면 프로세스를 붙잡지 않는다. 정상
        # context 종료 시에는 stop event와 join으로 마지막 heartbeat를 정리한다.
        while not stop.wait(interval_seconds):
            try:
                if not jobs.heartbeat(job_id, worker_id):
                    logger.error("job lease lost job_id=%s worker=%s", job_id, worker_id)
                    return
            except Exception:
                logger.exception("job heartbeat failed job_id=%s", job_id)

    thread = threading.Thread(target=beat, name=f"heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=min(interval_seconds, 5.0))


def _add_asset_context(items: list[Item], job: dict) -> list[Item]:
    """ECM metadata와 원문을 합친 embedding text를 만들되 display text는 보존한다."""
    values = [
        ("Asset type", job.get("asset_type")), ("Subtype", job.get("subtype")),
        ("Title", job.get("title")), ("Vendor", job.get("vendor")),
        ("Product", job.get("product")), ("System", job.get("system_name")),
        ("Service", job.get("service_name")), ("Component", job.get("component")),
        ("Version", job.get("version_label")),
    ]
    prefix = "\n".join(f"[{name}] {value}" for name, value in values if value)
    for item in items:
        # 화면에 보여줄 원문 item.text는 바꾸지 않고 embedding_text에만 ECM 메타데이터를
        # 넣는다. 검색 품질과 사용자 인용 원문 보존을 동시에 만족시키기 위한 분리다.
        item.embedding_text = "\n\n".join(
            value for value in (prefix, item.embedding_text or item.text) if value
        )
    return items


def _parse(
    job: dict,
    source: Path,
    output: Path,
    progress: Callable[[str, str, int | None, int | None], None] | None = None,
) -> tuple[list[Item], dict]:
    """Asset 유형과 확장자에 맞는 파서를 선택한다.

    PDF만 Docling/OCR의 무거운 의존성을 지연 import하고, TXT·로그·소스는 가벼운
    표준 라이브러리 경로로 처리한다.
    """
    asset_type = job["asset_type"]
    suffix = source.suffix.lower()
    if asset_type == "document" and suffix == ".docx":
        return parse_docx(source)
    if asset_type == "document" and suffix == ".pdf":
        docling = get_settings().solutions.docling
        if docling.require_local_artifacts and not docling.artifacts_path:
            raise RuntimeError("현재 profile은 로컬 Docling artifact 경로가 필요합니다")
        return parse_pdf(source, output)
    if asset_type == "document":
        return parse_text(source)
    if asset_type == "incident":
        return parse_incident(source)
    if asset_type == "error_trace":
        return parse_logs(source)
    if asset_type == "source_code":
        if suffix == ".jar":
            return parse_jar(
                source,
                output,
                job.get("repository") or "",
                job.get("branch") or "",
                job.get("logical_path") or job["source_filename"],
                get_settings().solutions.jar_decompiler,
                progress,
            )
        return parse_code(
            source,
            job.get("repository") or "",
            job.get("branch") or "",
            job.get("logical_path") or job["source_filename"],
        )
    raise ValueError(f"지원하지 않는 asset_type: {asset_type}")


def process(job_id: str, worker_id: str) -> None:
    """worker가 claim한 작업 하나를 끝까지 처리한다.

    성공 시 청크와 구조화 레코드가 하나의 DB 트랜잭션으로 저장되고 completed가 된다.
    실패 시 원인 문자열을 기록하고 재시도 가능하면 queued, 소진하면 failed로 전환한다.
    """
    settings = get_settings()
    job = jobs.get_job(job_id)
    if not job or job["status"] != "processing" or job["worker_id"] != worker_id:
        return
    raw = Path(job["source_path"])
    # DB source_path가 조작됐더라도 설정된 업로드 루트 밖 파일은 절대 읽지 않는다.
    if not raw.resolve().is_relative_to(settings.paths.upload_root):
        jobs.fail_job(job_id, worker_id, "허용하지 않은 파일 경로")
        return

    output = settings.paths.artifact_root / str(job["version_id"])
    # DRM 사용 시에만 생성되는 평문 임시파일이다. finally에서 성공/실패와 무관하게
    # 삭제하며 canonical artifact와 DB 평문 보존 정책은 별도 접근통제로 관리한다.
    clear = output / ("decrypted" + raw.suffix)
    heartbeat_interval = max(10.0, settings.worker.lease_seconds / 3)
    try:
        with lease_heartbeat(job_id, worker_id, heartbeat_interval):
            def update_progress(
                stage: str,
                detail: str,
                processed_items: int | None = None,
                total_items: int | None = None,
            ) -> None:
                jobs.update_progress(
                    job_id,
                    worker_id,
                    stage=stage,
                    detail=detail,
                    processed_items=processed_items,
                    total_items=total_items,
                )
                logger.info(
                    "job progress job=%s stage=%s detail=%s processed=%s total=%s",
                    job_id,
                    stage,
                    detail,
                    processed_items,
                    total_items,
                )

            output.mkdir(parents=True, exist_ok=True)
            update_progress("decrypting", "DRM 적용 여부와 원본 파일을 확인하고 있습니다.")
            source = decrypt(raw, clear)
            update_progress("parsing", "자산 유형에 맞는 파서를 준비하고 있습니다.")
            items, report = _parse(job, source, output, update_progress)
            if not items:
                raise ValueError("검색 가능한 내용을 추출하지 못했습니다")
            _add_asset_context(items, job)
            update_progress(
                "embedding",
                f"검색 벡터를 생성하고 있습니다: 0/{len(items)}",
                0,
                len(items),
            )

            last_embedding_bucket = -1

            def embedding_progress(processed: int, total: int) -> None:
                nonlocal last_embedding_bucket
                # 대형 JAR에서도 DB 진행상황 갱신이 embedding 요청 수만큼 발생하지 않도록
                # 5% 단위(완료 포함)로 제한한다.
                bucket = 20 if processed >= total else int(processed * 20 / max(total, 1))
                if bucket == last_embedding_bucket:
                    return
                last_embedding_bucket = bucket
                update_progress(
                    "embedding",
                    f"검색 벡터를 생성하고 있습니다: {processed}/{total}",
                    processed,
                    total,
                )

            vectors = embed(
                [item.embedding_text or item.text for item in items],
                progress=embedding_progress,
            )
            report.update({
                "parser": settings.solutions.document_parser,
                "embedding_model": settings.models.embedding.model,
                "profile": settings.runtime.environment,
            })
            update_progress(
                "saving",
                f"PostgreSQL과 pgvector에 {len(items)}개 청크를 저장하고 있습니다.",
                len(items),
                len(items),
            )
            jobs.complete_job(
                job_id, worker_id, items, vectors, report, settings.models.embedding.model
            )
            logger.info("indexed job=%s chunks=%d", job_id, len(items))
    except Exception as exc:
        logger.exception("ingestion failed job=%s", job_id)
        status = jobs.fail_job(job_id, worker_id, f"{type(exc).__name__}: {exc}")
        logger.warning("job=%s transitioned=%s", job_id, status)
    finally:
        if clear.exists():
            clear.unlink()


def run(poll_seconds: float | None = None) -> None:
    """queue를 계속 poll하는 worker 프로세스 진입점."""
    settings = get_settings()
    settings.paths.upload_root.mkdir(parents=True, exist_ok=True)
    settings.paths.artifact_root.mkdir(parents=True, exist_ok=True)
    # 동일한 설정 이름으로 여러 프로세스를 실행해도 lease 소유자가 겹치지 않게 PID를
    # 항상 포함한다.
    worker_prefix = settings.worker.worker_name or socket.gethostname()
    worker_id = f"{worker_prefix}:{os.getpid()}"
    poll = poll_seconds if poll_seconds is not None else settings.worker.poll_seconds
    logger.info("worker started id=%s profile=%s", worker_id, settings.runtime.environment)
    while True:
        # 한 번에 가져오는 수는 profile의 claim_batch_size로 제한해 worker 하나가 queue를
        # 독점하지 않게 한다.
        claimed = jobs.claim_jobs(
            worker_id,
            limit=settings.worker.claim_batch_size,
            lease_seconds=settings.worker.lease_seconds,
        )
        for job in claimed:
            process(str(job["job_id"]), worker_id)
        time.sleep(poll)


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll", type=float, default=None)
    arguments = parser.parse_args()
    run(arguments.poll)
