"""사원별 화면/API 접근 권한 저장소.

권한 값은 ``viewer``, ``uploader``, ``admin`` 세 종류만 저장한다. 로그인 세션에는
로그인 시점의 권한 snapshot이 들어가므로 권한을 변경하거나 삭제할 때 해당 사원의
활성 세션을 폐기해 다음 로그인부터 새 권한이 확실히 적용되게 한다.
"""
from __future__ import annotations

from typing import Any

from service.config import get_settings
from service.db import connect
from service.schemas import EmployeeIdentity


ROLE_KEYS = ("viewer", "uploader", "admin")
ROLE_LABELS = {"viewer": "뷰어", "uploader": "업로더", "admin": "관리자"}


def _bootstrap_admin_ids() -> set[str]:
    """환경변수로 주입된 최초 관리자 사번을 대소문자 구분 없이 반환한다."""
    raw = get_settings().security.authentication.bootstrap_admin_employee_ids
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def resolve_identity_role(identity: EmployeeIdentity) -> EmployeeIdentity:
    """EAI 인증 사원의 최종 권한을 결정하고 최신 조직정보를 동기화한다.

    권한 등록이 없는 사원은 기본값인 업로더 권한을 받는다. 부트스트랩 관리자
    목록은 DB 장애 복구와 최초 구축을 위해 DB 값보다 우선한다.
    """
    if identity.employee_id.strip().upper() in _bootstrap_admin_ids():
        role = "admin"
    else:
        with connect() as conn:
            row = conn.execute(
                "SELECT role FROM user_access_roles WHERE employee_id=%s",
                (identity.employee_id,),
            ).fetchone()
            role = row["role"] if row else "uploader"
            if row:
                conn.execute(
                    """UPDATE user_access_roles
                          SET employee_name=%s, department=%s, upper_department=%s,
                              updated_at=now()
                        WHERE employee_id=%s""",
                    (
                        identity.employee_name,
                        identity.department or None,
                        identity.upper_department or None,
                        identity.employee_id,
                    ),
                )
    return identity.model_copy(update={"roles": [role]})


def list_roles(*, query: str | None, limit: int, offset: int) -> dict[str, Any]:
    pattern = f"%{query.strip()}%" if query and query.strip() else None
    params = (pattern, pattern, pattern, pattern)
    where = """(%s::text IS NULL OR employee_id ILIKE %s
                 OR employee_name ILIKE %s OR department ILIKE %s)"""
    with connect() as conn:
        total = conn.execute(
            f"SELECT count(*) AS count FROM user_access_roles WHERE {where}", params
        ).fetchone()["count"]
        rows = conn.execute(
            f"""SELECT employee_id, employee_name, department, upper_department, role,
                       updated_by, created_at, updated_at
                  FROM user_access_roles WHERE {where}
                 ORDER BY updated_at DESC, employee_id
                 LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        ).fetchall()
    return {"items": rows, "total": int(total)}


def upsert_role(employee_id: str, values: dict[str, Any], updated_by: str) -> dict[str, Any]:
    """권한을 등록/수정하고 기존 세션을 폐기한다."""
    employee_id = employee_id.strip()
    with connect() as conn:
        # 마지막 DB 관리자를 강등하면 부트스트랩 설정이 없는 환경에서 관리 화면을 다시
        # 열 수 없으므로 차단한다. 부트스트랩 관리자는 DB 행과 무관하게 항상 관리자다.
        current = conn.execute(
            "SELECT role FROM user_access_roles WHERE employee_id=%s FOR UPDATE",
            (employee_id,),
        ).fetchone()
        if current and current["role"] == "admin" and values["role"] != "admin":
            count = conn.execute(
                "SELECT count(*) AS count FROM user_access_roles WHERE role='admin'"
            ).fetchone()["count"]
            if count <= 1 and not _bootstrap_admin_ids():
                raise ValueError("마지막 관리자 권한은 변경할 수 없습니다")
        row = conn.execute(
            """INSERT INTO user_access_roles
                   (employee_id, employee_name, department, upper_department, role, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (employee_id) DO UPDATE SET
                   employee_name=excluded.employee_name,
                   department=excluded.department,
                   upper_department=excluded.upper_department,
                   role=excluded.role,
                   updated_by=excluded.updated_by,
                   updated_at=now()
               RETURNING *""",
            (
                employee_id, values.get("employee_name"), values.get("department"),
                values.get("upper_department"), values["role"], updated_by,
            ),
        ).fetchone()
        conn.execute(
            """UPDATE auth_sessions SET revoked_at=now()
                 WHERE employee_id=%s AND revoked_at IS NULL""",
            (employee_id,),
        )
        return row


def delete_role(employee_id: str) -> bool:
    """명시적 권한을 제거해 다음 로그인부터 업로더 권한으로 되돌린다."""
    with connect() as conn:
        current = conn.execute(
            "SELECT role FROM user_access_roles WHERE employee_id=%s FOR UPDATE",
            (employee_id,),
        ).fetchone()
        if not current:
            return False
        if current["role"] == "admin":
            count = conn.execute(
                "SELECT count(*) AS count FROM user_access_roles WHERE role='admin'"
            ).fetchone()["count"]
            if count <= 1 and not _bootstrap_admin_ids():
                raise ValueError("마지막 관리자 권한은 삭제할 수 없습니다")
        conn.execute("DELETE FROM user_access_roles WHERE employee_id=%s", (employee_id,))
        conn.execute(
            """UPDATE auth_sessions SET revoked_at=now()
                 WHERE employee_id=%s AND revoked_at IS NULL""",
            (employee_id,),
        )
        return True
