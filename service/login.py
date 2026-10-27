"""사내 EAI Java VO를 호출하는 FastAPI 로그인 구현.

이 모듈은 사용자가 제공한 JPype 기반 ``login.py``의 호출 규격을 서비스 구조에 맞게
정리한 것이다. FastAPI route는 :func:`authenticate_employee`만 호출하며, JVM 경로와
Java 라이브러리 위치는 선택된 ``config/profiles/*.yaml``과 환경변수에서 읽는다.

보안 원칙:

* 비밀번호와 암호화 결과를 로그·DB·HTTP 응답에 기록하지 않는다.
* 사번·성명·부서·상위부서만 세션용 ``EmployeeIdentity``로 변환한다.
* Java/EAI 상세 예외는 서버 로그에 남기되 클라이언트에는 일반화한 오류만 반환한다.
* JPype JVM은 프로세스당 한 번만 시작하고 Java 호출을 잠금으로 직렬화한다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import HTTPException

from service.config import get_settings
from service.schemas import EmployeeIdentity


logger = logging.getLogger(__name__)
_JAVA_LOGIN_LOCK = Lock()

# 환경별 경로가 다르면 RAG_AUTH_CLASSPATH로 전체 classpath를 대체할 수 있다.
_DEFAULT_JARS = (
    "jackson-databind-2.10.2.jar",
    "jackson-core-2.10.2.jar",
    "jackson-annotations-2.10.2.jar",
    "eai-app.jar",
    "jef-application-parser-jdk5-0.1.jar",
    "JsonConverter-0.1.jar",
    "json-simple-1.1.jar",
)


@dataclass(frozen=True)
class EaiLoginResult:
    """Java ``ZZUL0002VO``에서 인증 판정과 세션에 필요한 값만 추출한 결과."""

    transaction_error_code: str
    return_type: str
    return_code: str
    return_message: str
    user_id: str
    employee_number: str
    user_name: str
    department_name: str
    upper_department_name: str


def _as_text(value: Any) -> str:
    """Java null과 Python None을 빈 문자열로 정규화한다."""
    return "" if value is None else str(value).strip()


def _getter(target: Any, method_name: str) -> str:
    """Java getter 결과를 문자열로 변환하고 필드 누락은 빈 값으로 처리한다."""
    method = getattr(target, method_name, None)
    if not callable(method):
        return ""
    return _as_text(method())


def _classpath() -> list[str]:
    """설정된 classpath 또는 표준 JAR 목록을 JPype 입력 배열로 만든다."""
    settings = get_settings().security.authentication
    if settings.java_classpath.strip():
        entries = [item.strip() for item in settings.java_classpath.split(os.pathsep)]
        return [item for item in entries if item]
    if not settings.java_base_dir.strip():
        raise HTTPException(503, "사내 로그인 Java 디렉터리가 설정되지 않았습니다")
    base_dir = Path(settings.java_base_dir).expanduser()
    return [str(base_dir / name) for name in _DEFAULT_JARS] + [str(base_dir)]


def _validate_java_environment(classpath: list[str]) -> tuple[Path, Path]:
    """JVM과 Java 리소스 경로를 EAI 호출 전에 검증한다."""
    settings = get_settings().security.authentication
    if not settings.jvm_path.strip():
        raise HTTPException(503, "사내 로그인 JVM 경로가 설정되지 않았습니다")
    if not settings.java_base_dir.strip():
        raise HTTPException(503, "사내 로그인 Java 디렉터리가 설정되지 않았습니다")

    jvm_path = Path(settings.jvm_path).expanduser()
    base_dir = Path(settings.java_base_dir).expanduser()
    missing = [Path(item) for item in classpath if not Path(item).exists()]
    if not jvm_path.is_file() or not base_dir.is_dir() or missing:
        logger.error(
            "login Java environment is incomplete jvm_exists=%s base_dir_exists=%s "
            "missing_classpath_entries=%s",
            jvm_path.is_file(),
            base_dir.is_dir(),
            [path.name for path in missing],
        )
        raise HTTPException(503, "사내 로그인 Java 실행 환경이 준비되지 않았습니다")
    return jvm_path, base_dir


def _load_jpype():
    """인증 비활성 환경도 기동할 수 있도록 JPype를 실제 로그인 시점에 가져온다."""
    try:
        import jpype
    except ImportError as exc:
        raise HTTPException(503, "JPype1 로그인 라이브러리가 설치되지 않았습니다") from exc
    return jpype


def _execute_eai_login(
    employee_id: str, password: str, remote_addr: str
) -> EaiLoginResult:
    """사용자 제공 코드와 동일한 Java VO/EAI 호출을 수행한다."""
    settings = get_settings().security.authentication
    jpype = _load_jpype()
    classpath = _classpath()
    jvm_path, base_dir = _validate_java_environment(classpath)

    # Java class, property, JAR는 모두 java_base_dir 또는 명시적 classpath에서
    # classloader로 찾는다. FastAPI 전체에 영향을 주는 os.chdir()는 수행하지 않는다.
    with _JAVA_LOGIN_LOCK:
        try:
            if not jpype.isJVMStarted():
                logger.info(
                    "initializing login JVM classpath_entries=%s java_dir=%s",
                    len(classpath),
                    base_dir,
                )
                jpype.startJVM(jvmpath=str(jvm_path), classpath=classpath)

            outbound = jpype.JClass(settings.outbound_process_class)()
            encrypt_util = jpype.JClass(settings.encrypt_util_class)()
            request_vo = jpype.JClass(settings.request_vo_class)()
            response_vo = jpype.JClass(settings.response_vo_class)()

            outbound.setUserId(employee_id)
            request_vo.setUserId(employee_id)
            # 사내 규격에 따라 대문자로 변환한 뒤 Java에서 암호화한다. 평문과 암호화
            # 결과는 어떤 로그에도 남기지 않는다.
            request_vo.setPassword(encrypt_util.encrypt(password.upper()))
            request_vo.setSystemId(settings.system_id)
            request_vo.setClientIp(remote_addr)
            outbound.call(settings.interface_id, request_vo, response_vo)

            java_user = response_vo.getUserVO()
            return EaiLoginResult(
                transaction_error_code=_getter(outbound, "getErrorCode"),
                return_type=_getter(response_vo, "getReturnType"),
                return_code=_getter(response_vo, "getReturnCd"),
                return_message=_getter(response_vo, "getReturnMsg"),
                user_id=_getter(java_user, "getUserId") if java_user else "",
                employee_number=_getter(java_user, "getEmpNo") if java_user else "",
                user_name=_getter(java_user, "getUserName") if java_user else "",
                department_name=_getter(java_user, "getDeptName") if java_user else "",
                upper_department_name=(
                    _getter(java_user, "getUpDeptName") if java_user else ""
                ),
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "EAI login call failed employee_id=%s error_type=%s",
                employee_id,
                type(exc).__name__,
            )
            raise HTTPException(503, "사내 로그인 시스템을 호출할 수 없습니다") from exc


def _identity_from_result(result: EaiLoginResult) -> EmployeeIdentity:
    """정상 ``ZZUL0002VO``를 애플리케이션 세션 사원정보로 변환한다."""
    if result.transaction_error_code != "00000000" or result.return_type != "OK":
        raise HTTPException(401, "사번 또는 비밀번호가 올바르지 않습니다")

    # 정상 사례에서는 user_id와 emp_no가 동일하지만 값이 달라도 두 항목을 보존한다.
    # 세션의 employee_id에는 인사 기준 키인 emp_no를 우선 사용한다.
    employee_number = result.employee_number or result.user_id
    user_id = result.user_id or employee_number
    if not employee_number or not user_id or not result.user_name:
        logger.error(
            "EAI login response missing identity employee=%s user_id_present=%s "
            "employee_number_present=%s user_name_present=%s",
            employee_number or user_id or "unknown",
            bool(result.user_id),
            bool(result.employee_number),
            bool(result.user_name),
        )
        raise HTTPException(502, "사내 로그인 응답에 필수 사원정보가 없습니다")

    return EmployeeIdentity(
        employee_id=employee_number,
        user_id=user_id,
        employee_name=result.user_name,
        department=result.department_name,
        upper_department=result.upper_department_name,
        roles=["uploader"],
    )


def authenticate_employee(
    employee_id: str, password: str, remote_addr: str | None = None
) -> EmployeeIdentity:
    """사내 계정을 검증하고 FastAPI 세션에 저장할 사원정보를 반환한다."""
    client_ip = remote_addr or "127.0.0.1"
    result = _execute_eai_login(employee_id, password, client_ip)
    identity = _identity_from_result(result)
    logger.info(
        "EAI login succeeded employee_id=%s department=%s upper_department=%s",
        identity.employee_id,
        identity.department,
        identity.upper_department,
    )
    return identity
