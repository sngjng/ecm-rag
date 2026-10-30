"""프로세스별 PostgreSQL 동기 연결 풀과 트랜잭션 경계.

FastAPI의 동기 route와 ingestion worker가 같은 repository 코드를 사용하므로 async 풀이
아닌 ``psycopg_pool.ConnectionPool``을 사용한다. Uvicorn/worker 프로세스마다 풀이 하나씩
생성되며 요청이 끝나면 연결을 닫지 않고 풀에 반환한다.
"""
from __future__ import annotations

import atexit
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from service.config import get_settings

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _configure_connection(connection: psycopg.Connection) -> None:
    """새 물리 연결에 pgvector 변환기와 schema 검색 경로를 한 번만 설정한다."""
    settings = get_settings()
    register_vector(connection)
    connection.execute(
        "SELECT set_config('search_path', %s, false)",
        (f"{settings.database.schema_name},public",),
    )
    # configure callback은 재사용 전에 연결을 IDLE 상태로 반환해야 한다.
    connection.commit()


def get_connection_pool() -> ConnectionPool:
    """현재 OS 프로세스의 연결 풀을 지연 생성한다."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        database = get_settings().database
        if not database.dsn:
            raise RuntimeError("database.dsn 또는 RAG_DB_DSN 설정이 필요합니다")
        pool = ConnectionPool(
            conninfo=database.dsn,
            min_size=database.pool.min_size,
            max_size=database.pool.max_size,
            timeout=database.pool.acquire_timeout_seconds,
            max_idle=database.pool.max_idle_seconds,
            max_lifetime=database.pool.max_lifetime_seconds,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": database.connect_timeout_seconds,
            },
            configure=_configure_connection,
            open=False,
            name="ecm-rag-db",
        )
        pool.open(wait=True)
        _pool = pool
        return pool


def connection_lease() -> AbstractContextManager[psycopg.Connection]:
    """session lock처럼 연결을 명시적으로 오래 보유할 저수준 pool lease를 반환한다."""
    return get_connection_pool().connection()


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """업무 트랜잭션 동안 pooled connection을 대여한 뒤 자동 반환한다."""
    with connection_lease() as connection:
        yield connection


def close_connection_pool() -> None:
    """프로세스 종료 시 보유 중인 물리 연결을 정리한다."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.close()


atexit.register(close_connection_pool)
