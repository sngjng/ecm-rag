"""PostgreSQL 연결 경계.

비즈니스 코드는 DSN이나 schema 이름을 직접 다루지 않는다. 모든 repository는 이
함수로 연결하고 PostgreSQL 트랜잭션은 psycopg connection context로 관리한다.
"""
from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from service.config import get_settings


def connect() -> psycopg.Connection:
    settings = get_settings()
    if not settings.database.dsn:
        raise RuntimeError("database.dsn 또는 RAG_DB_DSN 설정이 필요합니다")
    connection = psycopg.connect(
        settings.database.dsn,
        row_factory=dict_row,
        connect_timeout=settings.database.connect_timeout_seconds,
    )
    register_vector(connection)
    # schema 이름을 SQL 문자열에 보간하지 않고 PostgreSQL 설정 함수의 값으로 전달한다.
    connection.execute(
        "SELECT set_config('search_path', %s, false)",
        (f"{settings.database.schema_name},public",),
    )
    return connection
