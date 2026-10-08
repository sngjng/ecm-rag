"""FastAPI 공통 의존성."""
from __future__ import annotations

import hmac
from fastapi import Header, HTTPException
from service.config import get_settings


def authorize(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().security.api_key
    if not expected:
        raise HTTPException(503, "security.api_key 또는 RAG_API_KEY 설정이 필요합니다")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "잘못된 API 키")
