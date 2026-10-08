"""FastAPI 공통 의존성."""
from __future__ import annotations

import hmac
from fastapi import Header, HTTPException
from service.config import get_settings


def authorize(x_api_key: str | None = Header(default=None)) -> None:
    """보호된 router에 공통 적용하는 API key 인증 의존성.

    키 미설정은 인증을 우회하지 않고 503으로 처리한다. 입력 키 비교에는 일반 문자열
    비교 대신 constant-time 비교를 사용해 timing 정보 노출을 줄인다.
    """
    expected = get_settings().security.api_key
    if not expected:
        raise HTTPException(503, "security.api_key 또는 RAG_API_KEY 설정이 필요합니다")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "잘못된 API 키")
