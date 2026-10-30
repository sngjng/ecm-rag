"""근거 기반 LLM 요청 조립의 네트워크 비의존 단위 테스트."""
from types import SimpleNamespace

import httpx

import service.llm as llm_module
from service.llm import _build_messages, _chat_completions_url, _format_context, generate_answer
from service.schemas import ChatHistoryMessage


def test_chat_completions_url_accepts_api_root_and_full_endpoint():
    assert _chat_completions_url("http://llm.local/v1") == (
        "http://llm.local/v1/chat/completions"
    )
    assert _chat_completions_url("http://llm.local/v1/chat/completions") == (
        "http://llm.local/v1/chat/completions"
    )


def test_format_context_numbers_sources_and_honors_character_limit():
    context = _format_context(
        [{
            "title": "운영 지침",
            "source_filename": "guide.pdf",
            "version_label": "2",
            "page_start": 3,
            "page_end": 4,
            "content": "장애 대응 절차 본문",
            "neighbor_context": ["인접 문맥"],
        }],
        max_characters=200,
    )

    assert context.startswith("[1] 제목: 운영 지침")
    assert "3-4쪽" in context
    assert "장애 대응 절차 본문" in context
    assert "인접 문맥" in context
    assert len(context) <= 200


def test_message_budget_limits_code_context_and_recent_history_for_8k_model():
    settings = SimpleNamespace(
        context_window_tokens=8192,
        max_tokens=1200,
        prompt_safety_tokens=1000,
        estimated_characters_per_token=1.0,
        max_history_characters=1500,
        max_context_characters=24000,
    )
    messages, budget = _build_messages(
        "결제 오류의 원인을 알려줘",
        [{
            "title": "결제 소스",
            "source_filename": "payment.jar",
            "version_label": "1",
            "content": "public class PaymentService {" + "x" * 30000 + "}",
            "neighbor_context": [],
        }],
        [
            ChatHistoryMessage(role="user", content="이전 질문" * 700),
            ChatHistoryMessage(role="assistant", content="이전 답변" * 700),
        ],
        settings,
    )

    self_contained_characters = sum(len(item["content"]) for item in messages)
    assert budget["prompt_character_budget"] == 5992
    assert budget["history_characters"] <= 1500
    assert budget["context_characters"] < 6000
    assert self_contained_characters <= budget["prompt_character_budget"]


def test_generate_answer_retries_transport_error(monkeypatch):
    settings = SimpleNamespace(
        provider="openai_compatible",
        base_url="http://llm.local/v1",
        api_key="",
        model="test-model",
        timeout_seconds=10,
        temperature=0.1,
        max_tokens=1200,
        context_top_k=8,
        max_context_characters=24000,
        context_window_tokens=8192,
        prompt_safety_tokens=1000,
        estimated_characters_per_token=1.0,
        max_history_characters=1500,
        max_continuation_attempts=2,
        continuation_tail_characters=2000,
        continuation_min_context_characters=1000,
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
                raise httpx.ConnectTimeout("timed out", request=request)
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "재시도 성공"}}]},
            )

    client = FakeClient()
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: SimpleNamespace(models=SimpleNamespace(llm=settings)),
    )
    monkeypatch.setattr(llm_module, "get_http_client", lambda *args: client)
    monkeypatch.setattr(llm_module, "sleep_before_retry", lambda *args: None)

    assert generate_answer("질문", [], []) == "재시도 성공"
    assert client.calls == 2


def test_generate_answer_continues_when_finish_reason_is_length(monkeypatch):
    settings = SimpleNamespace(
        provider="openai_compatible",
        base_url="http://llm.local/v1",
        api_key="",
        model="test-model",
        timeout_seconds=10,
        temperature=0.1,
        max_tokens=1200,
        context_top_k=8,
        max_context_characters=24000,
        context_window_tokens=8192,
        prompt_safety_tokens=1000,
        estimated_characters_per_token=1.0,
        max_history_characters=1500,
        max_continuation_attempts=2,
        continuation_tail_characters=2000,
        continuation_min_context_characters=1000,
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
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "choices": [{
                            "message": {"content": "## 1. 분석\n앞부분"},
                            "finish_reason": "length",
                        }],
                    },
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "message": {"content": "## 2. 조치\n뒷부분"},
                        "finish_reason": "stop",
                    }],
                },
            )

    client = FakeClient()
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: SimpleNamespace(models=SimpleNamespace(llm=settings)),
    )
    monkeypatch.setattr(llm_module, "get_http_client", lambda *args: client)

    answer = generate_answer("질문", [], [])

    assert "## 1. 분석\n앞부분" in answer
    assert "## 2. 조치\n뒷부분" in answer
    assert client.calls == 2


def test_continuation_keeps_only_answer_tail_and_preserves_rag_context(monkeypatch):
    settings = SimpleNamespace(
        provider="openai_compatible",
        base_url="http://llm.local/v1",
        api_key="",
        model="test-model",
        timeout_seconds=10,
        temperature=0.1,
        max_tokens=1800,
        context_top_k=8,
        max_context_characters=24000,
        context_window_tokens=8192,
        prompt_safety_tokens=1000,
        estimated_characters_per_token=1.0,
        max_history_characters=1500,
        max_continuation_attempts=3,
        continuation_tail_characters=2000,
        continuation_min_context_characters=1000,
        max_attempts=3,
        retry_backoff_seconds=0,
        retry_max_backoff_seconds=0,
        http_max_connections=4,
        http_max_keepalive_connections=2,
    )

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post(self, url, json):
            self.calls.append(json)
            request = httpx.Request("POST", url)
            if len(self.calls) == 1:
                return httpx.Response(
                    200,
                    request=request,
                    json={"choices": [{
                        "message": {"content": "앞" * 5000},
                        "finish_reason": "length",
                    }]},
                )
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{
                    "message": {"content": "완료"},
                    "finish_reason": "stop",
                }]},
            )

    client = FakeClient()
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: SimpleNamespace(models=SimpleNamespace(llm=settings)),
    )
    monkeypatch.setattr(llm_module, "get_http_client", lambda *args: client)

    result = generate_answer(
        "원인과 해결 절차",
        [{
            "title": "장애 지식",
            "source_filename": "guide.md",
            "version_label": "1",
            "content": "RAG-근거 " * 1000,
            "neighbor_context": [],
        }],
        [],
    )

    continuation_messages = client.calls[1]["messages"]
    assistant_tail = next(
        item["content"] for item in continuation_messages if item["role"] == "assistant"
    )
    continuation_user = next(
        item["content"] for item in continuation_messages if item["role"] == "user"
    )
    assert len(assistant_tail) <= settings.continuation_tail_characters + 30
    assert "RAG-근거" in continuation_user
    assert result.endswith("완료")
