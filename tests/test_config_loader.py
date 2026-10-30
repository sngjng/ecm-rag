"""단일 Profile YAML 선택과 환경 변수 치환 테스트."""
import pytest

from service.config import _expand_env, reload_settings


def test_profile_is_selected_directly(monkeypatch):
    monkeypatch.setenv("RAG_PROFILE", "unit-test")
    settings = reload_settings()
    assert settings.runtime.environment == "unit-test"
    assert settings.models.llm.provider == "stub"
    assert settings.models.embedding.provider == "deterministic_stub"
    assert ".pptx" in settings.ingestion.allowed_extensions["document"]
    assert settings.solutions.easyocr_device == "cpu"


def test_environment_expression_supports_default(monkeypatch):
    monkeypatch.delenv("ECM_RAG_TEST_VALUE", raising=False)
    assert _expand_env("${ECM_RAG_TEST_VALUE:-fallback}") == "fallback"


def test_incident_log_context_budget_can_be_overridden(monkeypatch):
    monkeypatch.setenv("RAG_PROFILE", "unit-test")
    monkeypatch.setenv("RAG_INCIDENT_LOG_CONTEXT_CHARACTERS", "32000")

    settings = reload_settings()

    assert settings.incident_guidance.max_log_context_characters == 32000


def test_missing_required_environment_variable_is_rejected(monkeypatch):
    monkeypatch.delenv("ECM_RAG_REQUIRED", raising=False)
    with pytest.raises(RuntimeError, match="ECM_RAG_REQUIRED"):
        _expand_env("${ECM_RAG_REQUIRED}")


def test_invalid_profile_name_is_rejected(monkeypatch):
    monkeypatch.setenv("RAG_PROFILE", "../settings")
    with pytest.raises(ValueError, match="RAG_PROFILE"):
        reload_settings()
