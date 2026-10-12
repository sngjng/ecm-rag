"""FastAPI 공통 세션 인증 의존성."""
from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request

from service.config import get_settings
from service.repositories import auth


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authorize(request: Request) -> dict:
    """HttpOnly 쿠키의 세션 토큰을 검증하고 인증된 사원정보를 반환한다."""
    cookie_name = get_settings().security.authentication.cookie_name
    token = request.cookies.get(cookie_name)
    if not token:
        raise HTTPException(401, "로그인이 필요합니다")
    session = auth.get_active_session(token_digest(token))
    if not session:
        raise HTTPException(401, "로그인 세션이 만료되었거나 유효하지 않습니다")
    return session
