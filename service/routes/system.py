"""로드밸런서와 운영 점검용 endpoint."""
from fastapi import APIRouter, HTTPException
from service.config import get_settings
from service.db import connect


router = APIRouter(prefix="/health", tags=["system"])


@router.get("/live")
def live():
    settings = get_settings()
    return {"status": "ok", "profile": settings.runtime.environment}


@router.get("/ready")
def ready():
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        raise HTTPException(503, "PostgreSQL 연결 준비 안 됨") from exc
    return {"status": "ready", "database": "postgresql"}
