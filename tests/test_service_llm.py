"""근거 기반 LLM 요청 조립의 네트워크 비의존 단위 테스트."""
from service.llm import _chat_completions_url, _format_context


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
