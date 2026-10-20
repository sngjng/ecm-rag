"""기존 PostgreSQL DB에 로그인 세션·업로드 감사 이력 migration을 적용한다.

001 기본 스키마가 이미 적용된 DB를 대상으로 하며 002 SQL은 IF NOT EXISTS로 작성되어
반복 실행해도 안전하다. DSN은 YAML/환경변수 설정 로더에서 읽고 화면에 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from psycopg.errors import UndefinedTable

from service.config import ROOT
from service.db import connect


MIGRATIONS = (
    ROOT / "sql" / "002_session_auth_audit.sql",
    ROOT / "sql" / "003_code_symbol_stack_lookup.sql",
)


def inherit_rag_environment(pid: int) -> None:
    """실행 중인 Linux 프로세스에서 RAG_* 설정만 안전하게 상속한다.

    이미 서비스가 떠 있는 상태에서 해당 서비스와 동일한 DB에 긴급 migration을
    적용할 때 사용한다. 값은 로그에 출력하지 않으며 Linux의 /proc 접근 권한이 있는
    운영 계정(또는 root)에서만 동작한다.
    """
    environ_path = Path("/proc") / str(pid) / "environ"
    for item in environ_path.read_bytes().split(b"\0"):
        if not item.startswith(b"RAG_") or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        os.environ[key.decode()] = value.decode()


def apply() -> None:
    with connect() as connection:
        for path in MIGRATIONS:
            source = path.read_text(encoding="utf-8")
            try:
                # 파라미터 없는 migration 파일은 simple query protocol로 한 트랜잭션에서
                # 실행한다. 중간 DDL 실패 시 connection context가 전체를 rollback한다.
                connection.execute(source, prepare=False)
            except UndefinedTable as exc:
                raise RuntimeError(
                    "기본 rag.assets 스키마가 없습니다. sql/001_pgvector.sql을 먼저 적용하세요."
                ) from exc
            print(f"적용 완료: {path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inherit-env-pid",
        type=int,
        help="실행 중인 Linux 프로세스의 RAG_* 환경을 상속한다(값은 출력하지 않음)",
    )
    args = parser.parse_args()
    if args.inherit_env_pid is not None:
        inherit_rag_environment(args.inherit_env_pid)
    apply()
