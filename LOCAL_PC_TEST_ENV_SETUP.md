# 다른 PC용 ECM-RAG 로컬 테스트 환경 구축 가이드

## 1. 문서 목적

이 문서는 다른 Windows PC에서 현재 ECM 문서 자산화 서비스를 재현하고 테스트하기 위한
전체 설치 절차입니다. 다음 환경을 기준으로 합니다.

- Windows 11 64-bit
- WSL 2
- Ubuntu 24.04
- Docker Desktop의 WSL 2 backend 또는 Ubuntu WSL 네이티브 PostgreSQL
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

Docker 방식은 Docker Desktop 라이선스 정책을 먼저 확인합니다. Docker Desktop을 사용할
수 없으면 9절과 16절의 Ubuntu WSL 네이티브 PostgreSQL 방식을 선택할 수 있습니다.

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

### 4.3 Docker Desktop(Docker DB 방식을 선택할 때만)

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
python -c "import fastapi, psycopg, psycopg_pool, pgvector, yaml; print('service dependencies: OK')"
python -c "from docx import Document; from pptx import Presentation; print('office parsers: OK')"
```

핵심 API/DB 통합 테스트만 먼저 수행할 경우 Docling·OCR·모델 의존성 설치 시간이 길 수
있습니다. 그러나 다른 PC에 동일한 전체 환경을 구성하려면 위 requirements 전체 설치를
권장합니다.

## 9. PostgreSQL 17 + pgvector 테스트 DB 생성

Rocky Linux 9 폐쇄망 서버에서 Docker Desktop 대신 rootless Podman을 사용하는 경우에는
[`PODMAN_POSTGRESQL_PGVECTOR_SETUP.md`](PODMAN_POSTGRESQL_PGVECTOR_SETUP.md)를 적용합니다.
이 절의 Docker/WSL 절차와 Podman 절차를 같은 DB에 동시에 적용하지 않습니다.

다음 두 방식 중 하나를 선택합니다. 애플리케이션 기능과 DB 스키마는 같으며 접속 포트와
운영 방법만 다릅니다. 두 DB를 동시에 실행할 수도 있지만 테스트할 때는 `RAG_DB_DSN`이
어느 DB를 가리키는지 반드시 확인합니다.

| 방식 | 실행 위치 | 접속 주소 | 적합한 경우 |
|---|---|---|---|
| Docker | Docker Desktop Linux container | `127.0.0.1:55432` | 격리와 초기화가 쉬운 기본 테스트 |
| WSL 네이티브 | Ubuntu 24.04 WSL 서비스 | `127.0.0.1:5432` | Docker를 사용하지 않거나 폐쇄망 OS 패키지 설치를 검증할 때 |

Windows용 PostgreSQL을 별도로 설치하는 구성은 이 문서의 검증 범위에 포함하지 않습니다.

### 9.1 방식 A: Docker 컨테이너

#### 9.1.1 이미지 다운로드

```bash
docker pull pgvector/pgvector:pg17
```

#### 9.1.2 테스트 컨테이너 실행

DB 데이터는 컨테이너 writable layer가 아니라 WSL 로컬 경로
`$HOME/ecm-rag-data/postgresql17`에 저장합니다. 필요하면 `ECM_RAG_PGDATA`만 다른 절대경로로
변경할 수 있지만, 성능과 Linux 권한 호환성을 위해 `/mnt/c`보다 WSL Linux filesystem
경로를 권장합니다.

이미지를 이용해 bind mount 디렉터리의 소유자를 이미지 내부 `postgres` 계정으로 맞춘 뒤
컨테이너를 실행합니다. 아래 계정과 비밀번호는 로컬 테스트 전용입니다.
동일한 이름의 기존 컨테이너가 있다면 아래 `docker run`을 바로 실행하지 말고, 이 절 뒤쪽의
기존 DB 백업·복원 절차를 먼저 확인합니다.

```bash
export ECM_RAG_PGDATA="$HOME/ecm-rag-data/postgresql17"
mkdir -p "$ECM_RAG_PGDATA"

docker run --rm \
  --user root \
  --mount type=bind,source="$ECM_RAG_PGDATA",target=/var/lib/postgresql/data \
  pgvector/pgvector:pg17 \
  chown -R postgres:postgres /var/lib/postgresql/data

docker run -d \
  --name ecm-rag-it-postgres \
  -p 127.0.0.1:55432:5432 \
  --mount type=bind,source="$ECM_RAG_PGDATA",target=/var/lib/postgresql/data \
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
docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' \
  ecm-rag-it-postgres
```

결과가 `healthy`여야 하고 mount 출력의 대상이 `/var/lib/postgresql/data`여야 합니다. 컨테이너를
삭제하거나 다시 만들어도 같은 `ECM_RAG_PGDATA` 경로를 mount하면 기존 DB가 유지됩니다.

이미 컨테이너가 있고 중지된 상태이며 위 `docker inspect`에서 동일한 bind mount가 확인되면
새로 만들지 않고 시작합니다.

```bash
docker start ecm-rag-it-postgres
```

기존 컨테이너에 `/var/lib/postgresql/data` bind mount가 없다면 실행 중인 컨테이너에 mount를
추가할 수 없습니다. 기존 DB를 보존해야 하는 경우 먼저 host 쪽에 논리 백업을 생성합니다.

```bash
mkdir -p "$HOME/ecm-rag-data/migration"
export ECM_RAG_DB_BACKUP="$HOME/ecm-rag-data/migration/ragdb-before-bind-$(date +%Y%m%d-%H%M%S).dump"

docker exec ecm-rag-it-postgres \
  pg_dump -U ragtest -d ragdb --format=custom \
  > "$ECM_RAG_DB_BACKUP"
test -s "$ECM_RAG_DB_BACKUP"
printf 'DB 논리 백업: %s\n' "$ECM_RAG_DB_BACKUP"
```

백업 파일이 존재하고 크기가 0보다 큰 것을 확인한 다음 기존 컨테이너를 제거하고, 이 절의
디렉터리 준비·권한 설정·`docker run` 명령으로 bind-mount 컨테이너를 새로 만듭니다. 새
컨테이너가 `healthy`가 되면 복원합니다.

```bash
docker rm -f ecm-rag-it-postgres

# 위의 ECM_RAG_PGDATA 준비, 권한 설정, docker run 명령을 다시 실행한 후 복원
docker exec -i ecm-rag-it-postgres \
  pg_restore -U ragtest -d ragdb --clean --if-exists --no-owner \
  < "$ECM_RAG_DB_BACKUP"

docker exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb -c \
  "SELECT count(*) AS rag_table_count FROM pg_tables WHERE schemaname='rag';"
```

복원과 애플리케이션 테스트가 끝날 때까지 백업 파일과 기존 Docker volume을 삭제하거나
`docker volume prune`하지 않습니다. 기존 데이터가 없으면 백업·복원 과정 없이 컨테이너만
제거하고 bind-mount 방식으로 새로 만들 수 있습니다.

#### 9.1.3 신규 DB 스키마 적용

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

### 9.2 방식 B: Ubuntu 24.04 WSL 네이티브 설치

이 방식은 PostgreSQL을 Windows가 아니라 이 문서의 규정 Linux 환경인 Ubuntu 24.04
WSL 안에 설치합니다. 폐쇄망 대상 PC에서는 외부 PGDG 저장소에 접속하지 않고, 16.4.2절에서
동일 환경으로 준비한 `.deb` 묶음을 사용합니다.

#### 9.2.1 대상 OS와 반입 파일 검증

```bash
. /etc/os-release
printf 'distribution=%s codename=%s architecture=%s\n' \
  "$PRETTY_NAME" "$VERSION_CODENAME" "$(dpkg --print-architecture)"
test "$VERSION_CODENAME" = "noble"

cd /반입경로/postgresql17-native
sha256sum --check SHA256SUMS
```

Ubuntu codename은 `noble`, 일반 Intel/AMD PC의 architecture는 `amd64`여야 합니다.
ARM64 PC에서는 준비 PC와 대상 PC가 모두 `arm64`여야 합니다. 체크섬이 하나라도 실패하면
설치하지 않고 반입 파일을 다시 준비합니다.

#### 9.2.2 오프라인 `.deb` 설치

16.4.2절의 절차로 체크섬 검증, 임시 local APT source 등록과 다음 세 패키지 설치를
완료합니다. `dpkg -i`만 단독 실행하면 의존성 해소가 누락될 수 있으므로 사용하지 않습니다.

- `postgresql-17`
- `postgresql-client-17`
- `postgresql-17-pgvector`

설치가 외부 저장소 접속을 시도하거나 의존 패키지를 찾지 못하면 반입 묶음이 대상 PC의
Ubuntu 기준과 일치하지 않거나 누락된 것입니다. 임의 외부 URL을 허용하지 말고 준비
PC에서 묶음을 다시 생성합니다.

설치 버전과 cluster를 확인합니다.

```bash
/usr/lib/postgresql/17/bin/postgres --version
/usr/lib/postgresql/17/bin/psql --version
dpkg-query -W \
  postgresql-17 \
  postgresql-client-17 \
  postgresql-17-pgvector
pg_lsclusters
```

세 패키지가 모두 설치되고 server/client 주 버전이 17이어야 합니다.
`pg_lsclusters`에 `17 main`이 없다면 다른 PostgreSQL 주 버전의 기존 cluster와 포트 충돌이
없는지 확인한 뒤 테스트용 cluster를 생성합니다.

```bash
sudo pg_createcluster 17 main --start
pg_lsclusters
```

#### 9.2.3 서비스 시작

```bash
sudo systemctl enable --now postgresql
pg_isready -h 127.0.0.1 -p 5432
```

`pg_isready`가 `accepting connections`를 반환해야 합니다. WSL에서 systemd를 사용할 수
없다는 오류가 발생하면 `/etc/wsl.conf`에 다음을 설정한 뒤 Windows PowerShell에서
`wsl --shutdown`을 실행하고 Ubuntu를 다시 엽니다.

```ini
[boot]
systemd=true
```

일회성 확인만 필요하면 `sudo service postgresql start`도 사용할 수 있지만, 재시작 후
자동 기동 여부는 별도로 확인합니다.

#### 9.2.4 테스트 Role·Database·extension 생성

아래 계정과 비밀번호는 로컬 테스트 전용입니다. 운영환경에서 재사용하지 않습니다.

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE ragtest LOGIN PASSWORD 'ragtest_password';
CREATE DATABASE ragdb OWNER ragtest ENCODING 'UTF8';
SQL

sudo -u postgres psql -v ON_ERROR_STOP=1 -d ragdb <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL
```

Role 또는 Database가 이미 있다는 오류가 발생하면 기존 로컬 테스트 DB를 계속 사용할지,
15.2절에 따라 초기화할지 먼저 결정합니다. 다른 용도의 동일 이름 DB를 삭제하지 않습니다.
명령에 표시된 비밀번호는 로컬 통합 테스트 전용이며, 실제 업무 데이터나 운영 서버에서는
승인된 비밀정보 관리 방식과 별도 비밀번호를 사용합니다.

#### 9.2.5 신규 DB 스키마 적용

```bash
cd ~/workspace/ecm-rag

PGPASSWORD=ragtest_password \
  psql -h 127.0.0.1 -p 5432 -U ragtest -d ragdb \
  -v ON_ERROR_STOP=1 \
  -f sql/001_pgvector.sql
```

기본 테스트 profile의 embedding 차원은 1024이며 SQL의 `vector(1024)`와 일치합니다.
다른 차원을 사용할 때는 YAML의 `models.embedding.dimension`을 변경한 뒤
`scripts/render_pgvector_sql.py`로 설치 SQL을 렌더링합니다.

검증:

```bash
PGPASSWORD=ragtest_password \
  psql -h 127.0.0.1 -p 5432 -U ragtest -d ragdb \
  -v ON_ERROR_STOP=1 <<'SQL'
SHOW server_version;
SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('vector', 'pg_trgm')
ORDER BY extname;
SELECT tablename
FROM pg_tables
WHERE schemaname = 'rag'
ORDER BY tablename;
SELECT vector_dims('[1,2,3]'::vector) AS test_dimensions;
SQL
```

서버 주 버전 17, 두 extension, `rag` 스키마 테이블과 `test_dimensions=3`을 확인합니다.

## 10. 테스트 환경 변수

현재 설정은 `.env`와 선택된 완결형 `config/profiles/*.yaml`만 사용합니다. 이전 배포본의
`config/settings.yaml`은 삭제하고 `.env`의 `RAG_CONFIG_FILE` 항목도 제거합니다.
업로드·변환 파일과 PostgreSQL 데이터는 삭제하지 않습니다.

각 터미널을 새로 열 때 다음 값을 설정합니다. DB 방식에 따라 DSN 포트만 다릅니다.

Docker DB:

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

export RAG_PROFILE=unit-test
export RAG_DB_DSN='postgresql://ragtest:ragtest_password@127.0.0.1:55432/ragdb'
```

WSL 네이티브 DB:

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

export RAG_PROFILE=unit-test
export RAG_DB_DSN='postgresql://ragtest:ragtest_password@127.0.0.1:5432/ragdb'
```

두 DB를 동시에 실행한 경우 현재 터미널이 선택한 서버를 확인합니다.

```bash
psql "$RAG_DB_DSN" -Atc \
  "SELECT current_database(), current_user, inet_server_port(), current_setting('server_version');"
```

`unit-test` profile의 특징:

- 실제 BGE-M3 대신 `deterministic_stub` embedding 사용
- 실제 Java 로그인 대신 `development_stub` 사용
- 로그인 테스트 계정: `test / test`
- 기본 인증 비활성: 로그인 없이 임시 사용자로 화면/API 사용
- DRM 비활성화
- reranker 비활성화
- 업로드 제한 10 MB

따라서 PostgreSQL, API, 세션, 업로드, worker 및 검색 흐름을 외부 모델 없이 검증할 수
있습니다.

## 11. 자동 테스트

같은 터미널에서 먼저 10절의 Docker 또는 WSL 네이티브 환경변수 블록을 실행합니다.

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

python -m compileall -q service tests
python -m pytest -q
```

모든 테스트가 passed여야 합니다. 실패 시 다음 순서로 확인합니다.

```bash
# Docker DB를 선택한 경우
docker ps --filter name=ecm-rag-it-postgres
docker logs --tail 100 ecm-rag-it-postgres

# WSL 네이티브 DB를 선택한 경우
pg_lsclusters
sudo journalctl -u postgresql --no-pager -n 100

psql "$RAG_DB_DSN" -c "SELECT version();"
python --version
git status
```

## 12. 서비스 수동 실행

각 터미널에서 프로세스를 실행하기 전에 10절의 동일한 DB 방식 환경변수 블록을 실행합니다.

### 12.1 터미널 1: FastAPI

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

python -m uvicorn service.api:app \
  --host 0.0.0.0 \
  --port 8300
```

### 12.2 터미널 2: worker

```bash
cd ~/workspace/ecm-rag
source .venv/bin/activate

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
{"status":"ok","profile":"unit-test"}
{"status":"ready","database":"postgresql"}
```

## 13. 로그인·업로드·검색 화면 확인

Windows 브라우저에서 다음 주소를 엽니다.

- 첫 화면/간편 업로드: `http://localhost:8300/`
- 간편 업로드 직접 경로: `http://localhost:8300/spl_upload.html`
- 상세 업로드: `http://localhost:8300/upload.html`
- 로그인 화면: 인증을 활성화한 경우 `http://localhost:8300/login`
- 문서 조회·질의응답: `http://localhost:8300/search.html`
- 장애 로그 해결 가이드: `http://localhost:8300/incident_guide.html`
- API 문서: `http://localhost:8300/docs`

현재 기본값에서는 로그인하지 않습니다. `/api/v1/auth/me`는 임시 사용자 정보를
반환합니다. 아래 계정은 `RAG_AUTH_ENABLED=true`로 인증을 활성화한 local/test/unit-test profile에서만
사용합니다.

테스트 로그인:

| 항목 | 값 |
|---|---|
| 사번 | `test` |
| 비밀번호 | `test` |

로그인 후 TXT 또는 MD 파일을 업로드합니다. 기존 문서 후보가 발견되면 화면에서 다음 중
하나를 선택합니다.

- 신규 Asset으로 등록
- 후보 Asset의 새 Version으로 등록
- 입력 내용을 수정하고 재확인

정상 처리 상태:

```text
queued → processing → completed
```

장애 해결 화면과 템플릿 저장소는 `sql/007_incident_guide_template.sql`과
`sql/008_incident_timestamp_template.sql`이 필요합니다.
`bin/start.sh` 또는 `python -m scripts.apply_migrations`가 기존 DB에 이를 자동 적용합니다.
인증 사용자는 로그 분석을 실행할 수 있고 관리자만 상단 공용 템플릿을 수정할 수 있습니다.
분석에 사용한 PostgreSQL/pgvector 청크는 화면이나 API 응답에 표시되지 않습니다.

검색 API 확인:

```bash
curl -X POST \
  --cookie-jar /tmp/ecm-rag-cookie.txt \
  --cookie /tmp/ecm-rag-cookie.txt \
  -H 'Content-Type: application/json' \
  -d '{"query":"ECM 문서 자산","limit":5}' \
  http://127.0.0.1:8300/api/v1/search
```

인증 활성화 이후 CLI에서 세션까지 시험하려면 먼저 로그인 쿠키를 받습니다.

```bash
curl -X POST \
  --cookie-jar /tmp/ecm-rag-cookie.txt \
  -H 'Content-Type: application/json' \
  -d '{"employee_id":"test","password":"test"}' \
  http://127.0.0.1:8300/api/v1/auth/login
```

현재 사용자 확인:

```bash
curl --cookie /tmp/ecm-rag-cookie.txt \
  http://127.0.0.1:8300/api/v1/auth/me
```

내부망 외부 연동 endpoint는 세션 없이 일반 문자열과 스택트레이스 JSON을 받을 수
있습니다. 로컬에서는 다음처럼 기능만 확인하고, 다른 PC에 개방할 때는 반드시 허용
대역 ACL을 적용합니다.

```bash
curl -X POST http://127.0.0.1:8300/api/v1/external/answer \
  -H 'Content-Type: application/json' \
  -d '{"query":"등록한 테스트 문서의 핵심 내용을 알려줘","limit":3}'

curl -X POST http://127.0.0.1:8300/api/v1/external/answer \
  -H 'Content-Type: application/json' \
  -d '{
    "query":"원인과 관련 소스를 알려줘",
    "stack_trace":"java.lang.NullPointerException: value\n at com.company.OrderService.load(OrderService.java:42)",
    "limit":5
  }'
```

## 14. 실제 모델·`login.py` 인증·DRM 연동

핵심 통합 테스트가 끝난 후 실제 환경을 연결합니다.

```bash
export RAG_PROFILE=test
export RAG_DB_DSN='postgresql://raguser:실제비밀번호@DB주소:5432/ragdb'

export RAG_EMBED_URL='http://임베딩서버:8200/v1/embeddings'
export RAG_EMBED_MODEL='bge-m3'
export RAG_EMBED_API_KEY='필요한경우만설정'

export RAG_LLM_URL='http://LLM서버:8000/v1'
export RAG_LLM_MODEL='실제모델명'
export RAG_LLM_API_KEY='필요한경우만설정'
export RAG_LLM_MAX_CONTINUATIONS='2'

# 로그분석 AI에서 대량 로그 중 LLM에 전달할 핵심 오류 구간 문자 수
export RAG_INCIDENT_LOG_CONTEXT_CHARACTERS='16000'

# PPTX 포함 이미지 및 PDF EasyOCR 장치: cpu, gpu, auto
export RAG_EASYOCR_DEVICE='cpu'
export RAG_DOCLING_ARTIFACTS='/경로/docling-models'

# service/login.py 구현·시험 후 활성화
export RAG_AUTH_ENABLED=true
# 기존 Java wrapper provider를 선택할 때만 설정
export RAG_AUTH_COMMAND='java -jar /경로/company-auth-adapter.jar'
export RAG_DRM_COMMAND='/경로/decrypt-wrapper'
export RAG_JAVA_COMMAND='/경로/jdk/bin/java'
export RAG_CFR_JAR_PATH='/경로/cfr-0.152.jar'
```

주의사항:

- BGE-M3 응답은 PostgreSQL 스키마와 같은 1024차원이어야 합니다.
- `RAG_EASYOCR_DEVICE=gpu`는 CUDA/MPS가 없으면 작업을 실패시킵니다. GPU worker마다
  EasyOCR 모델을 별도로 적재하므로 GPU 메모리에 맞춰 worker 수를 조정합니다.
- `test` profile은 쿠키 `Secure=false`라 HTTP 폐쇄망 테스트에만 사용합니다.
- Rocky Linux 운영 profile은 HTTPS와 `Secure=true` 쿠키를 전제로 합니다.
- 기본 로그인 구현 계약은 `LOGIN_INTEGRATION.md`를 따릅니다.
- 기존 Java wrapper를 선택할 때만 `JAVA_AUTH_INTEGRATION.md`를 따릅니다.
- DRM wrapper 계약은 README와 `service/drm.py`를 따릅니다.
- JAR 업로드는 자산유형 `source_code`, JDK/JRE와 CFR 파일이 필요합니다. 디컴파일 결과는
  원본 line과 다를 수 있으므로 검색 답변에서는 package/class/method를 우선 확인합니다.
- 비밀번호와 API key를 YAML이나 Git에 저장하지 않습니다.

## 15. 테스트 환경 중지·재시작·초기화

서비스 API와 worker는 각 터미널에서 `Ctrl+C`로 종료합니다.

### 15.1 Docker DB

중지와 재시작:

```bash
docker stop ecm-rag-it-postgres
```

```bash
docker start ecm-rag-it-postgres
```

컨테이너만 제거하려면 다음 명령을 실행합니다. bind mount의 DB 데이터는 삭제되지 않습니다.

```bash
docker rm -f ecm-rag-it-postgres
```

DB 데이터까지 초기화하려면 컨테이너 제거 후 정확한 로컬 데이터 디렉터리를 타임스탬프가
붙은 백업 경로로 이동합니다. 운영 DB 또는 다른 경로에는 실행하지 않습니다.

```bash
export ECM_RAG_PGDATA="$HOME/ecm-rag-data/postgresql17"
export ECM_RAG_PGDATA_BACKUP="$HOME/ecm-rag-data/postgresql17.backup-$(date +%Y%m%d-%H%M%S)"

test "$ECM_RAG_PGDATA" = "$HOME/ecm-rag-data/postgresql17"
test -d "$ECM_RAG_PGDATA"
mv -- "$ECM_RAG_PGDATA" "$ECM_RAG_PGDATA_BACKUP"
printf '기존 DB 데이터 백업: %s\n' "$ECM_RAG_PGDATA_BACKUP"
```

그 다음 9.1.2절부터 다시 진행합니다. 백업 디렉터리는 새 DB 검증이 끝난 후 보존 정책에 따라
정리합니다.

### 15.2 WSL 네이티브 DB

서비스 중지·시작·상태 확인:

```bash
sudo systemctl stop postgresql
sudo systemctl start postgresql
sudo systemctl status postgresql --no-pager
pg_lsclusters
```

테스트 DB와 Role을 완전히 초기화할 때만 다음을 실행합니다. `ragdb`의 모든 데이터가
삭제되므로 운영 DB나 다른 사용자가 쓰는 DB에는 절대 실행하지 않습니다.

```bash
sudo -u postgres dropdb --if-exists ragdb
sudo -u postgres dropuser --if-exists ragtest
```

그 다음 9.2.4절부터 다시 진행합니다. PostgreSQL 패키지까지 제거할 필요는 없습니다.

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
- Docker 방식을 선택한 경우 Docker Desktop 설치 파일과 사내 라이선스 승인
- GPU 사용 시 승인된 NVIDIA Windows/WSL driver

WSL과 선택한 DB 실행 방식의 버전은 사내 표준으로 고정합니다. Docker Desktop을 사용할
수 없으면 이 문서의 WSL 네이티브 PostgreSQL 방식을 사용할 수 있습니다. WSL 자체가
허용되지 않는 경우에는 사내 표준 Linux VM 또는 서버 DB 사용 여부를 인프라 담당자와
먼저 결정해야 합니다.

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

### 16.4 PostgreSQL 17·pgvector 오프라인 반입

DB 실행 방식에 따라 16.4.1 또는 16.4.2 중 하나를 수행합니다.

#### 16.4.1 Docker image 반입

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

이미지를 load한 다음 9.1.2절의 `docker run`부터 실행합니다. 사내 Docker registry가 있으면
보안 검증된 이미지를 registry에 등록하고 사내 주소에서 pull하는 방식을 우선합니다.

#### 16.4.2 WSL 네이티브 `.deb` 묶음 준비

가장 안전한 방법은 사내 Ubuntu/PGDG mirror에서 승인된 패키지를 설치하는 것입니다. mirror가
없다면 인터넷 연결 준비 PC에 **대상 PC와 같은 Windows WSL 버전, Ubuntu 24.04 image,
CPU architecture 및 업데이트 수준**의 깨끗한 Ubuntu WSL 인스턴스를 별도로 준비합니다.
이미 PostgreSQL이나 다른 개발 패키지가 많이 설치된 PC에서 수집하면 의존 `.deb`가 빠질 수
있습니다.

준비 PC의 Ubuntu 24.04 WSL에서 OS와 architecture를 기록합니다.

```bash
. /etc/os-release
test "$VERSION_CODENAME" = "noble"
dpkg --print-architecture
uname -a
```

PostgreSQL 공식 PGDG 저장소를 준비 PC에만 등록합니다. 아래 키와 저장소 URL은 대상 폐쇄망
PC에서 호출하지 않습니다.

```bash
sudo apt update
sudo apt install -y curl ca-certificates
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
  --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc

cat <<'EOF' | sudo tee /etc/apt/sources.list.d/pgdg.sources
Types: deb
URIs: https://apt.postgresql.org/pub/repos/apt
Suites: noble-pgdg
Architectures: amd64
Components: main
Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
EOF

sudo apt update
```

ARM64 환경이면 `Architectures: amd64`를 `Architectures: arm64`로 바꾸고 대상 PC도 같은
architecture인지 확인합니다.

준비 PC가 아직 깨끗한 상태에서 PostgreSQL 17, 클라이언트, pgvector와 현재 설치되지 않은
의존 패키지를 APT cache에 다운로드합니다.

```bash
sudo apt clean
sudo apt-get install --download-only -y \
  postgresql-17 \
  postgresql-client-17 \
  postgresql-17-pgvector

mkdir -p ~/ecm-rag-offline/postgresql17-native/debs
sudo cp /var/cache/apt/archives/*.deb \
  ~/ecm-rag-offline/postgresql17-native/debs/
sudo chown -R "$(id -u):$(id -g)" \
  ~/ecm-rag-offline/postgresql17-native
```

`apt-get --download-only`는 준비 PC에 이미 설치된 의존 패키지를 다시 받지 않습니다. 따라서
준비 PC는 대상 PC와 동일한 깨끗한 WSL image 및 업데이트 기준이어야 합니다. 이 조건을
만족시킬 수 없으면 사내 APT mirror 또는 승인된 repository snapshot을 사용합니다.

대상 PC가 인터넷 없이 APT 의존성을 계산할 수 있도록 준비 PC에서 flat repository index를
생성합니다. 이 작업은 `.deb` 다운로드가 끝난 다음 수행합니다.

```bash
sudo apt install -y dpkg-dev
cd ~/ecm-rag-offline/postgresql17-native/debs
dpkg-scanpackages . /dev/null > Packages
gzip -9c Packages > Packages.gz

cd ..
apt-cache policy \
  postgresql-17 \
  postgresql-client-17 \
  postgresql-17-pgvector \
  > PACKAGE_VERSIONS.txt
find . -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  > SHA256SUMS
```

최소 반입 구조는 다음과 같습니다.

```text
postgresql17-native/
├── SHA256SUMS
├── PACKAGE_VERSIONS.txt
└── debs/
    ├── Packages
    ├── Packages.gz
    ├── postgresql-17_*.deb
    ├── postgresql-client-17_*.deb
    ├── postgresql-17-pgvector_*.deb
    └── 의존 패키지 *.deb
```

대상 폐쇄망 PC에서는 체크섬 검증 후 flat repository만을 명시적으로 사용합니다. 반입 경로는
공백이 없는 절대경로를 사용합니다.

```bash
export ECM_RAG_PG_BUNDLE=/반입경로/postgresql17-native
cd "$ECM_RAG_PG_BUNDLE"
sha256sum --check SHA256SUMS

printf 'deb [trusted=yes] file:%s/debs ./\n' "$ECM_RAG_PG_BUNDLE" \
  | sudo tee /etc/apt/sources.list.d/ecm-rag-postgresql-local.list

sudo apt-get \
  -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/ecm-rag-postgresql-local.list \
  -o Dir::Etc::sourceparts=- \
  update

sudo apt-get \
  -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/ecm-rag-postgresql-local.list \
  -o Dir::Etc::sourceparts=- \
  install -y \
  postgresql-17 \
  postgresql-client-17 \
  postgresql-17-pgvector

sudo rm -f /etc/apt/sources.list.d/ecm-rag-postgresql-local.list
```

`trusted=yes`는 서명 검증을 생략하므로 승인된 반입 절차와 `SHA256SUMS` 검증을 완료한 이
로컬 묶음에만 제한해서 사용합니다. 설치 후 9.2.2절의 버전 확인부터 계속합니다. 보안
업데이트도 자동으로 내려오지 않으므로 준비 PC에서 동일 절차로 새 묶음을 만들고 정기적으로
재반입해야 합니다.

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

PostgreSQL 17과 pgvector 네이티브 설치 패키지는 일반 OS 도구 묶음과 섞지 말고 16.4.2절의
별도 flat repository 절차를 사용합니다. PostgreSQL 패키지 버전과 보안 패치 이력은
`PACKAGE_VERSIONS.txt`와 반입 승인 기록으로 관리합니다.

### 16.8 모델과 외부 솔루션 artifact

다음 파일은 Python wheelhouse나 Git 저장소에 자동으로 포함되지 않습니다.

- BGE-M3 embedding 모델 또는 사내 embedding API 배포본
- BGE reranker 모델
- Docling 모델 artifact
- EasyOCR 모델 파일(설정으로 RapidOCR를 선택할 때는 해당 모델도 별도 반입)
- JDK/JRE와 검증된 CFR JAR(`RAG_JAVA_COMMAND`, `RAG_CFR_JAR_PATH`)
- `service/login.py`가 사용하는 사내 인증 SDK/인증서 또는 선택적인 Java adapter
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
├── pgvector-pg17.tar                 # Docker DB 선택 시
├── postgresql17-native/              # WSL 네이티브 DB 선택 시
│   ├── SHA256SUMS
│   ├── PACKAGE_VERSIONS.txt
│   └── debs/
│       ├── Packages.gz
│       └── *.deb
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
- [ ] 선택한 DB 방식 확인: Docker 또는 Ubuntu WSL 네이티브
- [ ] Docker 선택 시 `docker version` 성공 및 컨테이너 상태가 healthy
- [ ] Docker 선택 시 DB bind mount가 지정한 WSL 로컬 경로를 가리킴
- [ ] 네이티브 선택 시 PostgreSQL server/client 주 버전 17 및 서비스 active
- [ ] 폐쇄망 네이티브 선택 시 `.deb` 묶음 SHA-256 검증 및 반입 버전 기록 완료
- [ ] GitHub의 시험 대상 브랜치와 로컬 HEAD가 일치함
- [ ] `python --version`이 3.11.x
- [ ] vector와 pg_trgm 확장이 설치됨
- [ ] 최신 `001_pgvector.sql` 적용 완료
- [ ] 전체 pytest 통과
- [ ] `/health/live`가 200 반환
- [ ] `/health/ready`가 200 반환
- [ ] 임시 사용자로 첫 화면·업로드·검색 성공
- [ ] 인증 시험 시 `RAG_AUTH_ENABLED=true`와 개발 stub `test/test` 로그인 성공
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
- PostgreSQL Ubuntu/PGDG 설치: https://www.postgresql.org/download/linux/ubuntu/
- PostgreSQL PGDG APT 저장소: https://wiki.postgresql.org/wiki/Apt
- pgvector APT 설치: https://github.com/pgvector/pgvector#apt
- pgvector Docker 이미지: https://github.com/pgvector/pgvector
- PyTorch 설치 선택기: https://pytorch.org/get-started/locally/
