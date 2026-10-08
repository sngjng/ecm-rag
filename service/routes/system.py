"""로드밸런서와 운영 점검용 endpoint."""
from fastapi import APIRouter, HTTPException
from service.config import get_settings
from service.db import connect


router = APIRouter(prefix="/health", tags=["system"])


@router.get("/live")
def live():
    """프로세스가 HTTP 요청을 받을 수 있는지만 확인한다(DB 연결은 확인하지 않음)."""
    settings = get_settings()
    return {"status": "ok", "profile": settings.runtime.environment}


@router.get("/ready")
def ready():
    """트래픽을 받을 준비가 됐는지 PostgreSQL 왕복 질의로 확인한다."""
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        raise HTTPException(503, "PostgreSQL 연결 준비 안 됨") from exc
    return {"status": "ready", "database": "postgresql"}
