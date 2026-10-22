"""FastAPI 공통 인증 의존성.

현재 임시 모드에서는 공통 사용자를 반환하고, 인증을 활성화하면 기존 HttpOnly 세션을
검증한다. 모든 route가 같은 dependency를 유지하므로 로그인 도입 시 누락을 방지한다.
"""
from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request

from service.config import get_settings
from service.repositories import auth


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def temporary_user() -> dict:
    """인증 비활성 기간에 API와 업로드 감사 이력이 사용할 공통 사용자 정보."""
    settings = get_settings().security.authentication
    return {
        "employee_id": settings.temporary_employee_id,
        "employee_name": settings.temporary_employee_name,
        "department": settings.temporary_department,
        "email": "",
        "roles": ["uploader"],
        "temporary_access": True,
    }


def authorize(request: Request) -> dict:
    """설정에 따라 임시 사용자 또는 검증된 세션 사원정보를 반환한다."""
    settings = get_settings().security.authentication
    # 사내 Java 로그인이 준비되기 전의 명시적 임시 모드다. route마다 인증 코드를
    # 제거하지 않아도 되므로 추후 enabled=true 한 줄로 원래 세션 검증을 복구할 수 있다.
    if not settings.enabled:
        return temporary_user()
    cookie_name = settings.cookie_name
    token = request.cookies.get(cookie_name)
    if not token:
        raise HTTPException(401, "로그인이 필요합니다")
    session = auth.get_active_session(token_digest(token))
    if not session:
        raise HTTPException(401, "로그인 세션이 만료되었거나 유효하지 않습니다")
    return session


def authorize_admin(request: Request) -> dict:
    """관리 API는 인증 활성화 시 ``admin`` 역할이 있는 세션만 허용한다.

    임시 인증 비활성 기간에는 화면 시험을 위해 접근을 허용한다. 이 기간에는 애플리케이션
    권한 검사가 없으므로 운영 방화벽·reverse proxy에서 관리자 PC 대역으로 제한해야 한다.
    """
    user = authorize(request)
    if user.get("temporary_access"):
        return user
    roles = {str(role).lower() for role in (user.get("roles") or [])}
    if "admin" not in roles:
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return user
