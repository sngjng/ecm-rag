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

    def fake_authenticate(employee_id: str, password: str) -> EmployeeIdentity:
        assert employee_id == "E10001"
        assert password == "secret"
        return expected

    monkeypatch.setattr(
        "service.login.authenticate_employee", fake_authenticate
    )
    assert auth_service.authenticate("E10001", "secret") == expected


def test_unimplemented_login_module_returns_service_unavailable():
    """미구현 상태에서 임의 계정을 허용하지 않고 명시적인 503을 반환한다."""
    from service.login import authenticate_employee

    with pytest.raises(HTTPException) as error:
        authenticate_employee("employee", "password")
    assert error.value.status_code == 503


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


def test_admin_dependency_accepts_admin_and_temporary_mode(monkeypatch):
    """관리자 역할과 명시적인 임시 인증 모드는 관리자 API를 사용할 수 있다."""
    from service import dependencies

    admin = {"employee_id": "E00001", "roles": ["admin"]}
    monkeypatch.setattr(dependencies, "authorize", lambda request: admin)
    assert dependencies.authorize_admin(object()) == admin

    temporary = {"employee_id": "temporary", "roles": ["uploader"], "temporary_access": True}
    monkeypatch.setattr(dependencies, "authorize", lambda request: temporary)
    assert dependencies.authorize_admin(object()) == temporary
