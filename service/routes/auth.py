"""임시 무인증 모드와 사내 Java 세션 인증을 함께 제공하는 HTTP API."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi import HTTPException
from psycopg.errors import UndefinedTable

from service.auth import authenticate, normalize_client_ip
from service.config import get_settings
from service.dependencies import authorize, temporary_user, token_digest
from service.repositories import access_roles, auth
from service.schemas import LoginRequest


router = APIRouter(prefix="/auth", tags=["auth"])


def _public_employee(session: dict) -> dict:
    """브라우저에 세션 토큰·내부 키를 제외한 사원정보만 반환한다."""
    return {
        "employee_id": session["employee_id"],
        "user_id": session.get("user_id") or session["employee_id"],
        "employee_name": session["employee_name"],
        "department": session.get("department") or "",
        "upper_department": session.get("upper_department") or "",
        "email": session.get("email") or "",
        "roles": session.get("roles") or [],
        "authentication_enabled": get_settings().security.authentication.enabled,
        "temporary_access": bool(session.get("temporary_access")),
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    """설정된 로그인 provider 성공 시 서버 세션과 HttpOnly 쿠키를 발급한다."""
    settings = get_settings().security.authentication
    if not settings.enabled:
        return _public_employee(temporary_user())
    client_ip = normalize_client_ip(request.client.host if request.client else None)
    identity = authenticate(
        payload.employee_id,
        payload.password.get_secret_value(),
        client_ip,
    )
    try:
        # Java/EAI는 신원 확인만 담당한다. 애플리케이션 권한은 PostgreSQL의 권한
        # 테이블에서 별도로 결정해 외부 인증 모듈과 업무 권한을 분리한다.
        identity = access_roles.resolve_identity_role(identity)
    except UndefinedTable as exc:
        raise HTTPException(
            503, "권한 관리 DB가 준비되지 않았습니다. bin/start.sh로 migration을 적용해 주세요."
        ) from exc
    token = secrets.token_urlsafe(48)
    try:
        session = auth.create_session(
            token_hash=token_digest(token),
            identity=identity,
            session_hours=settings.session_hours,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent", "")[:500] or None,
        )
    except UndefinedTable as exc:
        raise HTTPException(
            503, "로그인 세션 DB가 준비되지 않았습니다. bin/start.sh로 재기동해 주세요."
        ) from exc
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    return _public_employee(session)


@router.get("/me")
def me(current_user: dict = Depends(authorize)):
    return _public_employee(current_user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response):
    settings = get_settings().security.authentication
    if not settings.enabled:
        response.delete_cookie(settings.cookie_name, path="/")
        response.status_code = status.HTTP_204_NO_CONTENT
        return response
    token = request.cookies.get(settings.cookie_name)
    if token:
        auth.revoke_session(token_digest(token))
    response.delete_cookie(settings.cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
