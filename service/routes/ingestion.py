"""파일 접수와 처리 작업 endpoint. 실제 파싱은 별도 worker가 수행한다."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from psycopg.errors import UniqueViolation
from service.config import get_settings
from service.dependencies import authorize
from service.repositories import assets, jobs
from service.schemas import AssetCreate


router = APIRouter(prefix="/ingestion", tags=["ingestion"], dependencies=[Depends(authorize)])


@router.post("/uploads", status_code=status.HTTP_202_ACCEPTED)
async def upload(
    file: UploadFile = File(...), asset_type: str = Form(...), asset_id: str = Form(""),
    subtype: str = Form("general"), title: str = Form(""), vendor: str = Form(""),
    product: str = Form(""), system_name: str = Form(""), service_name: str = Form(""),
    component: str = Form(""), owner_org: str = Form(""),
    security_level: str = Form("internal"), version_label: str = Form("1"),
    lifecycle_status: str = Form(""), is_current: bool = Form(True),
    repository: str = Form(""), branch: str = Form(""), logical_path: str = Form(""),
):
    settings = get_settings()
    lifecycle_status = lifecycle_status or settings.ingestion.default_lifecycle_status
    suffix = Path(file.filename or "").suffix.lower()
    allowed = settings.ingestion.allowed_extensions
    if asset_type not in allowed or suffix not in set(allowed[asset_type]):
        raise HTTPException(422, "허용하지 않은 자산 유형/확장자 조합")
    if lifecycle_status not in {"draft", "approved", "obsolete"}:
        raise HTTPException(422, "lifecycle_status가 올바르지 않습니다")
    if asset_type == "source_code" and not repository.strip():
        raise HTTPException(422, "소스코드는 repository가 필요합니다")
    if logical_path and (
        logical_path.startswith("/") or ".." in Path(logical_path).parts or "\\" in logical_path
    ):
        raise HTTPException(422, "logical_path는 안전한 상대 경로만 허용합니다")

    name = re.sub(r"[^\w.\-가-힣]", "_", Path(file.filename or "upload").name)[:160]
    if not name or name.startswith("."):
        raise HTTPException(422, "파일명이 올바르지 않습니다")
    settings.paths.upload_root.mkdir(parents=True, exist_ok=True)
    directory = settings.paths.upload_root / str(uuid4())
    directory.mkdir(mode=0o700)
    target = directory / name
    digest, size = hashlib.sha256(), 0
    created_asset_id: UUID | None = None

    def cleanup_upload() -> None:
        target.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
        if created_asset_id:
            try:
                assets.soft_delete_asset(created_asset_id)
            except Exception:
                # 원래 업로드 오류를 보존한다. DB 장애 정리는 운영 로그/점검에서 처리한다.
                pass

    try:
        with target.open("xb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.server.max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, "용량 제한 초과")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise HTTPException(422, "빈 파일")
        if asset_id:
            try:
                resolved_asset_id = UUID(asset_id)
            except ValueError as exc:
                raise HTTPException(422, "asset_id 형식이 올바르지 않습니다") from exc
            existing = assets.get_asset(resolved_asset_id)
            if not existing:
                raise HTTPException(404, "asset을 찾을 수 없습니다")
            if existing["asset_type"] != asset_type:
                raise HTTPException(409, "기존 asset_type과 업로드 유형이 다릅니다")
        else:
            created = assets.create_asset(AssetCreate(
                asset_type=asset_type, subtype=subtype, title=title or name,
                vendor=vendor or None, product=product or None,
                system_name=system_name or None, service_name=service_name or None,
                component=component or None, owner_org=owner_org or None,
                security_level=security_level,
            ))
            resolved_asset_id = created["asset_id"]
            created_asset_id = resolved_asset_id
        version, job = assets.create_version_and_job(
            asset_id=resolved_asset_id, version_label=version_label, source_filename=name,
            source_path=str(target), sha256=digest.hexdigest(),
            lifecycle_status=lifecycle_status, is_current=is_current,
            effective_from=None, effective_to=None, repository=repository, branch=branch,
            logical_path=logical_path, max_attempts=settings.worker.max_attempts,
        )
        return {"asset_id": resolved_asset_id, "version_id": version["version_id"],
                "job_id": job["job_id"], "status": job["status"], "size": size}
    except UniqueViolation as exc:
        cleanup_upload()
        raise HTTPException(409, "동일한 원본 파일 버전이 이미 등록되어 있습니다") from exc
    except Exception:
        cleanup_upload()
        raise
    finally:
        await file.close()


@router.get("/jobs/{job_id}")
def get_job(job_id: UUID):
    result = jobs.get_job(job_id)
    if not result:
        raise HTTPException(404, "처리 작업을 찾을 수 없습니다")
    hidden = {"source_path", "worker_id", "locked_at", "heartbeat_at"}
    return {key: value for key, value in result.items() if key not in hidden}


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: UUID):
    result = jobs.retry_job(job_id)
    if not result:
        raise HTTPException(409, "실패 또는 취소된 작업만 재시도할 수 있습니다")
    return {"job_id": result["job_id"], "status": result["status"]}
