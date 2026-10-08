"""파일 접수와 처리 작업 HTTP API.

업로드 요청 안에서는 다음 일만 수행한다.

1. 자산 유형·확장자·경로·크기를 검증한다.
2. 원본을 서버의 격리된 업로드 디렉터리에 저장한다.
3. Asset/Version과 PostgreSQL ingestion job을 하나의 트랜잭션으로 등록한다.

DRM, 파싱, 청킹, 임베딩은 응답 이후 별도 ``service.worker`` 프로세스가 수행한다.
따라서 업로드 성공 응답은 처리 완료(200)가 아니라 작업 접수(202)를 의미한다.
"""
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
    """원본 파일을 저장하고 비동기 처리 작업을 등록한다.

    ``asset_id``가 비어 있으면 새 논리 Asset을 만들고, 값이 있으면 기존 Asset의 새
    Version으로 등록한다. 동일 Asset에서 ``is_current=true``인 버전은 DB 트랜잭션
    안에서 하나만 유지된다.
    """
    settings = get_settings()
    lifecycle_status = lifecycle_status or settings.ingestion.default_lifecycle_status

    # 자산 유형별 허용 확장자는 YAML에서 관리한다. 브라우저 accept 속성은 편의 기능일
    # 뿐이므로 서버에서 반드시 다시 검증한다.
    suffix = Path(file.filename or "").suffix.lower()
    allowed = settings.ingestion.allowed_extensions
    if asset_type not in allowed or suffix not in set(allowed[asset_type]):
        raise HTTPException(422, "허용하지 않은 자산 유형/확장자 조합")
    if lifecycle_status not in {"draft", "approved", "obsolete"}:
        raise HTTPException(422, "lifecycle_status가 올바르지 않습니다")
    if asset_type == "source_code" and not repository.strip():
        raise HTTPException(422, "소스코드는 repository가 필요합니다")

    # logical_path는 검색 결과에 표시할 논리 경로다. 절대경로나 상위 디렉터리 이동을
    # 허용하면 저장소 경계 밖 경로가 노출될 수 있으므로 안전한 상대경로만 받는다.
    if logical_path and (
        logical_path.startswith("/") or ".." in Path(logical_path).parts or "\\" in logical_path
    ):
        raise HTTPException(422, "logical_path는 안전한 상대 경로만 허용합니다")

    # 클라이언트가 보낸 전체 경로는 버리고 basename만 사용한다. 운영체제에서 의미가
    # 달라질 수 있는 문자는 '_'로 치환하고 파일명 길이도 제한한다.
    name = re.sub(r"[^\w.\-가-힣]", "_", Path(file.filename or "upload").name)[:160]
    if not name or name.startswith("."):
        raise HTTPException(422, "파일명이 올바르지 않습니다")
    settings.paths.upload_root.mkdir(parents=True, exist_ok=True)
    # 업로드마다 UUID 디렉터리를 사용해 동일 파일명 충돌과 다른 요청 간 접근을 막는다.
    directory = settings.paths.upload_root / str(uuid4())
    directory.mkdir(mode=0o700)
    target = directory / name
    digest, size = hashlib.sha256(), 0
    created_asset_id: UUID | None = None

    def cleanup_upload() -> None:
        """DB 등록 전후 오류가 발생했을 때 파일과 신규 Asset을 보상 정리한다."""
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
        # 스트리밍 저장으로 요청 파일 전체를 메모리에 올리지 않는다. 읽으면서 SHA-256과
        # 크기를 계산해 중복 버전 판별 및 무결성 추적에 사용한다.
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
            # 기존 Asset의 새 버전 등록 경로. UUID, 존재 여부, 자산 유형 일치를 확인한다.
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
            # 신규 Asset 경로. 파일 저장이 끝난 후 메타데이터 원장을 만든다.
            created = assets.create_asset(AssetCreate(
                asset_type=asset_type, subtype=subtype, title=title or name,
                vendor=vendor or None, product=product or None,
                system_name=system_name or None, service_name=service_name or None,
                component=component or None, owner_org=owner_org or None,
                security_level=security_level,
            ))
            resolved_asset_id = created["asset_id"]
            created_asset_id = resolved_asset_id
        # Version과 queue row는 repository의 단일 DB 트랜잭션으로 생성한다. 둘 중 하나가
        # 실패하면 모두 rollback되어 고아 Version/Job이 남지 않는다.
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
        # 같은 Asset에 동일 SHA-256 원본이 이미 있으면 중복 버전으로 판단한다.
        cleanup_upload()
        raise HTTPException(409, "동일한 원본 파일 버전이 이미 등록되어 있습니다") from exc
    except Exception:
        cleanup_upload()
        raise
    finally:
        await file.close()


@router.get("/jobs/{job_id}")
def get_job(job_id: UUID):
    """화면 polling용 작업 상태 조회. 내부 파일경로와 worker lease 정보는 숨긴다."""
    result = jobs.get_job(job_id)
    if not result:
        raise HTTPException(404, "처리 작업을 찾을 수 없습니다")
    hidden = {"source_path", "worker_id", "locked_at", "heartbeat_at"}
    return {key: value for key, value in result.items() if key not in hidden}


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: UUID):
    """최종 failed/cancelled 작업을 queued 상태와 시도 횟수 0으로 되돌린다."""
    result = jobs.retry_job(job_id)
    if not result:
        raise HTTPException(409, "실패 또는 취소된 작업만 재시도할 수 있습니다")
    return {"job_id": result["job_id"], "status": result["status"]}
