"""기존 색인 문서의 청크로 PostgreSQL 경량 지식 그래프를 재구축한다.

신규 업로드는 worker가 자동 저장한다. 이 스크립트는 migration 009 적용 전에 이미
색인된 문서를 대상으로 사용한다. LLM 호출은 DB 트랜잭션 밖에서 수행하며, 문서별
그래프 저장만 짧은 트랜잭션으로 처리한다.
"""
from __future__ import annotations

import argparse
import logging
from typing import Any
from uuid import UUID

from service.config import get_settings
from service.db import connect
from service.graph_rag import extract_knowledge_graph
from service.parsers import Item
from service.repositories.knowledge_graph import replace_version_graph

logger = logging.getLogger(__name__)


def _targets(version_id: UUID | None, limit: int | None, force: bool) -> list[dict[str, Any]]:
    # 과거 버전이나 이관 데이터는 chunks가 정상 저장돼도 indexed_at이 NULL일 수 있다.
    # GraphRAG가 실제로 사용할 수 있는지는 인덱싱 시각이 아니라 원문 chunk 존재 여부로
    # 판단한다. 특히 --version-id로 지정한 정상 문서가 0건으로 제외되지 않아야 한다.
    conditions = [
        "EXISTS (SELECT 1 FROM chunks source_chunk "
        "WHERE source_chunk.version_id=v.version_id)",
        "a.deleted_at IS NULL",
    ]
    values: list[Any] = []
    if version_id is not None:
        conditions.append("v.version_id=%s")
        values.append(version_id)
    else:
        conditions.extend(["v.is_current", "v.lifecycle_status='approved'"])
    if not force:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM chunks c JOIN chunk_entities ce USING(chunk_id) "
            "WHERE c.version_id=v.version_id)"
        )
    limit_sql = ""
    if limit is not None:
        limit_sql = " LIMIT %s"
        values.append(limit)
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT v.version_id, a.title, v.source_filename
                 FROM asset_versions v JOIN assets a USING(asset_id)
                WHERE """ + " AND ".join(conditions)
            + " ORDER BY v.created_at" + limit_sql,
            tuple(values),
        ).fetchall()]


def _items(version_id: UUID) -> list[Item]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT kind, display_content, embedding_content, metadata
                 FROM chunks WHERE version_id=%s ORDER BY chunk_seq""",
            (version_id,),
        ).fetchall()
    return [
        Item(
            kind=row["kind"],
            text=row["display_content"],
            embedding_text=row["embedding_content"],
            metadata=dict(row["metadata"] or {}),
        )
        for row in rows
    ]


def backfill(*, version_id: UUID | None, limit: int | None, force: bool) -> None:
    settings = get_settings()
    if not settings.graph_rag.enabled:
        raise RuntimeError("graph_rag.enabled=false입니다. RAG_GRAPH_RAG_ENABLED를 확인하세요")
    targets = _targets(version_id, limit, force)
    logger.info("GraphRAG backfill targets=%d force=%s", len(targets), force)
    completed = 0
    for index, target in enumerate(targets, start=1):
        items = _items(target["version_id"])
        if not items:
            logger.warning("GraphRAG backfill skipped empty version=%s", target["version_id"])
            continue
        logger.info(
            "GraphRAG backfill extracting progress=%d/%d version=%s title=%s chunks=%d",
            index, len(targets), target["version_id"], target["title"], len(items),
        )
        graph = extract_knowledge_graph(items, settings.graph_rag)
        with connect() as conn:
            # 저장 직전 대상 version을 잠가 동시에 재색인되는 경우와 충돌하지 않는다.
            locked = conn.execute(
                "SELECT version_id FROM asset_versions WHERE version_id=%s FOR UPDATE",
                (target["version_id"],),
            ).fetchone()
            if not locked:
                logger.warning("GraphRAG backfill target disappeared version=%s", target["version_id"])
                continue
            replace_version_graph(conn, target["version_id"], graph)
        completed += 1
        logger.info(
            "GraphRAG backfill saved version=%s entities=%d relations=%d",
            target["version_id"], len(graph["entities"]), len(graph["relations"]),
        )
    logger.info("GraphRAG backfill completed=%d requested=%d", completed, len(targets))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="기존 문서 GraphRAG 메타데이터 재구축")
    parser.add_argument("--version-id", type=UUID, help="특정 version UUID만 처리")
    parser.add_argument("--limit", type=int, help="현재 승인 문서 처리 건수 제한")
    parser.add_argument("--force", action="store_true", help="기존 그래프가 있어도 다시 추출")
    arguments = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    backfill(
        version_id=arguments.version_id,
        limit=arguments.limit,
        force=arguments.force,
    )
