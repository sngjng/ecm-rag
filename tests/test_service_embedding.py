"""Embedding HTTP 재시도와 응답 검증의 네트워크 비의존 테스트."""
from types import SimpleNamespace

import httpx

import service.embedding as embedding_module
from service.embedding import embed


def test_embedding_retries_retryable_http_status(monkeypatch):
    settings = SimpleNamespace(
        provider="openai_compatible",
        base_url="http://embedding.local/v1/embeddings",
        api_key="",
        model="test-embedding",
        dimension=2,
        batch_size=8,
        max_input_characters=100,
        timeout_seconds=10,
        max_attempts=3,
        retry_backoff_seconds=0,
        retry_max_backoff_seconds=0,
        http_max_connections=4,
        http_max_keepalive_connections=2,
    )

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def post(self, url, json):
            self.calls += 1
            request = httpx.Request("POST", url)
            if self.calls == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(
                200,
                request=request,
                json={"data": [{"index": 0, "embedding": [0.6, 0.8]}]},
            )

    client = FakeClient()
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: SimpleNamespace(models=SimpleNamespace(embedding=settings)),
    )
    monkeypatch.setattr(embedding_module, "get_http_client", lambda *args: client)
    monkeypatch.setattr(embedding_module, "sleep_before_retry", lambda *args: None)

    assert embed(["검색 질문"]) == [[0.6, 0.8]]
    assert client.calls == 2
