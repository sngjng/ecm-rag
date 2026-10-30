"""장애 해결 가이드 공용 출력 템플릿 repository."""
from __future__ import annotations

from typing import Any

from service.db import connect

TEMPLATE_KEY = "incident_resolution_guide"


def get_template() -> dict[str, Any] | None:
    """현재 공용 장애 해결 가이드 템플릿을 조회한다."""
    with connect() as conn:
        return conn.execute(
            """SELECT template_key, content, updated_by, updated_at
                 FROM response_templates
                WHERE template_key=%s""",
            (TEMPLATE_KEY,),
        ).fetchone()


def update_template(content: str, employee_id: str) -> dict[str, Any]:
    """관리자 수정 내용을 저장하고 최종 값을 반환한다."""
    with connect() as conn:
        return conn.execute(
            """INSERT INTO response_templates(template_key, content, updated_by, updated_at)
               VALUES (%s, %s, %s, now())
               ON CONFLICT (template_key) DO UPDATE
                 SET content=excluded.content,
                     updated_by=excluded.updated_by,
                     updated_at=excluded.updated_at
               RETURNING template_key, content, updated_by, updated_at""",
            (TEMPLATE_KEY, content, employee_id),
        ).fetchone()
