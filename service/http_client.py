"""외부 모델 API용 재사용 HTTP client와 제한된 재시도 정책."""
from __future__ import annotations

import atexit
import time
from functools import lru_cache

import httpx

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_clients: set[httpx.Client] = set()


@lru_cache(maxsize=8)
def get_http_client(
    service_name: str,
    timeout_seconds: float,
    api_key: str,
    max_connections: int,
    max_keepalive_connections: int,
) -> httpx.Client:
    """프로세스 안에서 TCP 연결을 재사용하는 동기 client를 반환한다."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    client = httpx.Client(
        timeout=httpx.Timeout(timeout_seconds),
        headers=headers,
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        ),
    )
    _clients.add(client)
    return client


def retry_delay_seconds(
    attempt: int,
    base_seconds: float,
    max_seconds: float,
    response: httpx.Response | None = None,
) -> float:
    """Retry-After(초)를 우선하고 없으면 상한이 있는 지수 backoff를 계산한다."""
    if response is not None:
        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            if retry_after:
                return min(float(retry_after), max_seconds)
        except ValueError:
            pass
    return min(base_seconds * (2 ** max(0, attempt - 1)), max_seconds)


def sleep_before_retry(
    attempt: int,
    base_seconds: float,
    max_seconds: float,
    response: httpx.Response | None = None,
) -> None:
    delay = retry_delay_seconds(attempt, base_seconds, max_seconds, response)
    if delay > 0:
        time.sleep(delay)


def close_http_clients() -> None:
    """프로세스 종료 시 keep-alive socket을 닫는다."""
    for client in list(_clients):
        client.close()
    _clients.clear()
    get_http_client.cache_clear()


atexit.register(close_http_clients)
