"""인증 사용자용 장애 로그 분석과 관리자 전용 템플릿 관리 endpoint."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from service.dependencies import authorize, authorize_admin
from service.incident_guidance import generate_incident_guide
from service.incident_report import build_incident_report
from service.markdown_viewer import render_markdown
from service.repositories import incident_guide as incident_repository
from service.schemas import (
    IncidentGuideRequest,
    IncidentReportRequest,
    IncidentTemplateUpdate,
)

router = APIRouter(prefix="/incident-guide", tags=["incident-guide"])


@router.get("/template")
def get_incident_template(user: Annotated[dict, Depends(authorize)]):
    """인증 사용자에게 현재 공용 출력 템플릿을 읽기 전용으로 제공한다."""
    del user
    template = incident_repository.get_template()
    if not template:
        raise HTTPException(503, "장애 해결 가이드 템플릿이 준비되지 않았습니다")
    return template


@router.put("/template")
def update_incident_template(
    payload: IncidentTemplateUpdate,
    user: Annotated[dict, Depends(authorize_admin)],
):
    """관리자만 공용 출력 템플릿을 변경할 수 있다."""
    employee_id = str(user.get("employee_id") or user.get("user_id") or "unknown")
    return incident_repository.update_template(payload.content, employee_id)


@router.post("/analyze")
def analyze_incident(
    payload: IncidentGuideRequest,
    user: Annotated[dict, Depends(authorize)],
):
    """로그와 자연어 질문을 분석하고 내부 근거를 숨긴 해결 가이드를 반환한다."""
    del user
    template = incident_repository.get_template()
    if not template:
        raise HTTPException(503, "장애 해결 가이드 템플릿이 준비되지 않았습니다")
    result = generate_incident_guide(payload, template["content"])
    result["answer_html"] = render_markdown(result.get("answer") or "")
    return result


@router.post("/report")
def download_incident_report(
    payload: IncidentReportRequest,
    _user: Annotated[dict, Depends(authorize)],
):
    """화면에 표시된 가이드 답변을 편집 가능한 Word 문서로 반환한다."""
    generated_at = datetime.now()
    content = build_incident_report(answer=payload.answer)
    filename = f"로그분석_조치가이드_{generated_at:%Y%m%d_%H%M%S}.docx"
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                "attachment; filename=incident-analysis-report.docx; "
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "Content-Length": str(len(content)),
        },
    )
