"""API 프로세스와 독립 실행하는 PostgreSQL queue ingestion worker."""
from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from service.config import get_settings
from service.drm import decrypt
from service.embedding import embed
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
        item.embedding_text = "\n\n".join(
            value for value in (prefix, item.embedding_text or item.text) if value
        )
    return items


def _parse(job: dict, source: Path, output: Path) -> tuple[list[Item], dict]:
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
        return parse_code(
            source,
            job.get("repository") or "",
            job.get("branch") or "",
            job.get("logical_path") or job["source_filename"],
        )
    raise ValueError(f"지원하지 않는 asset_type: {asset_type}")


def process(job_id: str, worker_id: str) -> None:
    settings = get_settings()
    job = jobs.get_job(job_id)
    if not job or job["status"] != "processing" or job["worker_id"] != worker_id:
        return
    raw = Path(job["source_path"])
    if not raw.resolve().is_relative_to(settings.paths.upload_root):
        jobs.fail_job(job_id, worker_id, "허용하지 않은 파일 경로")
        return

    output = settings.paths.artifact_root / str(job["version_id"])
    clear = output / ("decrypted" + raw.suffix)
    heartbeat_interval = max(10.0, settings.worker.lease_seconds / 3)
    try:
        with lease_heartbeat(job_id, worker_id, heartbeat_interval):
            output.mkdir(parents=True, exist_ok=True)
            source = decrypt(raw, clear)
            items, report = _parse(job, source, output)
            if not items:
                raise ValueError("검색 가능한 내용을 추출하지 못했습니다")
            _add_asset_context(items, job)
            vectors = embed([item.embedding_text or item.text for item in items])
            report.update({
                "parser": settings.solutions.document_parser,
                "embedding_model": settings.models.embedding.model,
                "profile": settings.runtime.environment,
            })
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
    settings = get_settings()
    settings.paths.upload_root.mkdir(parents=True, exist_ok=True)
    settings.paths.artifact_root.mkdir(parents=True, exist_ok=True)
    worker_id = settings.worker.worker_name or f"{socket.gethostname()}:{os.getpid()}"
    poll = poll_seconds if poll_seconds is not None else settings.worker.poll_seconds
    logger.info("worker started id=%s profile=%s", worker_id, settings.runtime.environment)
    while True:
        claimed = jobs.claim_jobs(
            worker_id,
            limit=settings.worker.claim_batch_size,
            lease_seconds=settings.worker.lease_seconds,
        )
        for job in claimed:
            process(str(job["job_id"]), worker_id)
        time.sleep(poll)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll", type=float, default=None)
    arguments = parser.parse_args()
    run(arguments.poll)
