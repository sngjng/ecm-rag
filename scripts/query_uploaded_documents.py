"""로그인 후 ECM 검색 API로 업로드된 자료를 조회하는 실행 예제.

이 스크립트는 PostgreSQL에 직접 접속하지 않는다. 브라우저와 동일하게 로그인 API에서
세션 쿠키를 발급받고, 공식 검색 엔드포인트를 호출하므로 인증·검색·권한 경로를 한 번에
검증할 수 있다.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Any

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8300"


def parse_arguments() -> argparse.Namespace:
    """명령행 인자를 정의한다. 비밀번호는 프로세스 목록에 노출되지 않게 인자로 받지 않는다."""
    parser = argparse.ArgumentParser(
        description="ECM-RAG에 로그인하여 업로드된 문서를 검색합니다."
    )
    parser.add_argument("query", help="검색할 문장 또는 키워드")
    parser.add_argument(
        "--base-url",
        default=os.getenv("ECM_RAG_BASE_URL", DEFAULT_BASE_URL),
        help=f"ECM-RAG 서버 주소 (기본값: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--employee-id",
        default=os.getenv("ECM_RAG_EMPLOYEE_ID", "test"),
        help="로그인 사번 (기본값: test)",
    )
    parser.add_argument(
        "--asset-type",
        choices=("document", "incident", "error_trace", "source_code"),
        help="특정 자산유형만 조회",
    )
    parser.add_argument("--system-name", help="특정 시스템명만 조회")
    parser.add_argument("--vendor", help="특정 제조사만 조회")
    parser.add_argument("--product", help="특정 제품만 조회")
    parser.add_argument(
        "--include-obsolete",
        action="store_true",
        help="폐기 버전까지 검색 대상에 포함",
    )
    parser.add_argument("--limit", type=int, default=5, help="최대 결과 수 (기본값: 5)")
    return parser.parse_args()


def read_password() -> str:
    """자동화에서는 환경변수를, 수동 실행에서는 화면에 표시되지 않는 입력을 사용한다."""
    password = os.getenv("ECM_RAG_PASSWORD")
    if password:
        return password
    return getpass.getpass("비밀번호: ")


def build_search_payload(arguments: argparse.Namespace) -> dict[str, Any]:
    """값이 지정된 필터만 API 요청에 포함한다."""
    payload: dict[str, Any] = {
        "query": arguments.query,
        "include_obsolete": arguments.include_obsolete,
        "limit": arguments.limit,
    }
    for name in ("asset_type", "system_name", "vendor", "product"):
        value = getattr(arguments, name)
        if value:
            payload[name] = value
    return payload


def print_results(results: list[dict[str, Any]]) -> None:
    """검색 결과에서 사용자 확인에 필요한 출처와 본문을 읽기 쉽게 출력한다."""
    if not results:
        print("검색 결과가 없습니다.")
        return

    print(f"검색 결과: {len(results)}건\n")
    for index, result in enumerate(results, start=1):
        page_start = result.get("page_start")
        page_end = result.get("page_end")
        if page_start is None:
            page = "-"
        elif page_end and page_end != page_start:
            page = f"{page_start}-{page_end}"
        else:
            page = str(page_start)

        print(f"[{index}] {result.get('title') or '(제목 없음)'}")
        print(f"    파일: {result.get('source_filename') or '-'}")
        print(f"    버전: {result.get('version_label') or '-'} / 페이지: {page}")
        print(f"    유형: {result.get('kind') or '-'}")
        print(f"    RRF 점수: {result.get('rrf_score', 0):.6f}")
        print("    내용:")
        content = (result.get("content") or "").strip()
        print(f"      {content[:700].replace(chr(10), chr(10) + '      ')}")
        print()


def main() -> int:
    arguments = parse_arguments()
    base_url = arguments.base_url.rstrip("/")

    try:
        # Client가 Set-Cookie 응답을 보관하므로 이후 검색·로그아웃 요청에 인증 쿠키가
        # 자동으로 포함된다.
        with httpx.Client(base_url=base_url, timeout=120.0) as client:
            login_response = client.post(
                "/api/v1/auth/login",
                json={
                    "employee_id": arguments.employee_id,
                    "password": read_password(),
                },
            )
            login_response.raise_for_status()
            employee = login_response.json()
            print(
                f"로그인: {employee['employee_name']} ({employee['employee_id']})"
                f" / {employee.get('department') or '부서 정보 없음'}"
            )

            search_response = client.post(
                "/api/v1/search",
                json=build_search_payload(arguments),
            )
            search_response.raise_for_status()
            print_results(search_response.json().get("results", []))

            # 예제 실행이 끝나면 서버 세션도 명시적으로 폐기한다.
            client.post("/api/v1/auth/logout")
        return 0
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        print(
            f"API 오류: HTTP {exc.response.status_code} - {detail}",
            file=sys.stderr,
        )
    except httpx.RequestError as exc:
        print(f"서버 연결 오류: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
