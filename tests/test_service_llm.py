"""근거 기반 LLM 요청 조립의 네트워크 비의존 단위 테스트."""
from types import SimpleNamespace

from service.llm import _build_messages, _chat_completions_url, _format_context
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
