#!/usr/bin/env bash
# ECM-RAG API와 Worker를 현재 사용자 권한으로 함께 시작한다.
# 기본 실행 계정은 sngjng이며 sudo 또는 systemd를 사용하지 않는다.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$PROJECT_ROOT/artifacts/run"
LOG_DIR="$PROJECT_ROOT/artifacts/logs"
EXPECTED_USER="${ECM_RAG_USER:-sngjng}"

if [[ "$(id -un)" != "$EXPECTED_USER" ]]; then
  echo "오류: 이 스크립트는 $EXPECTED_USER 계정으로 실행해야 합니다. 현재 계정: $(id -un)" >&2
  echo "개발 확인 시에만 ECM_RAG_USER=$(id -un) 환경변수로 명시적으로 재정의할 수 있습니다." >&2
  exit 1
fi

cd "$PROJECT_ROOT"
mkdir -p "$RUN_DIR" "$LOG_DIR"

# 비밀값과 profile은 저장소에 커밋되지 않는 .env에서 선택적으로 읽는다.
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

# 0.0.0.0은 다른 사내 PC의 /api/v1/external/answer 호출을 받기 위한 기본값이다.
# 인터넷/비신뢰 대역에는 직접 노출하지 말고 방화벽 또는 reverse proxy ACL을 적용한다.
API_HOST="${ECM_RAG_HOST:-0.0.0.0}"
API_PORT="${ECM_RAG_PORT:-8300}"
PYTHON_BIN="${ECM_RAG_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
if [[ ! "$API_PORT" =~ ^[0-9]+$ ]] || (( API_PORT < 1 || API_PORT > 65535 )); then
  echo "오류: ECM_RAG_PORT는 1~65535 범위의 숫자여야 합니다: $API_PORT" >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "오류: Python 실행 파일을 찾을 수 없습니다: $PYTHON_BIN" >&2
  echo "ECM_RAG_PYTHON 또는 .venv 경로를 확인하세요." >&2
  exit 1
fi

# 프로세스 수와 접속 보호 값은 YAML/profile/환경변수 병합 결과를 그대로 사용한다.
read -r API_WORKERS API_LIMIT_CONCURRENCY API_BACKLOG WORKER_PROCESSES < <(
  "$PYTHON_BIN" -c 'from service.config import get_settings; s=get_settings(); print(s.server.workers, s.server.limit_concurrency, s.server.backlog, s.worker.processes)'
)
for value_name in API_WORKERS API_LIMIT_CONCURRENCY API_BACKLOG WORKER_PROCESSES; do
  value="${!value_name}"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || (( value < 1 )); then
    echo "오류: $value_name 설정은 1 이상의 숫자여야 합니다: $value" >&2
    exit 1
  fi
done

echo "PostgreSQL 필수 migration 확인 중..."
if ! "$PYTHON_BIN" -m scripts.apply_migrations; then
  echo "오류: DB migration 적용에 실패해 서비스를 시작하지 않습니다." >&2
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

start_process() {
  local name="$1"
  local marker="$2"
  local pid_file="$3"
  local log_file="$4"
  shift 4

  if [[ -f "$pid_file" ]]; then
    local old_pid
    old_pid="$(<"$pid_file")"
    if is_expected_process "$old_pid" "$marker"; then
      echo "$name 이미 실행 중 (PID $old_pid)"
      return 0
    fi
    echo "$name의 오래된 PID 파일을 정리합니다: $pid_file"
    rm -f -- "$pid_file"
  fi

  nohup "$@" >> "$log_file" 2>&1 &
  local new_pid=$!
  printf '%s\n' "$new_pid" > "$pid_file"

  sleep 1
  if ! is_expected_process "$new_pid" "$marker"; then
    echo "오류: $name 시작에 실패했습니다. 로그: $log_file" >&2
    tail -n 30 "$log_file" >&2 || true
    rm -f -- "$pid_file"
    return 1
  fi
  echo "$name 시작 완료 (PID $new_pid)"
}

API_PID_FILE="$RUN_DIR/api.pid"
API_LOG="$LOG_DIR/api.log"
WORKER_LOG="$LOG_DIR/worker.log"

# 단일 worker.pid를 사용하던 이전 버전이 실행 중이면 중복 worker 생성을 막는다.
if [[ -f "$RUN_DIR/worker.pid" ]]; then
  legacy_worker_pid="$(<"$RUN_DIR/worker.pid")"
  if is_expected_process "$legacy_worker_pid" "service.worker"; then
    echo "오류: 이전 방식의 Worker가 실행 중입니다. bin/stop.sh 실행 후 다시 시작해 주세요." >&2
    exit 1
  fi
  rm -f -- "$RUN_DIR/worker.pid"
fi

api_was_running=false
if [[ -f "$API_PID_FILE" ]] && is_expected_process "$(<"$API_PID_FILE")" "service.api:app"; then
  api_was_running=true
fi

if ! start_process "API" "service.api:app" "$API_PID_FILE" "$API_LOG" \
  "$PYTHON_BIN" -m uvicorn service.api:app --host "$API_HOST" --port "$API_PORT" \
  --workers "$API_WORKERS" --limit-concurrency "$API_LIMIT_CONCURRENCY" \
  --backlog "$API_BACKLOG" --log-config "$PROJECT_ROOT/config/logging.yaml"; then
  exit 1
fi

started_worker_pid_files=()
for ((worker_index = 1; worker_index <= WORKER_PROCESSES; worker_index++)); do
  worker_pid_file="$RUN_DIR/worker-$worker_index.pid"
  worker_was_running=false
  if [[ -f "$worker_pid_file" ]] && is_expected_process "$(<"$worker_pid_file")" "service.worker"; then
    worker_was_running=true
  fi
  if ! start_process "Worker $worker_index" "service.worker" "$worker_pid_file" "$WORKER_LOG" \
    "$PYTHON_BIN" -m service.worker; then
    for started_pid_file in "${started_worker_pid_files[@]}"; do
      started_pid="$(<"$started_pid_file")"
      if is_expected_process "$started_pid" "service.worker"; then
        kill -TERM "$started_pid" 2>/dev/null || true
      fi
      rm -f -- "$started_pid_file"
    done
    if [[ "$api_was_running" == false ]] && [[ -f "$API_PID_FILE" ]]; then
      api_pid="$(<"$API_PID_FILE")"
      if is_expected_process "$api_pid" "service.api:app"; then
        kill -TERM "$api_pid" 2>/dev/null || true
      fi
      rm -f -- "$API_PID_FILE"
    fi
    exit 1
  fi
  if [[ "$worker_was_running" == false ]]; then
    started_worker_pid_files+=("$worker_pid_file")
  fi
done

ready=false
if command -v curl >/dev/null 2>&1; then
  for _ in {1..30}; do
    if curl -fsS --max-time 1 "http://127.0.0.1:$API_PORT/health/live" >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 0.5
  done
else
  ready=true
  echo "주의: curl이 없어 HTTP 준비 상태 확인을 생략합니다."
fi

if [[ "$ready" != true ]]; then
  echo "오류: API 준비 상태 확인에 실패했습니다. $SCRIPT_DIR/stop.sh 실행 후 로그를 확인하세요." >&2
  echo "API 로그: $API_LOG" >&2
  exit 1
fi

echo
echo "ECM-RAG 기동 완료"
echo "  로그인: http://127.0.0.1:$API_PORT/login"
echo "  Profile: ${RAG_PROFILE:-config 기본값}"
echo "  API workers: $API_WORKERS (프로세스별 동시 요청 상한: $API_LIMIT_CONCURRENCY)"
echo "  Ingestion workers: $WORKER_PROCESSES"
echo "  API 로그: $API_LOG"
echo "  Worker 로그: $WORKER_LOG"
