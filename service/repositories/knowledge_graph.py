"""PostgreSQL 경량 GraphRAG 엔터티·관계·원문 근거 repository."""
from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from service.db import connect


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()[:300]


def replace_version_graph(
    conn: Any,
    version_id: Any,
    graph: dict[str, Any],
) -> None:
    """현재 version 청크의 그래프 근거를 같은 완료 트랜잭션에 저장한다."""
    # worker 재처리뿐 아니라 별도 backfill에서도 version 단위 교체가 되도록 기존
    # 청크-엔터티와 관계 근거를 먼저 제거한다. 다른 version의 공용 엔터티는 보존된다.
    conn.execute(
        """DELETE FROM relation_evidence re USING chunks c
             WHERE re.chunk_id=c.chunk_id AND c.version_id=%s""",
        (version_id,),
    )
    conn.execute(
        """DELETE FROM chunk_entities ce USING chunks c
             WHERE ce.chunk_id=c.chunk_id AND c.version_id=%s""",
        (version_id,),
    )
    entity_ids: dict[str, Any] = {}
    for entity in graph.get("entities", []):
        properties = {"group": entity.get("group") or ""}
        row = conn.execute(
            """INSERT INTO knowledge_entities
               (entity_id, entity_type, canonical_name, normalized_name, description, properties)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (entity_type, normalized_name) DO UPDATE
                 SET canonical_name=EXCLUDED.canonical_name,
                     description=CASE WHEN EXCLUDED.description <> ''
                                      THEN EXCLUDED.description
                                      ELSE knowledge_entities.description END,
                     properties=knowledge_entities.properties || EXCLUDED.properties,
                     updated_at=now()
               RETURNING entity_id""",
            (
                uuid4(), entity["type"], entity["name"], _normalize_name(entity["name"]),
                entity.get("description") or "", Jsonb(properties),
            ),
        ).fetchone()
        entity_ids[entity["key"]] = row["entity_id"]
        for sequence in entity.get("chunk_seqs") or []:
            chunk_id = f"{version_id}:{sequence:06d}"
            conn.execute(
                """INSERT INTO chunk_entities (chunk_id, entity_id, mention_text)
                   VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                (chunk_id, row["entity_id"], entity["name"]),
            )

    for relation in graph.get("relations", []):
        source_id = entity_ids.get(relation["source"])
        target_id = entity_ids.get(relation["target"])
        if not source_id or not target_id or source_id == target_id:
            continue
        row = conn.execute(
            """INSERT INTO knowledge_relations
               (relation_id, source_entity_id, target_entity_id, relation_type,
                description, confidence)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (source_entity_id, target_entity_id, relation_type) DO UPDATE
                 SET description=CASE WHEN EXCLUDED.description <> ''
                                      THEN EXCLUDED.description
                                      ELSE knowledge_relations.description END,
                     confidence=greatest(knowledge_relations.confidence, EXCLUDED.confidence),
                     updated_at=now()
               RETURNING relation_id""",
            (
                uuid4(), source_id, target_id, relation["type"],
                relation.get("description") or "", relation.get("confidence", 1.0),
            ),
        ).fetchone()
        evidence_sequences = relation.get("chunk_seqs") or []
        for sequence in evidence_sequences:
            chunk_id = f"{version_id}:{sequence:06d}"
            conn.execute(
                """INSERT INTO relation_evidence (relation_id, chunk_id, evidence_text)
                   VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                (row["relation_id"], chunk_id, relation.get("description") or ""),
            )

    # 재처리로 근거가 사라진 관계와 어느 청크에도 등장하지 않는 엔터티를 정리한다.
    conn.execute(
        """DELETE FROM knowledge_relations r
             WHERE NOT EXISTS (
               SELECT 1 FROM relation_evidence e WHERE e.relation_id=r.relation_id
             )"""
    )
    conn.execute(
        """DELETE FROM knowledge_entities e
             WHERE NOT EXISTS (
               SELECT 1 FROM chunk_entities ce WHERE ce.entity_id=e.entity_id
             )
               AND NOT EXISTS (
                 SELECT 1 FROM knowledge_relations r
                  WHERE r.source_entity_id=e.entity_id OR r.target_entity_id=e.entity_id
               )"""
    )


def fetch_subgraph(
    chunk_ids: list[str], *, max_entities: int, max_relations: int
) -> dict[str, list[dict[str, Any]]]:
    """검색 결과 문서의 엔터티와 1-hop 관계를 원문 근거와 함께 반환한다.

    질문과 직접 일치한 청크가 문서의 설명 문단이고 실제 엔터티 mention은 구성도·표
    청크에 있을 수 있다. 따라서 검색 청크가 속한 version을 먼저 찾고 해당 version의
    그래프를 seed로 사용한다. 이 범위 확장이 있어야 답변과 문서 구성도가 함께 나온다.
    """
    if not chunk_ids:
        return {"entities": [], "relations": []}
    with connect() as conn:
        entities = conn.execute(
            """WITH matched_versions AS (
                 SELECT DISTINCT version_id FROM chunks WHERE chunk_id=ANY(%s)
               ), version_relations AS (
                 SELECT DISTINCT r.source_entity_id, r.target_entity_id
                   FROM knowledge_relations r
                   JOIN relation_evidence ev USING(relation_id)
                   JOIN chunks evidence_chunk ON evidence_chunk.chunk_id=ev.chunk_id
                   JOIN matched_versions mv ON mv.version_id=evidence_chunk.version_id
               ), seed AS (
                 SELECT DISTINCT ce.entity_id
                   FROM chunk_entities ce JOIN chunks c USING(chunk_id)
                   JOIN matched_versions mv USING(version_id)
               ), expanded AS (
                 SELECT entity_id FROM seed
                 UNION
                 SELECT r.target_entity_id FROM version_relations r JOIN seed s
                   ON r.source_entity_id=s.entity_id
                 UNION
                 SELECT r.source_entity_id FROM version_relations r JOIN seed s
                   ON r.target_entity_id=s.entity_id
               )
               SELECT e.entity_id, e.entity_type, e.canonical_name, e.description,
                      e.properties
                 FROM knowledge_entities e JOIN expanded x USING(entity_id)
                ORDER BY (e.entity_id IN (SELECT entity_id FROM seed)) DESC,
                         e.canonical_name
                LIMIT %s""",
            (chunk_ids, max_entities),
        ).fetchall()
        entity_ids = [row["entity_id"] for row in entities]
        if not entity_ids:
            return {"entities": [], "relations": []}
        relations = conn.execute(
            """WITH matched_versions AS (
                 SELECT DISTINCT version_id FROM chunks WHERE chunk_id=ANY(%s)
               )
               SELECT r.relation_id, r.source_entity_id, r.target_entity_id,
                      r.relation_type, r.description, r.confidence,
                      array_agg(DISTINCT ev.chunk_id) AS evidence_chunk_ids
                 FROM knowledge_relations r
                 JOIN relation_evidence ev USING(relation_id)
                 JOIN chunks evidence_chunk ON evidence_chunk.chunk_id=ev.chunk_id
                 JOIN matched_versions mv ON mv.version_id=evidence_chunk.version_id
                WHERE r.source_entity_id=ANY(%s) AND r.target_entity_id=ANY(%s)
                GROUP BY r.relation_id
                ORDER BY r.confidence DESC, r.updated_at DESC
                LIMIT %s""",
            (chunk_ids, entity_ids, entity_ids, max_relations),
        ).fetchall()
    return {
        "entities": [dict(row) for row in entities],
        "relations": [dict(row) for row in relations],
    }
