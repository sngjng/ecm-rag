#!/usr/bin/env bash
# ECM-RAG Worker와 API를 PID 파일로 식별해 현재 사용자 권한으로 안전하게 종료한다.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$PROJECT_ROOT/artifacts/run"
EXPECTED_USER="${ECM_RAG_USER:-sngjng}"

if [[ "$(id -un)" != "$EXPECTED_USER" ]]; then
  echo "오류: 이 스크립트는 $EXPECTED_USER 계정으로 실행해야 합니다. 현재 계정: $(id -un)" >&2
  exit 1
fi

if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi
STOP_TIMEOUT="${ECM_RAG_STOP_TIMEOUT:-30}"
if [[ ! "$STOP_TIMEOUT" =~ ^[0-9]+$ ]] || (( STOP_TIMEOUT < 1 )); then
  echo "오류: ECM_RAG_STOP_TIMEOUT은 1 이상의 숫자여야 합니다: $STOP_TIMEOUT" >&2
  exit 1
fi

is_expected_process() {
  local pid="$1"
  local marker="$2"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$marker"
}

stop_process() {
  local name="$1"
  local marker="$2"
  local pid_file="$3"

  if [[ ! -f "$pid_file" ]]; then
    echo "$name 중지 상태 (PID 파일 없음)"
    return 0
  fi

  local pid
  pid="$(<"$pid_file")"
  if ! is_expected_process "$pid" "$marker"; then
    echo "$name 프로세스가 없거나 PID가 다른 프로세스를 가리켜 PID 파일만 정리합니다."
    rm -f -- "$pid_file"
    return 0
  fi

  echo "$name 종료 요청 (PID $pid)"
  kill -TERM "$pid"

  local waited=0
  while kill -0 "$pid" 2>/dev/null && (( waited < STOP_TIMEOUT * 2 )); do
    sleep 0.5
    ((waited += 1))
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "주의: $name이 ${STOP_TIMEOUT}초 안에 종료되지 않아 강제 종료합니다." >&2
    if is_expected_process "$pid" "$marker"; then
      kill -KILL "$pid"
    fi
  fi

  rm -f -- "$pid_file"
  echo "$name 종료 완료"
}

# 신규 접수를 먼저 차단한 다음 Worker를 종료한다.
stop_process "API" "service.api:app" "$RUN_DIR/api.pid"
stop_process "Worker" "service.worker" "$RUN_DIR/worker.pid"

echo
echo "ECM-RAG 종료 완료"
