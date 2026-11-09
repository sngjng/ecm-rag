"""PostgreSQL exact + FTS + pgvector 검색을 통합하는 hybrid retrieval 서비스.

동일한 질의를 식별자, 전문검색, 의미검색 lane에 각각 보내고 RRF(Reciprocal Rank
Fusion)로 순위를 결합한다. 선택적으로 로컬 reranker를 마지막 단계에 적용한다.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any
from uuid import UUID

import numpy as np

from retrieval.rrf import reciprocal_rank_fusion
from service.config import get_settings
from service.db import connect
from service.embedding import embed

TOKEN = re.compile(r"[\w.$/-]{3,}", re.UNICODE)
STACK_FRAME = re.compile(
    r"^\s*(?:at\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\.([\w$<>]+)\s*\(",
    re.MULTILINE,
)


def _stack_frame_keys(query: str) -> list[tuple[str, list[str], list[str]]]:
    """질문에 붙여 넣은 Java stack frame을 package/class/method 검색 키로 바꾼다."""
    keys: list[tuple[str, list[str], list[str]]] = []
    seen: set[tuple[str, str]] = set()
    for match in STACK_FRAME.finditer(query):
        qualified_class, method = match.groups()
        if (qualified_class, method) in seen:
            continue
        seen.add((qualified_class, method))
        package, _, class_part = qualified_class.rpartition(".")
        # 디컴파일 소스의 inner class 표현은 AST에 따라 Outer 또는 Inner로 저장될 수 있다.
        class_names = list(dict.fromkeys(
            (class_part, class_part.split("$")[0], class_part.split("$")[-1])
        ))
        method_names = [method]
        if method in {"<init>", "<clinit>"}:
            method_names.extend(class_names)
        keys.append((package, class_names, list(dict.fromkeys(method_names))))
        if len(keys) >= 8:
            break
    return keys


def _qualified_vector_ranking(
    rows: list[dict], max_distance: float
) -> tuple[list[str], dict[str, float]]:
    """관련성 기준을 통과한 vector 후보와 진단용 거리를 반환한다.

    최근접 검색은 어떤 질문에도 반드시 결과를 돌려준다. 따라서 거리 제한이 없으면
    날씨·인사 같은 일반대화에도 무관한 사내 문서를 근거로 선택하게 된다.
    """
    distances = {
        row["chunk_id"]: float(row["vector_distance"])
        for row in rows
        if row.get("vector_distance") is not None
    }
    ranking = [
        row["chunk_id"]
        for row in rows
        if row.get("vector_distance") is not None
        and float(row["vector_distance"]) <= max_distance
    ]
    return ranking, distances


@lru_cache(maxsize=2)
def get_reranker(path: str, use_fp16: bool):
    """무거운 reranker 모델을 프로세스당 한 번만 로드한다."""
    from retrieval.reranker import BgeReranker
    return BgeReranker(path, use_fp16=use_fp16)


def _filters(
    asset_type: str | None,
    system_name: str | None,
    vendor: str | None,
    product: str | None,
    include_obsolete: bool,
) -> tuple[str, tuple]:
    """모든 검색 lane에 동일하게 적용할 ECM 메타데이터/수명주기 필터를 만든다.

    ``::text`` cast는 선택 필터 값이 None일 때 PostgreSQL의 indeterminate datatype
    오류를 방지한다. include_obsolete=true이면 현재·승인 버전 기본 제한도 해제한다.
    """
    retrieval = get_settings().retrieval
    require_current = retrieval.current_versions_only and not include_obsolete
    require_approved = retrieval.approved_versions_only and not include_obsolete
    statement = """
      AND a.deleted_at IS NULL
      AND (%s::text IS NULL OR a.asset_type=%s)
      AND (%s::text IS NULL OR a.system_name=%s)
      AND (%s::text IS NULL OR a.vendor=%s)
      AND (%s::text IS NULL OR a.product=%s)
      AND (%s OR v.is_current)
      AND (%s OR v.lifecycle_status='approved')
    """
    return statement, (
        asset_type, asset_type, system_name, system_name, vendor, vendor, product, product,
        not require_current, not require_approved,
    )


def _stack_symbol_lanes(
    conn: Any,
    query: str,
    filter_sql: str,
    filter_values: tuple,
    exact_top_k: int,
) -> list[list[str]]:
    """stack frame의 package/class/method로 소스 심볼 후보를 만든다."""
    lanes: list[list[str]] = []
    for package, class_names, method_names in _stack_frame_keys(query):
        rows = conn.execute(
            """SELECT c.chunk_id
               FROM code_symbols s JOIN chunks c USING(chunk_id)
               JOIN assets a ON a.asset_id=c.asset_id
               JOIN asset_versions v ON v.version_id=c.version_id
               WHERE s.package_name=%s
                 AND s.class_name=ANY(%s)
                 AND s.symbol_name=ANY(%s)"""
            + filter_sql
            + " ORDER BY c.created_at DESC LIMIT %s",
            (package, class_names, method_names, *filter_values, exact_top_k),
        ).fetchall()
        lanes.append([row["chunk_id"] for row in rows])
    return lanes


def _identifier_lanes(
    conn: Any,
    query: str,
    filter_sql: str,
    filter_values: tuple,
    exact_top_k: int,
) -> list[list[str]]:
    """에러코드·예외명·파일명과 연결된 청크 및 소스 심볼 후보를 만든다."""
    lanes: list[list[str]] = []
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
            (token, f"%{escaped}%", *filter_values, exact_top_k),
        ).fetchall()
        lanes.append([row["chunk_id"] for row in rows])

        # application stack frame만 거쳐 동일 심볼의 소스 청크를 별도 lane으로 올린다.
        # psycopg client-side placeholder 규칙 때문에 SQL 리터럴의 %는 %%로 쓴다.
        rows = conn.execute(
            """SELECT DISTINCT c.chunk_id, c.created_at
               FROM error_events e
               JOIN stack_frames f ON f.error_event_id=e.error_event_id
               JOIN code_symbols s
                 ON s.symbol_name=f.method_name
                AND (concat_ws('.', s.package_name, s.class_name)=f.class_name
                     OR f.class_name LIKE concat_ws('.', s.package_name, s.class_name) || '$%%')
               JOIN chunks c ON c.chunk_id=s.chunk_id
               JOIN assets a ON a.asset_id=c.asset_id
               JOIN asset_versions v ON v.version_id=c.version_id
               WHERE f.is_application_code
                 AND (e.error_code=%s OR e.exception_class=%s)"""
            + filter_sql
            + " ORDER BY c.created_at DESC LIMIT %s",
            (token, token, *filter_values, exact_top_k),
        ).fetchall()
        lanes.append([row["chunk_id"] for row in rows])
    return lanes


def _graph_entity_lane(
    conn: Any,
    query: str,
    filter_sql: str,
    filter_values: tuple,
    top_k: int,
) -> list[str]:
    """질문에 명시된 엔터티 이름과 연결된 원문 청크를 그래프 검색 lane으로 만든다."""
    normalized_query = re.sub(r"\s+", "", query).casefold()
    rows = conn.execute(
        """SELECT c.chunk_id, max(similarity(e.canonical_name, %s)) AS graph_score
             FROM knowledge_entities e
             JOIN chunk_entities ce USING(entity_id)
             JOIN chunks c USING(chunk_id)
             JOIN assets a USING(asset_id)
             JOIN asset_versions v USING(version_id)
            WHERE (
              position(e.normalized_name in %s) > 0
              OR similarity(e.canonical_name, %s) >= 0.35
            )"""
        + filter_sql
        + " GROUP BY c.chunk_id ORDER BY graph_score DESC LIMIT %s",
        (query, normalized_query, query, *filter_values, top_k),
    ).fetchall()
    return [row["chunk_id"] for row in rows]


def _lexical_lane(
    conn: Any,
    query: str,
    filter_sql: str,
    filter_values: tuple,
    top_k: int,
) -> list[str]:
    rows = conn.execute(
        """SELECT c.chunk_id
           FROM chunks c JOIN assets a USING(asset_id)
           JOIN asset_versions v USING(version_id)
           WHERE c.search_vector @@ plainto_tsquery('simple', %s)"""
        + filter_sql
        + """ ORDER BY ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s)) DESC
               LIMIT %s""",
        (query, *filter_values, query, top_k),
    ).fetchall()
    return [row["chunk_id"] for row in rows]


def _vector_lane(
    conn: Any,
    query_vector: np.ndarray,
    filter_sql: str,
    filter_values: tuple,
    top_k: int,
    max_distance: float,
) -> tuple[list[str], dict[str, float]]:
    rows = conn.execute(
        """SELECT c.chunk_id, c.embedding <=> %s AS vector_distance
           FROM chunks c JOIN assets a USING(asset_id)
           JOIN asset_versions v USING(version_id)
           WHERE c.embedding IS NOT NULL"""
        + filter_sql + " ORDER BY vector_distance LIMIT %s",
        (query_vector, *filter_values, top_k),
    ).fetchall()
    return _qualified_vector_ranking(rows, max_distance)


def _preferred_filters(
    filter_sql: str,
    filter_values: tuple,
    preferred_asset_ids: list[UUID],
) -> tuple[str, tuple]:
    """기본 보안·수명주기 조건에 직전 답변 문서 범위를 추가한다."""
    return (
        filter_sql + " AND a.asset_id=ANY(%s)",
        (*filter_values, preferred_asset_ids),
    )


def _retrieve_lanes(
    conn: Any,
    query: str,
    query_vector: np.ndarray,
    filter_sql: str,
    filter_values: tuple,
    settings: Any,
) -> tuple[list[list[str]], dict[str, float]]:
    """한 검색 범위에서 exact·graph·FTS·vector lane을 생성한다."""
    retrieval = settings.retrieval
    lanes = _stack_symbol_lanes(
        conn, query, filter_sql, filter_values, retrieval.exact_top_k
    )
    lanes.extend(_identifier_lanes(
        conn, query, filter_sql, filter_values, retrieval.exact_top_k
    ))
    if settings.graph_rag.enabled:
        lanes.append(_graph_entity_lane(
            conn, query, filter_sql, filter_values, retrieval.exact_top_k
        ))
    lanes.append(_lexical_lane(
        conn, query, filter_sql, filter_values, retrieval.lexical_top_k
    ))
    vector_ranking, vector_distances = _vector_lane(
        conn,
        query_vector,
        filter_sql,
        filter_values,
        retrieval.vector_top_k,
        retrieval.max_vector_distance,
    )
    lanes.append(vector_ranking)
    return lanes, vector_distances


def _fetch_candidates(conn: Any, ranked: list[str]) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT c.chunk_id, c.display_content AS content, c.kind, c.identifier,
                  c.page_start, c.page_end,
                  c.line_start, c.line_end, c.heading_path, c.parent_id,
                  c.previous_chunk_id, c.next_chunk_id, c.metadata,
                  a.asset_id, a.asset_type, a.title, a.vendor, a.product, a.system_name,
                  v.version_id, v.version_label, v.source_filename, v.lifecycle_status
           FROM chunks c JOIN assets a USING(asset_id)
           JOIN asset_versions v USING(version_id)
           WHERE c.chunk_id = ANY(%s)""",
        (ranked,),
    ).fetchall()
    return {row["chunk_id"]: row for row in rows}


def _rerank_candidates(
    query: str,
    ranked: list[str],
    by_id: dict[str, dict],
    limit: int,
    settings: Any,
) -> tuple[list[str], dict[str, float]]:
    """DB 연결을 보유하지 않은 상태에서 선택적 로컬 reranker를 실행한다."""
    reranker = settings.models.reranker
    if not reranker.enabled or not ranked:
        return ranked, {}
    reranked = get_reranker(reranker.model_path, reranker.use_fp16).rerank(
        query,
        [by_id[chunk_id]["content"] for chunk_id in ranked],
        top_k=min(limit, reranker.top_k),
    )
    return (
        [ranked[index] for index, _ in reranked],
        {ranked[index]: score for index, score in reranked},
    )


def _fetch_neighbor_content(
    conn: Any, by_id: dict[str, dict], selected: list[str]
) -> dict[str, str]:
    neighbor_ids: set[str] = set()
    for chunk_id in selected:
        row = by_id[chunk_id]
        if row["previous_chunk_id"]:
            neighbor_ids.add(row["previous_chunk_id"])
        if row["next_chunk_id"]:
            neighbor_ids.add(row["next_chunk_id"])
    if not neighbor_ids:
        return {}
    rows = conn.execute(
        "SELECT chunk_id, display_content FROM chunks WHERE chunk_id=ANY(%s)",
        (list(neighbor_ids),),
    ).fetchall()
    return {row["chunk_id"]: row["display_content"] for row in rows}


def search(
    query: str,
    *,
    asset_type: str | None = None,
    system_name: str | None = None,
    vendor: str | None = None,
    product: str | None = None,
    include_obsolete: bool = False,
    limit: int | None = None,
    preferred_asset_ids: list[UUID] | None = None,
) -> list[dict]:
    """검색 후보를 생성·융합하고 화면/LLM이 사용할 근거 메타데이터를 반환한다."""
    settings = get_settings()
    retrieval = settings.retrieval
    limit = limit or retrieval.default_limit
    limit = min(limit, retrieval.max_limit)
    filter_sql, filter_values = _filters(
        asset_type, system_name, vendor, product, include_obsolete
    )
    # 원격 embedding은 DB 연결을 대여하기 전에 끝낸다. 모델 서버 지연이 PostgreSQL
    # connection 점유 시간으로 이어지지 않는다.
    query_vector = np.asarray(embed([query])[0], dtype=np.float32)
    with connect() as conn:
        conn.execute(
            "SELECT set_config('hnsw.ef_search', %s, true)",
            (str(settings.database.vector.hnsw_ef_search),),
        )
        global_lanes, vector_distances = _retrieve_lanes(
            conn, query, query_vector, filter_sql, filter_values, settings
        )
        lanes = list(global_lanes)

        # 직전 답변 문서를 별도 검색 범위로 한 번 더 실행해 RRF에 soft boost를 준다.
        # 전체 lane도 항상 남기므로 새로운 시스템명·문서명이 명확한 질문은 전역 결과가
        # 우선할 수 있고, "그 문서의 재기동 방법" 같은 후속 질문은 이전 문서가 앞선다.
        preferred_asset_ids = list(dict.fromkeys(preferred_asset_ids or []))[:5]
        if preferred_asset_ids:
            preferred_sql, preferred_values = _preferred_filters(
                filter_sql, filter_values, preferred_asset_ids
            )
            preferred_lanes, preferred_distances = _retrieve_lanes(
                conn,
                query,
                query_vector,
                preferred_sql,
                preferred_values,
                settings,
            )
            lanes = preferred_lanes + lanes
            vector_distances.update(preferred_distances)

    # 서로 다른 점수 단위를 순위 기반 RRF로 결합한다.
    fused = reciprocal_rank_fusion(lanes, k=retrieval.rrf_k)
    rrf_scores = dict(fused)
    ranked = [chunk_id for chunk_id, _ in fused[:retrieval.fusion_top_k]]
    if not ranked:
        return []

    with connect() as conn:
        by_id = _fetch_candidates(conn, ranked)
    ranked = [chunk_id for chunk_id in ranked if chunk_id in by_id]

    # 로컬 reranker도 DB connection을 반환한 후 실행한다.
    ranked, rerank_scores = _rerank_candidates(
        query, ranked, by_id, limit, settings
    )
    selected = ranked[:limit]

    neighbors: dict[str, str] = {}
    if retrieval.expand_neighbors and selected:
        with connect() as conn:
            neighbors = _fetch_neighbor_content(conn, by_id, selected)

    results = []
    preferred_asset_id_values = {
        str(asset_id) for asset_id in (preferred_asset_ids or [])
    }
    for chunk_id in selected:
        row = dict(by_id[chunk_id])
        row["rrf_score"] = rrf_scores[chunk_id]
        row["rerank_score"] = rerank_scores.get(chunk_id)
        row["vector_distance"] = vector_distances.get(chunk_id)
        row["preferred_document"] = bool(
            preferred_asset_id_values
            and str(row["asset_id"]) in preferred_asset_id_values
        )
        row["neighbor_context"] = [
            neighbors[neighbor_id]
            for neighbor_id in (row["previous_chunk_id"], row["next_chunk_id"])
            if neighbor_id in neighbors
        ]
        results.append(row)
    return results
