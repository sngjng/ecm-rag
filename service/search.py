"""정확 식별자 + PostgreSQL FTS + cosine 벡터를 RRF로 통합한다."""
from __future__ import annotations
from functools import lru_cache
import re
import numpy as np
from pgvector.psycopg import register_vector
from service import store, config
from service.embedding import embed

TOKEN = re.compile(r'[\w.$/-]{3,}', re.UNICODE)


@lru_cache(maxsize=2)
def get_reranker(path: str):
    from retrieval.reranker import BgeReranker
    return BgeReranker(path, use_fp16=False)


def search(query: str, content_type: str | None = None, system_name: str | None = None, limit: int = 10):
    candidates = []
    # 쿼리 파라미터만 사용한다. asset 필터는 각 검색 경로의 JOIN에서 같이 적용한다.
    filter_sql = ' AND (%s IS NULL OR a.content_type = %s) AND (%s IS NULL OR a.system_name = %s)'
    filters = (content_type, content_type, system_name, system_name)
    with store.connect() as conn:
        # 1) 코드/예외/파일명은 원문 그대로 정확 비교. LIKE는 포함하지 않아 오탐을 줄인다.
        identifiers = [t for t in TOKEN.findall(query) if any(ch.isdigit() for ch in t) or '.' in t or t[0].isupper()]
        for token in identifiers[:8]:
            rows = conn.execute('''SELECT c.chunk_id FROM rag.chunks c JOIN rag.assets a USING (asset_id)
                WHERE a.status='completed' AND (c.identifier = %s OR c.content ILIKE %s)'''
                + filter_sql + ' ORDER BY c.created_at DESC LIMIT 30',
                (token, '%' + token.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%', *filters)).fetchall()
            candidates.append([r['chunk_id'] for r in rows])
        # 2) 코드/한글 혼합에 simple FTS는 토큰 범위가 한정됨. identifier는 위 정확 검색 우선.
        rows = conn.execute('''SELECT c.chunk_id FROM rag.chunks c JOIN rag.assets a USING (asset_id)
            WHERE a.status='completed' AND c.search_vector @@ plainto_tsquery('simple', %s)'''
            + filter_sql + ''' ORDER BY ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s)) DESC LIMIT 40''',
            (query, *filters, query)).fetchall()
        candidates.append([r['chunk_id'] for r in rows])
        # 3) 같은 BGE-M3 모델로 질문을 임베딩해 근접 청크를 찾는다.
        vector = np.asarray(embed([query])[0], dtype=np.float32)
        rows = conn.execute('''SELECT c.chunk_id FROM rag.chunks c JOIN rag.assets a USING (asset_id)
            WHERE a.status='completed' AND c.embedding IS NOT NULL'''
            + filter_sql + ' ORDER BY c.embedding <=> %s LIMIT 40', (*filters, vector)).fetchall()
        candidates.append([r['chunk_id'] for r in rows])
        scores: dict[str, float] = {}
        for lane in candidates:
            for rank, chunk_id in enumerate(lane, 1):
                scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (60 + rank)
        ranked = sorted(scores, key=scores.get, reverse=True)[:max(limit, 30)]
        if not ranked: return []
        found = conn.execute('''SELECT c.chunk_id, c.content, c.kind, c.page_start, c.page_end,
            c.line_start, c.line_end, c.parent_id, c.previous_chunk_id, c.next_chunk_id,
            c.metadata, a.asset_id, a.filename, a.title, a.system_name, a.version_label
            FROM rag.chunks c JOIN rag.assets a USING(asset_id) WHERE c.chunk_id = ANY(%s)''', (ranked,)).fetchall()
    by_id = {row['chunk_id']: row for row in found}
    if config.RERANK_MODEL_PATH:
        # 모델은 첫 검색에서 한 번만 로드한다. 폐쇄망에서는 절대 로컬 경로를 지정한다.
        ranked = [ranked[i] for i, _ in get_reranker(config.RERANK_MODEL_PATH).rerank(
            query, [by_id[cid]['content'] for cid in ranked], top_k=limit)]
    return [{**by_id[cid], 'score': scores[cid]} for cid in ranked[:limit]]
