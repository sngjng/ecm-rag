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
    """업무 트랜잭션용 PostgreSQL 연결을 생성한다.

    호출부는 반드시 ``with connect() as conn`` 형태로 사용한다. psycopg connection
    context는 정상 종료 시 commit, 예외 발생 시 rollback하므로 repository 함수 하나가
    기본 트랜잭션 경계가 된다. 연결할 때마다 pgvector adapter와 ``rag,public``
    search_path를 등록해 SQL에서 schema 접두어를 반복하지 않는다.
    """
    settings = get_settings()
    if not settings.database.dsn:
        raise RuntimeError("database.dsn 또는 RAG_DB_DSN 설정이 필요합니다")
    connection = psycopg.connect(
        settings.database.dsn,
        row_factory=dict_row,
        connect_timeout=settings.database.connect_timeout_seconds,
    )
    # Python list/NumPy 배열과 PostgreSQL vector 타입 간 변환을 등록한다.
    register_vector(connection)
    # schema 이름을 SQL 문자열에 보간하지 않고 PostgreSQL 설정 함수의 값으로 전달한다.
    connection.execute(
        "SELECT set_config('search_path', %s, false)",
        (f"{settings.database.schema_name},public",),
    )
    return connection
