"""사내 Java 인증 모듈과 FastAPI 사이의 최소 연동 경계.

Java 구현체는 wrapper 실행 파일 형태로 연결한다. wrapper는 stdin에서 다음 JSON을 받고
성공 시 stdout에 사원정보 JSON 하나만 출력해야 한다.

입력: {"employee_id":"...", "password":"..."}
출력: {"authenticated":true, "employee_id":"...", "employee_name":"...",
       "department":"...", "email":"...", "roles":["..."]}

비밀번호와 Java stderr/stdout 원문은 로그나 HTTP 응답에 포함하지 않는다.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from ipaddress import ip_address

from fastapi import HTTPException

from service.config import get_settings
from service.schemas import EmployeeIdentity


def normalize_client_ip(value: str | None) -> str | None:
    """DB inet 컬럼에 저장 가능한 실제 IP만 허용하고 임의 proxy 문자열은 버린다."""
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def authenticate(employee_id: str, password: str) -> EmployeeIdentity:
    """Java 사내 인증 결과를 검증된 사원정보 객체로 변환한다."""
    settings = get_settings().security.authentication

    # 자동화 테스트 전용 provider다. 운영 profile에서 사용하지 않는다.
    if settings.provider == "development_stub":
        # 로컬/자동화 시험 전용 계정이다. 운영 profile은 반드시 java_command를 사용한다.
        if employee_id != "test" or password != "test":
            raise HTTPException(401, "사번 또는 비밀번호가 올바르지 않습니다")
        return EmployeeIdentity(
            employee_id="test",
            employee_name="테스트 사용자",
            department="IDC본부",
            email="test@test.local",
            roles=["uploader"],
        )

    if not settings.java_command.strip():
        raise HTTPException(503, "사내 로그인 연동 모듈이 설정되지 않았습니다")

    command = shlex.split(settings.java_command, posix=os.name != "nt")
    payload = json.dumps(
        {"employee_id": employee_id, "password": password}, ensure_ascii=False
    )
    try:
        completed = subprocess.run(
            command,
            input=payload,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=settings.timeout_seconds,
            capture_output=True,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, "사내 로그인 모듈을 호출할 수 없습니다") from exc

    if completed.returncode != 0:
        raise HTTPException(401, "사번 또는 비밀번호가 올바르지 않습니다")
    try:
        result = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(502, "사내 로그인 모듈 응답 형식이 올바르지 않습니다") from exc
    if result.get("authenticated") is not True:
        raise HTTPException(401, "사번 또는 비밀번호가 올바르지 않습니다")

    try:
        return EmployeeIdentity.model_validate(result)
    except ValueError as exc:
        raise HTTPException(502, "사내 로그인 모듈에 필수 사원정보가 없습니다") from exc
