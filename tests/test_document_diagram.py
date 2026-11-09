"""저장된 지식 그래프의 화면 구성도와 LLM 관계 문맥 변환 테스트."""
from uuid import uuid4

from service.document_diagram import build_document_diagram, graph_context_text


def test_stored_graph_is_converted_to_diagram_and_context():
    database_id, api_id = uuid4(), uuid4()
    graph = {
        "entities": [
            {
                "entity_id": database_id,
                "entity_type": "database",
                "canonical_name": "통합 DB",
                "description": "원천 데이터",
                "properties": {"group": "내부망"},
            },
            {
                "entity_id": api_id,
                "entity_type": "api",
                "canonical_name": "Web API",
                "description": "외부 연계",
                "properties": {"group": "DMZ"},
            },
        ],
        "relations": [
            {
                "source_entity_id": database_id,
                "target_entity_id": api_id,
                "relation_type": "sends_to",
                "description": "전용선 전송",
            }
        ],
    }

    diagram = build_document_diagram(graph)

    assert diagram is not None
    assert [group["label"] for group in diagram["groups"]] == ["내부망", "DMZ"]
    assert diagram["edges"][0]["label"] == "전용선 전송"
    assert "통합 DB → Web API" in graph_context_text(graph)


def test_graph_without_relation_is_not_diagrammed():
    assert build_document_diagram({"entities": [], "relations": []}) is None


def test_long_relation_description_uses_short_relation_label():
    source_id, target_id = uuid4(), uuid4()
    diagram = build_document_diagram({
        "entities": [
            {"entity_id": source_id, "entity_type": "server", "canonical_name": "WAS"},
            {"entity_id": target_id, "entity_type": "database", "canonical_name": "DB"},
        ],
        "relations": [{
            "source_entity_id": source_id,
            "target_entity_id": target_id,
            "relation_type": "writes_to",
            "description": "대량 메시지 처리 결과와 상태 정보를 데이터베이스에 저장합니다.",
        }],
    })

    assert diagram is not None
    assert diagram["edges"][0]["label"] == "저장"
