"""활성 YAML profile의 embedding 차원으로 PostgreSQL 설치 SQL을 렌더링한다."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from service.config import ROOT, get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True, help="생성할 SQL 파일")
    arguments = parser.parse_args()
    source_path = ROOT / "sql" / "001_pgvector.sql"
    source = source_path.read_text(encoding="utf-8")
    dimension = get_settings().models.embedding.dimension
    rendered, count = re.subn(
        r"(embedding\s+vector\()\d+(\)\s+NOT NULL)",
        rf"\g<1>{dimension}\g<2>",
        source,
        count=1,
    )
    if count != 1:
        raise RuntimeError("SQL에서 embedding vector 차원 선언을 정확히 하나 찾지 못했습니다")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    print(f"rendered={arguments.output} dimension={dimension}")


if __name__ == "__main__":
    main()
