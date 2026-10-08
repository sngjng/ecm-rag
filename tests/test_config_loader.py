"""환경별 YAML deep-merge와 환경 변수 치환 테스트."""
import pytest
from service.config import _deep_merge, _expand_env


def test_profile_deep_merge_keeps_unmodified_nested_values():
    base = {"models": {"embedding": {"model": "bge-m3", "dimension": 1024}}}
    profile = {"models": {"embedding": {"model": "test-model"}}}
    result = _deep_merge(base, profile)
    assert result["models"]["embedding"] == {"model": "test-model", "dimension": 1024}


def test_environment_expression_supports_default(monkeypatch):
    monkeypatch.delenv("ECM_RAG_TEST_VALUE", raising=False)
    assert _expand_env("${ECM_RAG_TEST_VALUE:-fallback}") == "fallback"


def test_missing_required_environment_variable_is_rejected(monkeypatch):
    monkeypatch.delenv("ECM_RAG_REQUIRED", raising=False)
    with pytest.raises(RuntimeError, match="ECM_RAG_REQUIRED"):
        _expand_env("${ECM_RAG_REQUIRED}")
