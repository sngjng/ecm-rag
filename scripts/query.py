"""PostgreSQL hybrid retrieval을 터미널에서 확인하는 CLI."""
from __future__ import annotations

import argparse
import json
from service.search import search


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL ECM RAG 검색 품질 테스트")
    parser.add_argument("query", help="자연어/에러코드/심볼 검색 질의")
    parser.add_argument("--asset-type", choices=("document", "incident", "error_trace", "source_code"))
    parser.add_argument("--system-name")
    parser.add_argument("--vendor")
    parser.add_argument("--product")
    parser.add_argument("--include-obsolete", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    arguments = parser.parse_args()
    results = search(
        arguments.query,
        asset_type=arguments.asset_type,
        system_name=arguments.system_name,
        vendor=arguments.vendor,
        product=arguments.product,
        include_obsolete=arguments.include_obsolete,
        limit=arguments.limit,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
