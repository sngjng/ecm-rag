"""FastAPI 공통 인증 의존성.

현재 임시 모드에서는 공통 사용자를 반환하고, 인증을 활성화하면 기존 HttpOnly 세션을
검증한다. 모든 route가 같은 dependency를 유지하므로 로그인 도입 시 누락을 방지한다.
"""
from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request

from service.config import get_settings
from service.repositories import auth


ROLE_ALIASES = {
    # 이전 배포 세션과의 호환. 신규 세션/DB에는 아래 표준 키만 저장한다.
    "user": "viewer",
    "general": "viewer",
    "viewer": "viewer",
    "operator": "uploader",
    "uploader": "uploader",
    "thanos": "admin",
    "admin": "admin",
}


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def temporary_user() -> dict:
    """인증 비활성 기간에 API와 업로드 감사 이력이 사용할 공통 사용자 정보."""
    settings = get_settings().security.authentication
    return {
        "employee_id": settings.temporary_employee_id,
        "user_id": settings.temporary_employee_id,
        "employee_name": settings.temporary_employee_name,
        "department": settings.temporary_department,
        "upper_department": "",
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
    """관리 화면/API는 관리자 역할만 허용한다."""
    user = authorize(request)
    roles = {ROLE_ALIASES.get(str(role).lower(), str(role).lower()) for role in (user.get("roles") or [])}
    if "admin" not in roles:
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return user


def authorize_uploader(request: Request) -> dict:
    """업로드·수정 API는 업로더 또는 관리자 역할만 허용한다."""
    user = authorize(request)
    roles = {ROLE_ALIASES.get(str(role).lower(), str(role).lower()) for role in (user.get("roles") or [])}
    if not roles.intersection({"uploader", "admin"}):
        raise HTTPException(403, "업로더 권한이 필요합니다")
    return user
