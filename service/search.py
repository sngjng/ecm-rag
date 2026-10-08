"""PostgreSQL exact + FTS + pgvector 검색을 RRF와 선택적 reranker로 통합한다."""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np

from retrieval.rrf import reciprocal_rank_fusion
from service.config import get_settings
from service.db import connect
from service.embedding import embed


TOKEN = re.compile(r"[\w.$/-]{3,}", re.UNICODE)


@lru_cache(maxsize=2)
def get_reranker(path: str, use_fp16: bool):
    from retrieval.reranker import BgeReranker
    return BgeReranker(path, use_fp16=use_fp16)


def _filters(
    asset_type: str | None,
    system_name: str | None,
    vendor: str | None,
    product: str | None,
    include_obsolete: bool,
) -> tuple[str, tuple]:
    retrieval = get_settings().retrieval
    require_current = retrieval.current_versions_only and not include_obsolete
    require_approved = retrieval.approved_versions_only and not include_obsolete
    statement = """
      AND a.deleted_at IS NULL
      AND (%s IS NULL OR a.asset_type=%s)
      AND (%s IS NULL OR a.system_name=%s)
      AND (%s IS NULL OR a.vendor=%s)
      AND (%s IS NULL OR a.product=%s)
      AND (%s OR v.is_current)
      AND (%s OR v.lifecycle_status='approved')
    """
    return statement, (
        asset_type, asset_type, system_name, system_name, vendor, vendor, product, product,
        not require_current, not require_approved,
    )


def search(
    query: str,
    *,
    asset_type: str | None = None,
    system_name: str | None = None,
    vendor: str | None = None,
    product: str | None = None,
    include_obsolete: bool = False,
    limit: int | None = None,
) -> list[dict]:
    settings = get_settings()
    retrieval = settings.retrieval
    limit = limit or retrieval.default_limit
    limit = min(limit, retrieval.max_limit)
    filter_sql, filter_values = _filters(
        asset_type, system_name, vendor, product, include_obsolete
    )
    lanes: list[list[str]] = []

    with connect() as conn:
        conn.execute(
            "SELECT set_config('hnsw.ef_search', %s, true)",
            (str(settings.database.vector.hnsw_ef_search),),
        )
        # Error code, 예외명, 파일명, 클래스/메서드 같은 식별자는 exact/substring 검색을 우선한다.
        identifiers = [
            token for token in TOKEN.findall(query)
            if any(char.isdigit() for char in token) or "." in token or token[0].isupper()
        ]
        for token in identifiers[:8]:
            escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = conn.execute(
                """SELECT c.chunk_id
                   FROM chunks c JOIN assets a USING(asset_id)
                   JOIN asset_versions v USING(version_id)
                   WHERE (c.identifier = %s OR c.display_content ILIKE %s ESCAPE '\\')"""
                + filter_sql + " ORDER BY c.created_at DESC LIMIT %s",
                (token, f"%{escaped}%", *filter_values, retrieval.exact_top_k),
            ).fetchall()
            lanes.append([row["chunk_id"] for row in rows])

        rows = conn.execute(
            """SELECT c.chunk_id
               FROM chunks c JOIN assets a USING(asset_id)
               JOIN asset_versions v USING(version_id)
               WHERE c.search_vector @@ plainto_tsquery('simple', %s)"""
            + filter_sql
            + """ ORDER BY ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s)) DESC
                   LIMIT %s""",
            (query, *filter_values, query, retrieval.lexical_top_k),
        ).fetchall()
        lanes.append([row["chunk_id"] for row in rows])

        query_vector = np.asarray(embed([query])[0], dtype=np.float32)
        rows = conn.execute(
            """SELECT c.chunk_id
               FROM chunks c JOIN assets a USING(asset_id)
               JOIN asset_versions v USING(version_id)
               WHERE c.embedding IS NOT NULL"""
            + filter_sql + " ORDER BY c.embedding <=> %s LIMIT %s",
            (*filter_values, query_vector, retrieval.vector_top_k),
        ).fetchall()
        lanes.append([row["chunk_id"] for row in rows])

        fused = reciprocal_rank_fusion(lanes, k=retrieval.rrf_k)
        rrf_scores = dict(fused)
        ranked = [chunk_id for chunk_id, _ in fused[:retrieval.fusion_top_k]]
        if not ranked:
            return []
        found = conn.execute(
            """SELECT c.chunk_id, c.display_content AS content, c.kind, c.page_start, c.page_end,
                      c.line_start, c.line_end, c.heading_path, c.parent_id,
                      c.previous_chunk_id, c.next_chunk_id, c.metadata,
                      a.asset_id, a.asset_type, a.title, a.vendor, a.product, a.system_name,
                      v.version_id, v.version_label, v.source_filename, v.lifecycle_status
               FROM chunks c JOIN assets a USING(asset_id)
               JOIN asset_versions v USING(version_id)
               WHERE c.chunk_id = ANY(%s)""",
            (ranked,),
        ).fetchall()

        by_id = {row["chunk_id"]: row for row in found}
        ranked = [chunk_id for chunk_id in ranked if chunk_id in by_id]
        rerank_scores: dict[str, float] = {}
        reranker = settings.models.reranker
        if reranker.enabled and ranked:
            reranked = get_reranker(reranker.model_path, reranker.use_fp16).rerank(
                query, [by_id[chunk_id]["content"] for chunk_id in ranked],
                top_k=min(limit, reranker.top_k),
            )
            rerank_scores = {ranked[index]: score for index, score in reranked}
            ranked = [ranked[index] for index, _ in reranked]

        selected = ranked[:limit]
        neighbor_ids: set[str] = set()
        if retrieval.expand_neighbors:
            for chunk_id in selected:
                row = by_id[chunk_id]
                if row["previous_chunk_id"]:
                    neighbor_ids.add(row["previous_chunk_id"])
                if row["next_chunk_id"]:
                    neighbor_ids.add(row["next_chunk_id"])
        neighbors = {}
        if neighbor_ids:
            neighbor_rows = conn.execute(
                "SELECT chunk_id, display_content FROM chunks WHERE chunk_id=ANY(%s)",
                (list(neighbor_ids),),
            ).fetchall()
            neighbors = {row["chunk_id"]: row["display_content"] for row in neighbor_rows}

    results = []
    for chunk_id in selected:
        row = dict(by_id[chunk_id])
        row["rrf_score"] = rrf_scores[chunk_id]
        row["rerank_score"] = rerank_scores.get(chunk_id)
        row["neighbor_context"] = [
            neighbors[neighbor_id]
            for neighbor_id in (row["previous_chunk_id"], row["next_chunk_id"])
            if neighbor_id in neighbors
        ]
        results.append(row)
    return results
