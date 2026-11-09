"""PostgreSQL 경량 GraphRAG 결과를 문서 조회 화면용 구성도로 변환한다."""
from __future__ import annotations

import re
from typing import Any

TYPE_LABELS = {
    "system": "시스템", "server": "서버", "application": "애플리케이션",
    "api": "API", "database": "데이터베이스", "network": "네트워크",
    "actor": "업무 주체", "process": "처리 단계", "component": "구성요소",
    "other": "기타",
}

RELATION_LABELS = {
    "calls": "호출", "sends_to": "전송", "reads_from": "조회",
    "writes_to": "저장", "depends_on": "의존", "contains": "포함",
    "deployed_on": "배포", "connects_to": "연결",
    "authenticates_with": "인증", "routes_to": "라우팅",
    "precedes": "선행", "related_to": "관련",
}


def _id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(value))[:80]


def build_document_diagram(graph: dict[str, Any]) -> dict[str, Any] | None:
    """DB에서 검증된 엔터티·관계만 사용해 안전한 화면 DTO를 만든다."""
    entities = graph.get("entities") if isinstance(graph, dict) else None
    relations = graph.get("relations") if isinstance(graph, dict) else None
    if not isinstance(entities, list) or not isinstance(relations, list):
        return None
    if len(entities) < 2 or not relations:
        return None

    entity_ids = {str(entity["entity_id"]) for entity in entities}
    groups: list[dict[str, str]] = []
    group_ids: dict[str, str] = {}
    nodes: list[dict[str, str]] = []
    for entity in entities:
        properties = entity.get("properties")
        group_label = ""
        if isinstance(properties, dict):
            group_label = str(properties.get("group") or "").strip()
        if not group_label:
            group_label = TYPE_LABELS.get(str(entity.get("entity_type")), "구성요소")
        if group_label not in group_ids:
            group_id = f"group-{len(group_ids) + 1}"
            group_ids[group_label] = group_id
            groups.append({"id": group_id, "label": group_label[:60]})
        nodes.append({
            "id": _id(entity["entity_id"]),
            "label": str(entity.get("canonical_name") or "구성요소")[:80],
            "group": group_ids[group_label],
            "detail": str(entity.get("description") or "")[:160],
        })

    edges: list[dict[str, str]] = []
    for relation in relations:
        source = str(relation.get("source_entity_id"))
        target = str(relation.get("target_entity_id"))
        if source not in entity_ids or target not in entity_ids or source == target:
            continue
        relation_type = str(relation.get("relation_type") or "related_to")
        description = str(relation.get("description") or "").strip()
        relation_label = RELATION_LABELS.get(relation_type, relation_type)
        # 긴 설명 문장은 Mermaid 화살표를 겹치게 만든다. 짧은 업무 라벨만 직접
        # 표시하고 상세 설명은 LLM 답변 및 그래프 문맥에 그대로 보존한다.
        edge_label = description if 0 < len(description) <= 24 else relation_label
        edges.append({
            "from": _id(source),
            "to": _id(target),
            "label": edge_label[:30],
        })
    if not edges:
        return None
    diagram_type = "flow" if all(
        str(item.get("relation_type")) == "precedes" for item in relations
    ) else "architecture"
    return {
        "type": diagram_type,
        "title": "문서 관계 구성도" if diagram_type == "architecture" else "문서 처리 흐름",
        "description": "업로드 시 원문 청크에서 추출해 PostgreSQL에 저장한 엔터티와 관계입니다.",
        "groups": groups,
        "nodes": nodes,
        "edges": edges,
    }


def graph_context_text(graph: dict[str, Any]) -> str:
    """LLM 답변 근거에 추가할 관계 문장을 만든다."""
    entities = {
        str(item["entity_id"]): str(item.get("canonical_name") or "")
        for item in graph.get("entities", [])
    }
    lines: list[str] = []
    for relation in graph.get("relations", []):
        source = entities.get(str(relation.get("source_entity_id")))
        target = entities.get(str(relation.get("target_entity_id")))
        if not source or not target:
            continue
        relation_type = str(relation.get("relation_type") or "related_to")
        label = RELATION_LABELS.get(relation_type, relation_type)
        description = str(relation.get("description") or "").strip()
        line = f"- {source} → {target} ({label})"
        if description:
            line += f": {description}"
        lines.append(line)
    if not lines:
        return ""
    return "[PostgreSQL 지식 그래프 관계]\n" + "\n".join(lines[:50])
