"""로컬 개발용 로그인 계정과 운영 profile 분리를 검증한다."""
import pytest
from fastapi import HTTPException

from service.auth import authenticate
from service.config import get_settings, reload_settings


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
