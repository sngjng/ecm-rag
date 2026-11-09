"""경량 GraphRAG의 표본 선택·행 프로토콜·정규화 테스트."""
from types import SimpleNamespace

import httpx

import service.graph_rag as graph_module
from service.graph_rag import (
    _generate_graph_lines,
    _ollama_generate_url,
    _parse_graph_lines,
    _sample_item_indices,
    _select_item_indices,
    extract_knowledge_graph,
    normalize_graph,
)
from service.parsers import Item


def test_sample_indices_include_document_start_and_end():
    assert _sample_item_indices(100, 4) == [0, 33, 66, 99]


def test_architecture_and_ocr_chunks_are_prioritized_over_test_descriptions():
    items = [Item(kind="text", text=f"일반 설명 {index}") for index in range(30)]
    items[7] = Item(kind="text", text="성능 테스트 수행 및 모니터링")
    items[11] = Item(
        kind="text",
        text="외부망 Push Engine에서 DMZ WebtoB를 호출",
        metadata={"heading": "시스템 구성도"},
    )
    items[17] = Item(
        kind="text",
        text="이미지 OCR: 내부망 통합 메시징 DB 인터넷망 중계서버",
        metadata={"content_source": "image_ocr"},
    )

    selected = _select_item_indices(items, 6)

    assert 11 in selected
    assert 17 in selected
    assert 7 not in selected


def test_line_protocol_parses_entities_and_relations_without_json():
    payload = _parse_graph_lines(
        "E|push|Push Engine|application|외부망|3|푸시 처리\n"
        "E|web|WebtoB|server|DMZ|3,4|웹 게이트웨이\n"
        "R|push|web|sends_to|3|메시지 전송"
    )

    assert payload["entities"][1]["chunk_seqs"] == [3, 4]
    assert payload["relations"][0]["type"] == "sends_to"


def test_normalize_graph_filters_invalid_relation_and_inherits_evidence():
    payload = {
        "entities": [
            {"key": "a", "name": "A 서버", "type": "server", "chunk_seqs": [2]},
            {"key": "b", "name": "업무 API", "type": "api", "chunk_seqs": [3]},
        ],
        "relations": [
            {"source": "a", "target": "b", "type": "calls", "chunk_seqs": []},
            {"source": "a", "target": "missing", "type": "calls", "chunk_seqs": [2]},
        ],
    }

    graph = normalize_graph(payload, item_count=5, max_entities=10, max_relations=10)

    assert len(graph["relations"]) == 1
    assert graph["relations"][0]["chunk_seqs"] == [2, 3]


def test_ollama_extractor_uses_generate_without_json_grammar(monkeypatch):
    llm = SimpleNamespace(
        base_url="http://127.0.0.1:11434/v1",
        api_key="",
        model="qwen3-local-8k:latest",
        timeout_seconds=120,
        max_attempts=1,
        retry_backoff_seconds=0,
        retry_max_backoff_seconds=0,
        http_max_connections=4,
        http_max_keepalive_connections=2,
    )
    settings = SimpleNamespace(
        extraction_provider="ollama_generate",
        extraction_max_tokens=2400,
        max_entities=30,
        max_relations=40,
    )

    class FakeClient:
        def __init__(self):
            self.url = None
            self.payload = None

        def post(self, url, json):
            self.url = url
            self.payload = json
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"response": "E|a|A 서버|server|내부망|0|업무 서버"},
            )

    client = FakeClient()
    monkeypatch.setattr(
        graph_module,
        "get_settings",
        lambda: SimpleNamespace(models=SimpleNamespace(llm=llm)),
    )
    monkeypatch.setattr(graph_module, "get_http_client", lambda *args: client)

    result = _generate_graph_lines("[CHUNK 0]\nA 서버", settings)

    assert result.startswith("E|a|")
    assert client.url == "http://127.0.0.1:11434/api/generate"
    assert client.payload["think"] is False
    assert client.payload["raw"] is False
    assert "format" not in client.payload


def test_extract_knowledge_graph_uses_dedicated_line_extractor(monkeypatch):
    captured = {}

    def fake_generate(content, settings):
        captured["content"] = content
        return (
            "E|a|A 서버|server|내부망|0|업무 서버\n"
            "E|b|B API|api|DMZ|0|연계 API\n"
            "R|a|b|calls|0|API 호출"
        )

    monkeypatch.setattr(graph_module, "_generate_graph_lines", fake_generate)
    settings = SimpleNamespace(
        extraction_provider="ollama_generate",
        extraction_max_tokens=2400,
        extraction_max_chunks=20,
        extraction_max_characters=16000,
        max_entities=50,
        max_relations=80,
    )

    graph = extract_knowledge_graph(
        [Item(kind="text", text="A 서버가 B API를 호출")], settings
    )

    assert len(graph["entities"]) == 2
    assert len(graph["relations"]) == 1
    assert "[CHUNK 0]" in captured["content"]


def test_ollama_generate_url_is_derived_from_openai_root():
    assert _ollama_generate_url("http://127.0.0.1:11434/v1") == (
        "http://127.0.0.1:11434/api/generate"
    )
