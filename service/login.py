"""향후 사내 로그인 로직을 구현할 단일 확장 지점.

FastAPI endpoint(``POST /api/v1/auth/login``)는 직접 사내 시스템 세부사항을 알지 않고
이 모듈의 :func:`authenticate_employee`만 호출한다. 로그인 연동 시 이 함수 내부에
사내 API 호출, Python SDK 또는 Java bridge 호출을 구현하면 된다.

보안 계약:

* 입력 비밀번호를 DB, 파일 또는 로그에 저장하지 않는다.
* 성공 시 검증 가능한 사원정보를 ``EmployeeIdentity``로 반환한다.
* 잘못된 자격증명은 401, 연동 시스템 장애는 502/503으로 변환한다.
* 외부 시스템의 상세 오류나 응답 원문에 비밀번호·토큰이 포함될 수 있으므로 그대로
  로그 또는 HTTP 응답에 노출하지 않는다.

현재 ``RAG_AUTH_ENABLED=false``에서는 이 함수가 호출되지 않는다.
"""
from __future__ import annotations

from fastapi import HTTPException

from service.schemas import EmployeeIdentity


def authenticate_employee(employee_id: str, password: str) -> EmployeeIdentity:
    """사내 계정을 검증하고 사원정보를 반환한다.

    로그인 구현 시 이 함수의 본문만 교체한다. 함수명과 반환형은 FastAPI 연동 계약이므로
    유지한다. 임시 placeholder는 인증을 허용하지 않고 503으로 명확히 실패한다.
    """
    del employee_id, password
    raise HTTPException(503, "사내 로그인 모듈이 아직 구현되지 않았습니다")
