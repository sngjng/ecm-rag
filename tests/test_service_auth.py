"""로컬 개발용 로그인 계정과 운영 profile 분리를 검증한다."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from service import auth as auth_service
from service.auth import authenticate
from service.config import get_settings, reload_settings
from service.schemas import EmployeeIdentity


@pytest.fixture(autouse=True)
def local_auth_profile(monkeypatch):
    monkeypatch.setenv("RAG_PROFILE", "local")
    reload_settings()
    yield
    get_settings.cache_clear()


def test_local_test_account_authenticates():
    identity = authenticate("test", "test")
    assert identity.employee_id == "test"
    assert identity.user_id == "test"
    assert identity.employee_name == "테스트 사용자"
    assert identity.department == "IDC본부"


def test_local_test_account_rejects_wrong_password():
    with pytest.raises(HTTPException) as error:
        authenticate("test", "wrong")
    assert error.value.status_code == 401


def test_python_module_provider_dispatches_to_login_module(monkeypatch):
    """FastAPI 인증 gateway가 향후 login.py 구현 함수를 호출하는지 확인한다."""
    authentication = SimpleNamespace(provider="python_module")
    settings = SimpleNamespace(
        security=SimpleNamespace(authentication=authentication)
    )
    monkeypatch.setattr(auth_service, "get_settings", lambda: settings)

    expected = EmployeeIdentity(
        employee_id="E10001",
        employee_name="테스트 사원",
        department="IDC본부",
        roles=["uploader"],
    )

    def fake_authenticate(
        employee_id: str, password: str, client_ip: str | None
    ) -> EmployeeIdentity:
        assert employee_id == "E10001"
        assert password == "secret"
        assert client_ip == "192.168.1.50"
        return expected

    monkeypatch.setattr(
        "service.login.authenticate_employee", fake_authenticate
    )
    assert auth_service.authenticate("E10001", "secret", "192.168.1.50") == expected


def test_eai_normal_response_maps_to_session_identity():
    """제공된 정상 응답의 사번·성명·부서·상위부서를 모두 보존한다."""
    from service.login import EaiLoginResult, _identity_from_result

    identity = _identity_from_result(
        EaiLoginResult(
            transaction_error_code="00000000",
            return_type="OK",
            return_code="UZZ16001",
            return_message="로그인 되었습니다.",
            user_id="IB1020",
            employee_number="IB1020",
            user_name="오최나",
            department_name="아키텍쳐팀",
            upper_department_name="ACS센터",
        )
    )

    assert identity.employee_id == "IB1020"
    assert identity.user_id == "IB1020"
    assert identity.employee_name == "오최나"
    assert identity.department == "아키텍쳐팀"
    assert identity.upper_department == "ACS센터"
    assert identity.roles == ["uploader"]


def test_eai_rejected_response_returns_401():
    """EAI 거래 오류 또는 return_type 실패는 일반화된 인증 실패로 반환한다."""
    from service.login import EaiLoginResult, _identity_from_result

    with pytest.raises(HTTPException) as error:
        _identity_from_result(
            EaiLoginResult(
                transaction_error_code="AUTH0001",
                return_type="FAIL",
                return_code="",
                return_message="internal message",
                user_id="",
                employee_number="",
                user_name="",
                department_name="",
                upper_department_name="",
            )
        )
    assert error.value.status_code == 401
    assert "internal message" not in str(error.value.detail)


def test_admin_dependency_requires_admin_role(monkeypatch):
    """인증 활성 상태의 일반 업로더는 관리자 API를 사용할 수 없다."""
    from service import dependencies

    monkeypatch.setattr(
        dependencies,
        "authorize",
        lambda request: {"employee_id": "E10001", "roles": ["uploader"]},
    )
    with pytest.raises(HTTPException) as error:
        dependencies.authorize_admin(object())
    assert error.value.status_code == 403


def test_admin_dependency_accepts_only_admin(monkeypatch):
    """관리자 역할만 관리자 API를 사용할 수 있고 임시 업로더도 예외가 아니다."""
    from service import dependencies

    admin = {"employee_id": "E00001", "roles": ["admin"]}
    monkeypatch.setattr(dependencies, "authorize", lambda request: admin)
    assert dependencies.authorize_admin(object()) == admin

    temporary = {"employee_id": "temporary", "roles": ["uploader"], "temporary_access": True}
    monkeypatch.setattr(dependencies, "authorize", lambda request: temporary)
    with pytest.raises(HTTPException) as error:
        dependencies.authorize_admin(object())
    assert error.value.status_code == 403


def test_uploader_dependency_allows_uploader_and_admin(monkeypatch):
    """업로더 기능은 업로더와 관리자에게만 허용한다."""
    from service import dependencies

    for role in ("uploader", "admin"):
        user = {"employee_id": "E10001", "roles": [role]}
        monkeypatch.setattr(dependencies, "authorize", lambda request, value=user: value)
        assert dependencies.authorize_uploader(object()) == user

    monkeypatch.setattr(
        dependencies,
        "authorize",
        lambda request: {"employee_id": "E10002", "roles": ["viewer"]},
    )
    with pytest.raises(HTTPException) as error:
        dependencies.authorize_uploader(object())
    assert error.value.status_code == 403
