# 다른 PC용 ECM-RAG 로컬 테스트 환경 구축 가이드

## 1. 문서 목적

이 문서는 다른 Windows PC에서 현재 ECM 문서 자산화 서비스를 재현하고 테스트하기 위한
전체 설치 절차입니다. 다음 환경을 기준으로 합니다.

- Windows 11 64-bit
- WSL 2
- Ubuntu 24.04
- Docker Desktop의 WSL 2 backend
- Python 3.11
- PostgreSQL 17 + pgvector
- FastAPI API 프로세스와 독립 worker 프로세스
- 테스트 profile의 deterministic embedding 및 개발용 로그인 stub

> **중요:** 다른 PC는 GitHub에 push된 커밋만 받을 수 있습니다. 현재 PC에 미커밋
> 변경사항이 있으면 먼저 검토·테스트·커밋·push해야 동일한 환경을 재현할 수 있습니다.

## 2. 권장 PC 사양

| 구분 | 핵심 API/DB 테스트 | Docling·OCR·로컬 모델 포함 |
|---|---:|---:|
| CPU | 4 core 이상 | 8 core 이상 권장 |
| RAM | 16 GB | 32 GB 이상 권장 |
| 디스크 여유 | 20 GB | 50 GB 이상 권장 |
| GPU | 불필요 | BGE-M3/reranker 로컬 실행 시 NVIDIA GPU 권장 |
| BIOS/UEFI | 가상화 활성화 | 가상화 활성화 |

Docker Desktop은 조직 규모와 용도에 따라 유료 구독이 필요할 수 있으므로 사내 라이선스
정책을 먼저 확인합니다.

## 2.1 사내망 설치 방식 먼저 결정

설치 전에 대상 PC의 네트워크 정책을 다음 중 하나로 구분합니다.

| 유형 | 특징 | 사용할 절차 |
|---|---|---|
| 외부 인터넷 허용 | GitHub, PyPI, Docker Hub 접근 가능 | 3~15절의 온라인 명령 사용 |
| 사내 mirror/proxy | 승인된 Git·PyPI·APT·Docker mirror만 접근 | URL을 사내 mirror 주소로 치환 |
| 완전 폐쇄망 | 외부 URL, package registry 접근 불가 | 16절의 오프라인 반입 절차 사용 |

외부 URL이 차단된 PC에서 `curl`, `git clone`, `docker pull`, `uv python install`,
`uv pip install`을 반복 실행해도 해결되지 않습니다. 인터넷 연결이 가능한 반입 준비 PC에서
필요한 파일을 먼저 모은 뒤 사내 보안 절차에 따라 전달해야 합니다.

## 3. 현재 PC에서 소스 배포 준비

새 PC 작업 전에 현재 개발 PC에서 실행합니다.

```powershell
cd C:\Users\HDS\Documents\ChatGPT\ecm-rag

git status
git branch --show-current
git log -1 --oneline
```

미커밋 파일이 있다면 내용과 비밀정보 포함 여부를 검토합니다. `.env`, 실제 비밀번호,
API key, 사내 문서와 모델 파일은 Git에 올리지 않습니다.

검토와 테스트가 끝난 변경만 작업 브랜치에 커밋합니다.

```powershell
git add <검토한-파일>
git commit -m "feat: describe current development change"
git push -u origin <현재-작업-브랜치>
```

안정화된 브랜치를 `main`에 병합한 경우 새 PC에서는 `main`을 사용합니다. 병합 전 개발
상태를 그대로 시험하려면 새 PC에서도 해당 원격 작업 브랜치로 전환해야 합니다.

## 4. Windows 기본 도구 설치

### 4.1 WSL 2와 Ubuntu 24.04

관리자 권한 PowerShell에서 실행합니다.

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
wsl --set-default-version 2
```

설치가 끝나면 Windows를 재부팅하고 Ubuntu를 한 번 실행해 Linux 사용자명과 비밀번호를
생성합니다.

설치 상태 확인:

```powershell
wsl --version
wsl --list --verbose
```

`Ubuntu-24.04`의 VERSION이 `2`여야 합니다. 1로 표시되면 다음 명령을 실행합니다.

```powershell
wsl --set-version Ubuntu-24.04 2
```

### 4.2 Git for Windows

일반 PowerShell에서 실행합니다.

```powershell
winget install --id Git.Git -e --source winget
git --version
```

실제 개발 명령은 WSL 내부 Git을 사용하지만 Windows에서도 저장소 상태를 확인할 수 있도록
설치합니다.

### 4.3 Docker Desktop

```powershell
winget install --id Docker.DockerDesktop -e --source winget
```

설치 후 Docker Desktop을 실행하고 다음 설정을 적용합니다.

1. `Settings → General → Use WSL 2 based engine` 활성화
2. `Settings → Resources → WSL Integration` 이동
3. `Ubuntu-24.04` 활성화
4. `Apply & Restart`

Docker Desktop이 Linux container mode인지 확인합니다. WSL Integration 메뉴가 없으면
Windows container mode일 수 있습니다.

Ubuntu 터미널에서 연결 상태를 확인합니다.

```bash
docker version
docker run --rm hello-world
```

## 5. Ubuntu 개발 도구 설치

이하 명령은 Ubuntu 24.04 WSL 터미널에서 실행합니다.

```bash
sudo apt update
sudo apt upgrade -y

sudo apt install -y \
  git \
  curl \
  ca-certificates \
  build-essential \
  libgl1 \
  libglib2.0-0 \
  libgomp1 \
  poppler-utils \
  tesseract-ocr
```

버전 확인:

```bash
git --version
curl --version
```

## 6. 프로젝트 clone

WSL 성능을 위해 프로젝트는 가능하면 `/mnt/c`가 아니라 Linux 홈 디렉터리에 둡니다.

```bash
mkdir -p ~/workspace
cd ~/workspace

git clone https://github.com/sngjng/ecm-rag.git
cd ecm-rag
```

안정화된 `main`을 시험할 때:

```bash
git switch main
git pull --ff-only origin main
```

아직 병합되지 않은 작업 브랜치를 시험할 때:

```bash
git fetch --prune origin
git switch --track origin/<작업-브랜치>
```

소스 확인:

```bash
git status
git log -3 --oneline --decorate
```

## 7. uv와 Python 3.11 설치

Ubuntu에서 uv 공식 설치 스크립트를 사용합니다. 사내 보안 정책상 외부 스크립트를 바로
실행할 수 없다면 먼저 내려받아 검토하거나 승인된 내부 배포본을 사용합니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv --version
uv python install 3.11
uv python list
```

프로젝트 전용 가상환경을 생성합니다.

```bash
cd ~/workspace/ecm-rag

uv venv --python 3.11 .venv
source .venv/bin/activate

python --version
```

결과는 `Python 3.11.x`여야 합니다.

## 8. Python 의존성 설치

### 8.1 CPU 테스트 PC

PyTorch를 CPU wheel로 먼저 설치한 뒤 프로젝트 의존성을 설치합니다. PyTorch wheel 주소는
변경될 수 있으므로 설치 시점에 공식 설치 선택기도 함께 확인합니다.

```bash
source ~/workspace/ecm-rag/.venv/bin/activate
cd ~/workspace/ecm-rag

uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install -r requirements-dev.txt
```

`requirements-dev.txt`가 `requirements.txt`를 포함하므로 개발·테스트와 런타임 패키지가
함께 설치됩니다.

### 8.2 NVIDIA GPU 테스트 PC

WSL GPU 드라이버와 CUDA 지원 여부를 먼저 확인합니다.

```bash
nvidia-smi
```

PyTorch 공식 설치 페이지에서 Linux/Pip/Python과 PC 드라이버에 맞는 CUDA 항목을 선택한
명령으로 torch를 먼저 설치한 다음 나머지 의존성을 설치합니다.

```bash
uv pip install -r requirements-dev.txt
```

설치 확인:

```bash
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
python -c "import fastapi, psycopg, pgvector, yaml; print('service dependencies: OK')"
```

핵심 API/DB 통합 테스트만 먼저 수행할 경우 Docling·OCR·모델 의존성 설치 시간이 길 수
있습니다. 그러나 다른 PC에 동일한 전체 환경을 구성하려면 위 requirements 전체 설치를
권장합니다.

## 9. PostgreSQL 17 + pgvector 테스트 DB 생성

### 9.1 이미지 다운로드

```bash
docker pull pgvector/pgvector:pg17
```

### 9.2 테스트 컨테이너 실행

아래 계정과 비밀번호는 로컬 테스트 전용입니다. 운영환경에서 사용하지 않습니다.

```bash
docker run -d \
  --name ecm-rag-it-postgres \
  -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=ragtest \
  -e POSTGRES_PASSWORD=ragtest_password \
  -e POSTGRES_DB=ragdb \
  --health-cmd="pg_isready -U ragtest -d ragdb" \
  --health-interval=2s \
  --health-timeout=3s \
  --health-retries=20 \
  pgvector/pgvector:pg17
```

상태 확인:

```bash
docker ps --filter name=ecm-rag-it-postgres
docker inspect --format '{{.State.Health.Status}}' ecm-rag-it-postgres
```

결과가 `healthy`여야 합니다.

이미 컨테이너가 있고 중지된 상태라면 새로 만들지 않고 시작합니다.

```bash
docker start ecm-rag-it-postgres
```

### 9.3 신규 DB 스키마 적용

최신 `001_pgvector.sql`에는 Asset, Version, queue, chunk, pgvector, 로그인 세션 및 업로드
감사 테이블이 포함됩니다.

```bash
cd ~/workspace/ecm-rag

docker cp \
  sql/001_pgvector.sql \
  ecm-rag-it-postgres:/tmp/001_pgvector.sql

docker exec ecm-rag-it-postgres \
  psql -v ON_ERROR_STOP=1 \
  -U ragtest \
  -d ragdb \
  -f /tmp/001_pgvector.sql
```

기존 `001_pgvector.sql`만 적용된 DB를 업그레이드할 때만 다음 migration을 추가 적용합니다.
신규 DB에는 중복 적용할 필요가 없습니다.

```bash
docker cp \
  sql/002_session_auth_audit.sql \
  ecm-rag-it-postgres:/tmp/002_session_auth_audit.sql

docker exec ecm-rag-it-postgres \
  psql -v ON_ERROR_STOP=1 \
  -U ragtest \
  -d ragdb \
  -f /tmp/002_session_auth_audit.sql
```

확장과 테이블 확인:

```bash
docker exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb \
  -c "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm');"

docker exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb \
  -c "SELECT tablename FROM pg_tables WHERE schemaname='rag' ORDER BY tablename;"
```

## 10. 테스트 환경 변수

각 터미널을 새로 열 때 다음 값을 설정합니다.

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

export RAG_PROFILE=test
export RAG_DB_DSN='postgresql://ragtest:ragtest_password@127.0.0.1:55432/ragdb'
```

`test` profile의 특징:

- 실제 BGE-M3 대신 `deterministic_stub` embedding 사용
- 실제 Java 로그인 대신 `development_stub` 사용
- 로그인 테스트 비밀번호: `test-password`
- DRM 비활성화
- reranker 비활성화
- 업로드 제한 5 MB

따라서 PostgreSQL, API, 세션, 업로드, worker 및 검색 흐름을 외부 모델 없이 검증할 수
있습니다.

## 11. 자동 테스트

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

export RAG_PROFILE=test
export RAG_DB_DSN='postgresql://ragtest:ragtest_password@127.0.0.1:55432/ragdb'

python -m compileall -q service tests
python -m pytest -q
```

모든 테스트가 passed여야 합니다. 실패 시 다음 순서로 확인합니다.

```bash
docker ps --filter name=ecm-rag-it-postgres
docker logs --tail 100 ecm-rag-it-postgres
python --version
git status
```

## 12. 서비스 수동 실행

### 12.1 터미널 1: FastAPI

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

export RAG_PROFILE=test
export RAG_DB_DSN='postgresql://ragtest:ragtest_password@127.0.0.1:55432/ragdb'

python -m uvicorn service.api:app \
  --host 0.0.0.0 \
  --port 8300
```

### 12.2 터미널 2: worker

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

export RAG_PROFILE=test
export RAG_DB_DSN='postgresql://ragtest:ragtest_password@127.0.0.1:55432/ragdb'

python -m service.worker
```

### 12.3 상태 확인

세 번째 터미널에서 실행합니다.

```bash
curl http://127.0.0.1:8300/health/live
curl http://127.0.0.1:8300/health/ready
```

예상 결과:

```json
{"status":"ok","profile":"test"}
{"status":"ready","database":"postgresql"}
```

## 13. 로그인·업로드·검색 화면 확인

Windows 브라우저에서 다음 주소를 엽니다.

- 로그인: `http://localhost:8300/login`
- 업로드: `http://localhost:8300/`
- API 문서: `http://localhost:8300/docs`

테스트 로그인:

| 항목 | 값 |
|---|---|
| 사번 | 임의 문자열, 예: `E12345` |
| 비밀번호 | `test-password` |

로그인 후 TXT 또는 MD 파일을 업로드합니다. 기존 문서 후보가 발견되면 화면에서 다음 중
하나를 선택합니다.

- 신규 Asset으로 등록
- 후보 Asset의 새 Version으로 등록
- 입력 내용을 수정하고 재확인

정상 처리 상태:

```text
queued → processing → completed
```

검색 API 확인:

```bash
curl -X POST \
  --cookie-jar /tmp/ecm-rag-cookie.txt \
  --cookie /tmp/ecm-rag-cookie.txt \
  -H 'Content-Type: application/json' \
  -d '{"query":"ECM 문서 자산","limit":5}' \
  http://127.0.0.1:8300/api/v1/search
```

CLI에서 세션까지 시험하려면 먼저 로그인 쿠키를 받습니다.

```bash
curl -X POST \
  --cookie-jar /tmp/ecm-rag-cookie.txt \
  -H 'Content-Type: application/json' \
  -d '{"employee_id":"E12345","password":"test-password"}' \
  http://127.0.0.1:8300/api/v1/auth/login
```

현재 사용자 확인:

```bash
curl --cookie /tmp/ecm-rag-cookie.txt \
  http://127.0.0.1:8300/api/v1/auth/me
```

## 14. 실제 모델·Java 인증·DRM 연동

핵심 통합 테스트가 끝난 후 실제 환경을 연결합니다.

```bash
export RAG_PROFILE=local
export RAG_DB_DSN='postgresql://raguser:실제비밀번호@DB주소:5432/ragdb'

export RAG_EMBED_URL='http://임베딩서버:8200/v1/embeddings'
export RAG_EMBED_MODEL='bge-m3'
export RAG_EMBED_API_KEY='필요한경우만설정'

export RAG_LLM_URL='http://LLM서버:8000/v1'
export RAG_LLM_MODEL='실제모델명'
export RAG_LLM_API_KEY='필요한경우만설정'

export RAG_AUTH_COMMAND='java -jar /경로/company-auth-adapter.jar'
export RAG_DRM_COMMAND='/경로/decrypt-wrapper'
```

주의사항:

- BGE-M3 응답은 PostgreSQL 스키마와 같은 1024차원이어야 합니다.
- `local` profile은 쿠키 `Secure=false`라 HTTP 로컬 테스트에만 사용합니다.
- Rocky Linux 운영 profile은 HTTPS와 `Secure=true` 쿠키를 전제로 합니다.
- Java 인증 wrapper 계약은 `JAVA_AUTH_INTEGRATION.md`를 따릅니다.
- DRM wrapper 계약은 README와 `service/drm.py`를 따릅니다.
- 비밀번호와 API key를 YAML이나 Git에 저장하지 않습니다.

## 15. 테스트 환경 중지·재시작·초기화

서비스 API와 worker는 각 터미널에서 `Ctrl+C`로 종료합니다.

DB 중지:

```bash
docker stop ecm-rag-it-postgres
```

DB 재시작:

```bash
docker start ecm-rag-it-postgres
```

테스트 DB를 완전히 다시 만들 때만 다음을 실행합니다. 컨테이너 내부 데이터는 복구되지
않으므로 운영 DB에는 절대 사용하지 않습니다.

```bash
docker rm -f ecm-rag-it-postgres
```

그 다음 이 문서의 **9. PostgreSQL 17 + pgvector 테스트 DB 생성**부터 다시 진행합니다.

## 16. 외부 URL이 차단된 사내망 설치

### 16.1 반입 준비 원칙

인터넷 연결이 가능한 별도 PC 또는 사내 반입 서버에서 준비합니다. 대상 PC와 다음 조건이
같아야 합니다.

- CPU architecture: 일반적으로 x86_64
- Linux 환경: Ubuntu 24.04 WSL
- Python: 3.11
- 설치할 PyTorch 종류: CPU 또는 승인된 CUDA 버전

반입 파일은 사내 보안 검토, 악성코드 검사와 승인된 이동 매체 절차를 따라야 합니다.
외부에서 받은 실행 파일과 모델은 SHA-256 목록을 함께 만들고 대상 PC에서 다시 비교합니다.

### 16.2 OS 설치 파일

완전 폐쇄망에서는 다음 설치 파일을 사전에 준비하거나 사내 SW 배포 시스템을 이용합니다.

- WSL 설치 패키지
- Ubuntu 24.04 WSL 배포판
- Git for Windows 설치 파일
- Docker Desktop 설치 파일과 사내 라이선스 승인
- GPU 사용 시 승인된 NVIDIA Windows/WSL driver

WSL과 Docker 설치 파일의 버전은 사내 표준 버전으로 고정하는 것이 좋습니다. Docker
Desktop을 사용할 수 없으면 사내 표준 Linux VM 또는 Podman/서버 Docker 사용 여부를
인프라 담당자와 먼저 결정해야 합니다.

### 16.3 GitHub 소스를 Git bundle로 준비

인터넷 연결 반입 준비 PC에서 실행합니다.

```bash
mkdir -p ~/ecm-rag-offline
cd ~/workspace/ecm-rag

git fetch --all --tags --prune
git bundle create ~/ecm-rag-offline/ecm-rag.bundle --all
git bundle verify ~/ecm-rag-offline/ecm-rag.bundle
```

대상 사내 PC에서는 GitHub URL 대신 bundle을 clone합니다.

```bash
mkdir -p ~/workspace
cd ~/workspace

git clone /반입경로/ecm-rag.bundle ecm-rag
cd ecm-rag

git branch -a
git switch main
```

특정 개발 브랜치가 필요하면 bundle에 해당 브랜치가 포함됐는지 확인한 뒤 전환합니다.

```bash
git switch <작업-브랜치>
```

이후 변경분만 추가 반입할 때는 준비 PC에서 최신 bundle을 다시 만들거나 승인된 사내 Git
서버를 사용합니다.

### 16.4 PostgreSQL/pgvector Docker image 반입

인터넷 연결 준비 PC:

```bash
docker pull pgvector/pgvector:pg17

docker save \
  --output ~/ecm-rag-offline/pgvector-pg17.tar \
  pgvector/pgvector:pg17
```

대상 사내 PC:

```bash
docker load --input /반입경로/pgvector-pg17.tar
docker image inspect pgvector/pgvector:pg17
```

이미지를 load한 다음 9.2절의 `docker run`부터 실행합니다. 사내 Docker registry가 있으면
보안 검증된 이미지를 registry에 등록하고 사내 주소에서 pull하는 방식을 우선합니다.

### 16.5 uv와 Python 3.11 runtime 반입

인터넷 연결 준비 PC의 Ubuntu 24.04 WSL에서 실행합니다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv python install 3.11

cp "$HOME/.local/bin/uv" ~/ecm-rag-offline/uv
cp "$HOME/.local/bin/uvx" ~/ecm-rag-offline/uvx

PYTHON_BIN="$(uv python find 3.11)"
PYTHON_ROOT="$(dirname "$(dirname "$PYTHON_BIN")")"
PYTHON_DIR_NAME="$(basename "$PYTHON_ROOT")"

printf '%s\n' "$PYTHON_DIR_NAME" \
  > ~/ecm-rag-offline/python-3.11-directory.txt

tar -C "$(dirname "$PYTHON_ROOT")" \
  -czf ~/ecm-rag-offline/python-3.11-linux-x86_64.tar.gz \
  "$PYTHON_DIR_NAME"
```

대상 사내 PC:

```bash
mkdir -p "$HOME/.local/bin"
mkdir -p "$HOME/.local/share/uv/python"

install -m 0755 /반입경로/uv "$HOME/.local/bin/uv"
install -m 0755 /반입경로/uvx "$HOME/.local/bin/uvx"

tar -xzf /반입경로/python-3.11-linux-x86_64.tar.gz \
  -C "$HOME/.local/share/uv/python"

export PATH="$HOME/.local/bin:$PATH"

PYTHON_DIR_NAME="$(cat /반입경로/python-3.11-directory.txt)"
PYTHON_BIN="$HOME/.local/share/uv/python/$PYTHON_DIR_NAME/bin/python3.11"

"$PYTHON_BIN" --version
uv --version
```

프로젝트 가상환경을 반입한 Python으로 생성합니다.

```bash
cd ~/workspace/ecm-rag

uv venv --python "$PYTHON_BIN" .venv
source .venv/bin/activate
python --version
```

가상환경 디렉터리 자체를 다른 PC에서 복사하면 내부 절대경로와 symlink가 깨질 수 있으므로
`.venv`는 대상 PC에서 새로 생성합니다.

### 16.6 Python wheelhouse 준비

인터넷 연결 준비 PC에서 최종 사용할 Python 3.11 가상환경과 CPU/CUDA 종류를 먼저
확정하고 패키지를 설치합니다.

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

# CPU 예시. GPU PC는 승인된 CUDA wheel 명령으로 바꾼다.
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install -r requirements-dev.txt

uv pip freeze \
  > ~/ecm-rag-offline/requirements-lock.txt

mkdir -p ~/ecm-rag-offline/wheelhouse

# uv 가상환경은 pip 모듈을 기본 포함하지 않을 수 있으므로 download 명령용 pip를 추가한다.
uv pip install pip

python -m pip download \
  --dest ~/ecm-rag-offline/wheelhouse \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  --requirement ~/ecm-rag-offline/requirements-lock.txt
```

`pip download`가 source distribution만 제공하는 패키지를 발견하면 동일 Ubuntu 24.04/Python
3.11 환경에서 wheel을 빌드하고 결과를 wheelhouse에 추가합니다.

```bash
python -m pip wheel \
  --wheel-dir ~/ecm-rag-offline/wheelhouse \
  --requirement ~/ecm-rag-offline/requirements-lock.txt
```

대상 사내 PC에서 외부 index를 사용하지 않고 설치합니다.

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

uv pip install \
  --offline \
  --find-links /반입경로/wheelhouse \
  --requirement /반입경로/requirements-lock.txt
```

설치 중 외부 URL 접속을 시도하거나 패키지를 찾지 못하면 wheelhouse가 불완전한 것입니다.
대상 PC에서 임의 URL 예외를 요청하기보다 누락된 wheel을 준비 PC에서 추가 반입합니다.

### 16.7 APT 패키지가 필요한 경우

`apt update`도 외부망 차단으로 실패할 수 있습니다. 다음 방법을 우선순위대로 사용합니다.

1. 사내 Ubuntu APT mirror 사용
2. 사내 표준 WSL Ubuntu image에 필요한 OS 패키지를 미리 포함
3. 동일 Ubuntu 24.04 준비 PC에서 의존 `.deb`를 함께 내려받아 승인 반입

사내 mirror가 있다면 `/etc/apt/sources.list.d/ubuntu.sources`를 사내 주소로 구성한 후 기존
5절 명령을 실행합니다. mirror 주소와 인증서는 인프라 담당자가 제공해야 합니다.

수동 `.deb` 반입은 의존성 누락 가능성이 높으므로 `git`, `curl`, `ca-certificates`,
`build-essential`, `libgl1`, `libglib2.0-0`, `libgomp1`, `poppler-utils`, `tesseract-ocr`가
포함된 사내 표준 WSL image를 만드는 방식을 권장합니다.

### 16.8 모델과 외부 솔루션 artifact

다음 파일은 Python wheelhouse나 Git 저장소에 자동으로 포함되지 않습니다.

- BGE-M3 embedding 모델 또는 사내 embedding API 배포본
- BGE reranker 모델
- Docling 모델 artifact
- EasyOCR/RapidOCR 모델 파일
- Java 사내 인증 adapter JAR과 wrapper
- Java DRM 라이브러리/JAR과 wrapper
- 사내 CA 인증서

각 artifact는 버전, 원본 위치, SHA-256, 설치 경로, 담당 부서를 manifest에 기록합니다.
운영 profile 경로와 실제 반입 경로가 다르면 YAML profile 또는 환경 변수로 조정합니다.

### 16.9 반입 파일 무결성 manifest

인터넷 연결 준비 PC:

```bash
cd ~/ecm-rag-offline
find . -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  > SHA256SUMS
```

대상 사내 PC:

```bash
cd /반입경로
sha256sum --check SHA256SUMS
```

모든 파일이 `OK`여야 설치를 계속합니다. 한 파일이라도 다르면 해당 파일을 사용하지 않고
보안 담당자와 반입 과정을 다시 확인합니다.

### 16.10 오프라인 반입 묶음 예시

```text
ecm-rag-offline/
├── SHA256SUMS
├── ecm-rag.bundle
├── pgvector-pg17.tar
├── uv
├── uvx
├── python-3.11-directory.txt
├── python-3.11-linux-x86_64.tar.gz
├── requirements-lock.txt
├── wheelhouse/
├── models/
│   ├── bge-m3/
│   ├── bge-reranker-v2-m3/
│   └── docling/
└── java/
    ├── company-auth-adapter.jar
    └── drm-adapter.jar
```

## 17. 최종 점검표

- [ ] `wsl --list --verbose`에서 Ubuntu가 WSL 2로 표시됨
- [ ] Ubuntu 터미널에서 `docker version` 성공
- [ ] GitHub의 시험 대상 브랜치와 로컬 HEAD가 일치함
- [ ] `python --version`이 3.11.x
- [ ] PostgreSQL 컨테이너 상태가 healthy
- [ ] vector와 pg_trgm 확장이 설치됨
- [ ] 최신 `001_pgvector.sql` 적용 완료
- [ ] 전체 pytest 통과
- [ ] `/health/live`가 200 반환
- [ ] `/health/ready`가 200 반환
- [ ] 개발 stub 로그인 성공
- [ ] 신규 Asset 및 새 Version 선택 흐름 확인
- [ ] worker 작업이 completed로 종료
- [ ] 검색 API가 근거 청크를 반환
- [ ] 업로드 감사 이력에 로그인 사용자 정보가 저장됨
- [ ] 실제 운영 연동 전 비밀값·HTTPS·쿠키·Java wrapper 별도 검증

## 18. 공식 참고 자료

- Microsoft WSL 설치: https://learn.microsoft.com/windows/wsl/install
- Docker Desktop Windows 설치: https://docs.docker.com/desktop/setup/install/windows-install/
- Docker Desktop WSL 연동: https://docs.docker.com/desktop/features/wsl/
- uv 설치: https://docs.astral.sh/uv/getting-started/installation/
- uv Python 관리: https://docs.astral.sh/uv/guides/install-python/
- pgvector Docker 이미지: https://github.com/pgvector/pgvector
- PyTorch 설치 선택기: https://pytorch.org/get-started/locally/
