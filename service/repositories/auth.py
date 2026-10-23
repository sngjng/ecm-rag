"""서버 세션의 PostgreSQL 저장·조회·폐기 repository."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from service.db import connect
from service.schemas import EmployeeIdentity


def create_session(
    *, token_hash: str, identity: EmployeeIdentity, session_hours: int,
    client_ip: str | None, user_agent: str | None,
) -> dict[str, Any]:
    """원문 세션 토큰 대신 SHA-256 해시와 사원정보 snapshot만 저장한다."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=session_hours)
    with connect() as conn:
        # 별도 scheduler 없이도 장기 운영 시 세션 테이블이 무한 증가하지 않게 한다.
        conn.execute(
            """DELETE FROM auth_sessions
                WHERE expires_at < now() - interval '7 days'
                   OR revoked_at < now() - interval '7 days'"""
        )
        return conn.execute(
            """INSERT INTO auth_sessions
               (session_id, token_hash, employee_id, user_id, employee_name,
                department, upper_department, email, roles, expires_at, client_ip,
                user_agent)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                uuid4(), token_hash, identity.employee_id,
                identity.user_id or identity.employee_id, identity.employee_name,
                identity.department or None, identity.upper_department or None,
                identity.email or None, Jsonb(identity.roles), expires_at, client_ip,
                user_agent,
            ),
        ).fetchone()


def get_active_session(token_hash: str) -> dict[str, Any] | None:
    """만료·로그아웃되지 않은 세션을 읽는다.

    상태 polling마다 DB UPDATE가 발생하지 않도록 인증 요청은 read-only로 유지한다.
    """
    with connect() as conn:
        return conn.execute(
            """SELECT session_id, employee_id, user_id, employee_name, department,
                      upper_department, email, roles, expires_at, last_seen_at
                 FROM auth_sessions
                WHERE token_hash=%s AND revoked_at IS NULL AND expires_at > now()""",
            (token_hash,),
        ).fetchone()


def revoke_session(token_hash: str) -> bool:
    """로그아웃 토큰을 재사용할 수 없도록 즉시 폐기한다."""
    with connect() as conn:
        row = conn.execute(
            """UPDATE auth_sessions SET revoked_at=now()
                WHERE token_hash=%s AND revoked_at IS NULL RETURNING session_id""",
            (token_hash,),
        ).fetchone()
        return row is not None
