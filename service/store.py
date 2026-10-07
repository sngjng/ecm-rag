"""PostgreSQL 트랜잭션 경계. 실패 시 검색 청크 일부만 남지 않도록 전체 롤백."""
from __future__ import annotations
from uuid import UUID, uuid4
import numpy as np
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pgvector.psycopg import register_vector
from service import config
from service.parsers import Item


def connect():
    if not config.DB_DSN:
        raise RuntimeError('RAG_DB_DSN 환경 변수가 필요합니다')
    conn = psycopg.connect(config.DB_DSN, row_factory=dict_row, connect_timeout=10)
    register_vector(conn)
    return conn


def create_asset(filename: str, content_type: str, subtype: str, title: str,
                 system_name: str, version_label: str, repository: str, branch: str,
                 sha256: str, source_path: str, logical_path: str = "") -> str:
    asset_id = str(uuid4())
    with connect() as conn:
        conn.execute('''INSERT INTO rag.assets (asset_id, filename, content_type, subtype, title,
            system_name, version_label, repository, branch, sha256, source_path, metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (asset_id, filename, content_type, subtype, title, system_name, version_label,
             repository, branch, sha256, source_path, Jsonb({"logical_path": logical_path})))
    return asset_id


def get_asset(asset_id: str) -> dict | None:
    with connect() as conn:
        return conn.execute('''SELECT asset_id, filename, content_type, subtype, title, system_name,
                version_label, repository, branch, source_path, sha256, metadata, status, chunk_count,
                error_detail, created_at, indexed_at FROM rag.assets WHERE asset_id=%s''',
                (UUID(asset_id),)).fetchone()


def set_status(asset_id: str, status: str, detail: str | None = None):
    with connect() as conn:
        conn.execute('UPDATE rag.assets SET status=%s, error_detail=%s WHERE asset_id=%s',
                     (status, detail, UUID(asset_id)))


def save_items(asset_id: str, items: list[Item], vectors: list[list[float]], report: dict):
    if len(items) != len(vectors): raise ValueError('청크와 임베딩 수가 다릅니다')
    with connect() as conn:
        for n, (item, vector) in enumerate(zip(items, vectors)):
            chunk_id = f'{asset_id}:{n:06d}'
            prev_id = f'{asset_id}:{n-1:06d}' if n else None
            next_id = f'{asset_id}:{n+1:06d}' if n+1 < len(items) else None
            conn.execute('''INSERT INTO rag.chunks (chunk_id, asset_id, kind, content, parent_id,
                previous_chunk_id, next_chunk_id, page_start, page_end, line_start, line_end,
                identifier, metadata, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (chunk_id, UUID(asset_id), item.kind, item.text, item.parent_id,
                 prev_id, next_id, item.page_start, item.page_end, item.line_start, item.line_end,
                 item.identifier, Jsonb(item.metadata), np.asarray(vector, dtype=np.float32)))
            if item.error:
                event_id = uuid4()
                e = item.error
                conn.execute('''INSERT INTO rag.error_events (error_event_id, asset_id, error_code,
                    exception_class, message, raw_trace, fingerprint, system_name)
                    SELECT %s, asset_id, %s,%s,%s,%s,%s,system_name FROM rag.assets WHERE asset_id=%s''',
                    (event_id, e['error_code'], e['exception_class'], e['message'], e['raw_trace'],
                     e['fingerprint'], UUID(asset_id)))
                for seq, frame in enumerate(e['frames']):
                    conn.execute('''INSERT INTO rag.stack_frames (error_event_id, frame_seq,
                        class_name, method_name, file_name, line_number) VALUES (%s,%s,%s,%s,%s,%s)''',
                        (event_id, seq, frame['class_name'], frame['method_name'], frame['file_name'], frame['line_number']))
            if item.symbol:
                s = item.symbol
                conn.execute('''INSERT INTO rag.code_symbols (symbol_id, asset_id, chunk_id,
                    repository, branch, file_path, language, symbol_type, class_name,
                    symbol_name, signature, start_line, end_line)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (uuid4(), UUID(asset_id), chunk_id, s['repository'], s['branch'], s['file_path'],
                     s['language'], s['symbol_type'], s['class_name'], s['symbol_name'],
                     s['signature'], s['start_line'], s['end_line']))
        conn.execute('''UPDATE rag.assets SET status='completed', indexed_at=now(),
            chunk_count=%s, metadata=%s, error_detail=NULL WHERE asset_id=%s''',
            (len(items), Jsonb(report), UUID(asset_id)))
