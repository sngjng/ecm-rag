# Rocky Linux 9 폐쇄망 Podman PostgreSQL·pgvector 신규 설치 가이드

## 1. 목적과 기준 구성

이 문서는 새로운 폐쇄망 Rocky Linux 9 테스트 서버에 빈 PostgreSQL 17·pgvector 환경을
rootless Podman으로 신규 구축하는 절차입니다. 기존 Docker DB의 데이터, volume 또는 dump는
이전하지 않습니다. 애플리케이션과 DB 컨테이너는 같은 서버에서 실행하는 구성을 기준으로
합니다.

이 작업은 Python·FastAPI 소스를 변경하지 않습니다. Podman DB는 `10.2.12.98:55432`,
`ragdb`, `ragtest` 규격으로 만들고 애플리케이션은 `.env`의 `RAG_DB_DSN`으로 연결합니다.
따라서 소스 수정 없이 환경 설정만으로 연결할 수 있습니다.

| 항목 | 기준값 |
|---|---|
| OS | Rocky Linux 9 x86_64 |
| 컨테이너 실행 계정 | `gptoss` 일반 계정 |
| 컨테이너 이미지 | `docker.io/pgvector/pgvector:pg17` |
| 컨테이너 이름 | `ecm-rag-it-postgres` |
| PostgreSQL 컨테이너 포트 | `5432` |
| 호스트 접속 주소 | `10.2.12.98:55432` |
| 데이터베이스 | `ragdb` |
| DB 사용자 | `ragtest` |
| DB 비밀번호 | `rag12#$` |
| 호스트 데이터 경로 | `/LLMChatbot/pgdata/postgresql17` |
| 필수 확장 | `vector`, `pg_trgm` |
| 기본 embedding 차원 | `vector(1024)` |

`10.2.12.98` 인터페이스에 DB 포트를 게시하므로 회사 네트워크에서 해당 주소로 접근할 수
있습니다. 방화벽에서 55432 포트의 출발지 대역을 ECM-RAG 서버와 허용된 관리 PC로 제한해야
합니다. 외부 사용자는 PostgreSQL에 직접 접속하지 않고 FastAPI의 8300 포트를 사용합니다.

비밀번호의 `#`, `$`는 DSN 예약문자와 shell 확장 문제를 피하기 위해 `RAG_DB_DSN`에서
각각 `%23`, `%24`로 URL encoding합니다. Podman의 `postgres.env`에는 원문 비밀번호를
그대로 기록합니다.

## 2. 사전 확인

대상 서버에서 OS, architecture와 저장공간을 확인합니다.

```bash
cat /etc/rocky-release
uname -m
df -h /home
id
```

다음 조건을 충족해야 합니다.

- Rocky Linux 9 계열이며 준비 PC와 대상 PC의 architecture가 동일함
- 신규 DB 데이터를 저장할 충분한 `/LLMChatbot` filesystem 공간
- 최초 RPM 설치, subuid/subgid 설정 및 linger 활성화에 사용할 관리자 지원
- 이후 컨테이너는 `gptoss` 일반 계정으로 실행

## 3. 폐쇄망 반입 자료 준비

### 3.1 Podman RPM 묶음

인터넷이 가능한 **동일한 Rocky Linux 9 minor 버전·architecture**의 준비 서버에서
Podman과 rootless 실행 의존성을 내려받습니다. 다른 OS에서 받은 RPM을 혼합하지 않습니다.

```bash
sudo dnf install -y dnf-plugins-core

mkdir -p "$HOME/ecm-rag-offline/podman-rpms"
cd "$HOME/ecm-rag-offline/podman-rpms"

dnf download --resolve --alldeps --destdir . \
  podman fuse-overlayfs slirp4netns shadow-utils-subid

sha256sum ./*.rpm > SHA256SUMS
```

Rocky Linux 버전, `uname -m`, RPM 목록과 `SHA256SUMS`를 함께 반입합니다. 사내 표준 RPM
repository가 있다면 별도 RPM 묶음 대신 해당 repository를 우선 사용합니다.

### 3.2 PostgreSQL·pgvector 이미지

인터넷이 가능한 Rocky Linux 9 준비 서버에 Podman을 설치하고 PostgreSQL 17·pgvector
이미지를 내려받습니다. Docker는 사용하지 않습니다.

```bash
mkdir -p "$HOME/ecm-rag-offline/images"

podman pull docker.io/pgvector/pgvector:pg17
podman image inspect docker.io/pgvector/pgvector:pg17 \
  --format 'image_id={{.Id}} digests={{json .RepoDigests}}'

podman save --format docker-archive \
  --output "$HOME/ecm-rag-offline/images/pgvector-pg17.tar" \
  docker.io/pgvector/pgvector:pg17
```

`pg17` 태그는 향후 다른 빌드를 가리킬 수 있으므로 출력된 image ID와 digest를 구축 기록에
남기고 archive의 체크섬을 생성합니다.

```bash
cd "$HOME/ecm-rag-offline/images"
sha256sum pgvector-pg17.tar > pgvector-pg17.tar.sha256
```

폐쇄망에는 다음 묶음을 반입합니다.

```text
podman-rpms/
  *.rpm
  SHA256SUMS
images/
  pgvector-pg17.tar
  pgvector-pg17.tar.sha256
ecm-rag/
  현재 배포 대상 프로젝트 전체
```

## 4. 폐쇄망 서버에 Podman 설치

반입한 RPM의 체크섬을 검증하고 로컬 RPM만 사용해 설치합니다.

```bash
cd /반입경로/podman-rpms
sha256sum --check SHA256SUMS

sudo dnf install -y --disablerepo='*' ./*.rpm

podman --version
podman info
```

의존성 누락이 보고되면 임의의 외부 RPM을 추가하지 말고 준비 서버에서 같은 Rocky Linux
repository 기준으로 누락 RPM을 다시 수집합니다.

### 4.1 rootless 사용자 매핑

관리자가 `gptoss`의 subordinate UID/GID 범위를 확인합니다.

```bash
grep '^gptoss:' /etc/subuid
grep '^gptoss:' /etc/subgid
```

출력이 없다면 다른 사용자 범위와 겹치지 않는 값을 관리자가 배정합니다. 아래 범위는
예시이며 기존 서버 정책을 우선합니다.

```bash
sudo usermod --add-subuids 100000-165535 gptoss
sudo usermod --add-subgids 100000-165535 gptoss
```

변경 후 `gptoss` 세션을 완전히 종료했다가 다시 로그인하고 확인합니다.

```bash
podman info --format '{{.Host.Security.Rootless}}'
```

`true`가 출력되어야 합니다.

## 5. 이미지 반입과 검증

이 절부터는 별도 안내가 없는 한 `gptoss` 계정으로 실행합니다.

```bash
cd /반입경로/images
sha256sum --check pgvector-pg17.tar.sha256

podman load --input pgvector-pg17.tar
podman image inspect docker.io/pgvector/pgvector:pg17 \
  --format 'image_id={{.Id}} created={{.Created}}'
```

외부 registry 접근 없이 이미지가 실행되는지 확인합니다.

```bash
podman run --rm --pull=never \
  docker.io/pgvector/pgvector:pg17 \
  postgres --version
```

archive가 다른 이름으로 등록되었다면 `podman images`에서 확인한 정확한 이름을 이후 명령에
사용합니다.

## 6. 데이터 경로와 비밀 설정

호스트 데이터는 PostgreSQL의 테이블, pgvector 값, 인덱스와 트랜잭션 로그가 실제로
저장되는 로컬 디스크 데이터입니다. 컨테이너 writable layer가 아닌 host bind mount에
저장하므로 컨테이너를 다시 만들어도 같은 경로를 연결하면 DB가 유지됩니다. 업로드 원본,
변환 산출물, API·worker 로그는 이 경로에 저장되지 않습니다.

```bash
export ECM_RAG_PGDATA=/LLMChatbot/pgdata/postgresql17
export ECM_RAG_PG_ENV=/home/gptoss/.config/ecm-rag/postgres.env

sudo install -d -o gptoss -g gptoss -m 700 "$ECM_RAG_PGDATA"
install -d -m 700 /home/gptoss/.config/ecm-rag

umask 077
vi "$ECM_RAG_PG_ENV"
```

`postgres.env`에는 다음 값을 저장합니다.

```dotenv
POSTGRES_USER=ragtest
POSTGRES_PASSWORD=rag12#$
POSTGRES_DB=ragdb
```

이 파일은 Podman의 `--env-file` 전용입니다. 비밀번호 원문에 특수문자가 있으므로 shell에서
`source`하지 않습니다.

```bash
chmod 600 "$ECM_RAG_PG_ENV"
ls -l "$ECM_RAG_PG_ENV"
```

이미지 내부 `postgres` 계정의 UID/GID를 확인하고 rootless user namespace 안에서 bind mount
소유권을 맞춥니다. 호스트 전체 UID를 임의로 `chown`하지 않습니다.

```bash
export ECM_RAG_POSTGRES_UID="$(podman run --rm --pull=never \
  docker.io/pgvector/pgvector:pg17 id -u postgres)"
export ECM_RAG_POSTGRES_GID="$(podman run --rm --pull=never \
  docker.io/pgvector/pgvector:pg17 id -g postgres)"

podman unshare chown -R \
  "$ECM_RAG_POSTGRES_UID:$ECM_RAG_POSTGRES_GID" \
  "$ECM_RAG_PGDATA"
```

Rocky Linux 9의 SELinux가 enforcing이어도 접근할 수 있도록 컨테이너 bind mount에는
반드시 `:Z` 옵션을 사용합니다.

## 7. PostgreSQL·pgvector 컨테이너 생성

먼저 같은 이름의 컨테이너가 있는지 확인합니다.

```bash
podman ps -a --filter name=ecm-rag-it-postgres
```

기존 컨테이너가 있다면 새로 생성하지 않습니다. 기존 데이터가 필요한 상태에서 컨테이너나
데이터 디렉터리를 삭제하면 안 됩니다. 신규 서버에서 출력이 없을 때만 실행합니다.

호스트가 요청받은 IP를 실제로 보유하는지 먼저 확인합니다.

```bash
ip -4 address show | grep -F '10.2.12.98'
```

출력이 없으면 컨테이너를 실행하지 말고 서버 IP를 네트워크 관리자에게 확인합니다.

```bash
podman run -d \
  --name ecm-rag-it-postgres \
  --pull=never \
  --restart=unless-stopped \
  -p 10.2.12.98:55432:5432 \
  --env-file /home/gptoss/.config/ecm-rag/postgres.env \
  -v /LLMChatbot/pgdata/postgresql17:/var/lib/postgresql/data:Z \
  --health-cmd='pg_isready -U ragtest -d ragdb' \
  --health-interval=2s \
  --health-timeout=3s \
  --health-retries=30 \
  docker.io/pgvector/pgvector:pg17
```

초기화 로그와 health 상태를 확인합니다.

```bash
podman logs --tail 100 ecm-rag-it-postgres
podman ps --filter name=ecm-rag-it-postgres
podman inspect ecm-rag-it-postgres \
  --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}healthcheck-not-configured{{end}}'
podman inspect ecm-rag-it-postgres \
  --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```

health 상태가 `healthy`, mount 대상이 `/var/lib/postgresql/data`여야 합니다. 비밀번호는
최초 초기화에만 사용되므로 기존 데이터 경로를 유지한 채 env 파일만 바꿔도 DB 비밀번호가
자동 변경되지 않습니다.

55432 포트가 요청한 인터페이스에만 게시됐는지도 확인합니다.

```bash
ss -lnt | grep '10.2.12.98:55432'
```

## 8. 빈 DB에 ECM-RAG 스키마 생성

이 절은 기존 DB 데이터를 옮기는 마이그레이션이 아닙니다. 새로 만든 빈 `ragdb`에 현재
애플리케이션이 요구하는 테이블, 인덱스, `vector(1024)`, `vector`, `pg_trgm` 확장을 생성하는
초기화 절차입니다. 기존 Docker DB, dump 또는 volume은 사용하지 않습니다.

```bash
cd /mnt/appl/ecm-rag

podman cp sql/001_pgvector.sql \
  ecm-rag-it-postgres:/tmp/001_pgvector.sql

podman exec ecm-rag-it-postgres \
  psql -v ON_ERROR_STOP=1 \
  -U ragtest \
  -d ragdb \
  -f /tmp/001_pgvector.sql
```

현재 애플리케이션과 같은 1024차원 embedding 구성이므로 SQL이나 Python 코드를 수정하지
않습니다. 프로젝트 루트 `/mnt/appl/ecm-rag/.env`의 기존 DB 설정이 다음과 같은지만
확인합니다.

```dotenv
RAG_PROFILE=test
RAG_DB_DSN=postgresql://ragtest:rag12%23%24@10.2.12.98:55432/ragdb
ECM_RAG_PYTHON=/mnt/appl/venv/venv_ecmrag/bin/python
ECM_RAG_USER=gptoss
```

이미 같은 값이면 `.env`도 수정하지 않습니다. `bin/start.sh`는 현재 소스에 포함된 호환성
DDL을 기동 전에 반복 안전하게 확인합니다. 이는 기존 데이터를 이전하는 작업이 아니며,
별도의 수동 데이터 마이그레이션 명령은 실행하지 않습니다.

## 9. 설치 결과 검증

PostgreSQL 버전, 확장, 테이블과 vector 차원을 확인합니다.

```bash
podman exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb -c 'SELECT version();'

podman exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb -c \
  "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_trgm') ORDER BY extname;"

podman exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb -c \
  "SELECT tablename FROM pg_tables WHERE schemaname='rag' ORDER BY tablename;"

podman exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb -c \
  "SELECT format_type(atttypid, atttypmod) AS embedding_type FROM pg_attribute WHERE attrelid='rag.chunks'::regclass AND attname='embedding';"
```

마지막 결과는 기본 구성에서 `vector(1024)`여야 합니다. 애플리케이션 연결도 같은 설정
로더를 통해 확인합니다.

```bash
cd /mnt/appl/ecm-rag
set -a
source .env
set +a

"$ECM_RAG_PYTHON" - <<'PY'
from service.db import connect

with connect() as connection:
    row = connection.execute(
        "SELECT current_database() AS database, current_user AS username"
    ).fetchone()
    print(row)
PY
```

서비스를 시작하고 readiness를 확인합니다.

```bash
./bin/start.sh
curl -fsS http://127.0.0.1:8300/health/live
curl -fsS http://127.0.0.1:8300/health/ready
```

## 10. 재부팅 후 자동 시작

컨테이너에는 `--restart=unless-stopped` 정책이 설정되어 있습니다. rootless 컨테이너가 사용자
로그인 전에도 재시작되게 하려면 관리자가 한 번만 linger를 활성화합니다.

```bash
sudo loginctl enable-linger gptoss
```

`gptoss` 계정에서 Podman restart unit의 존재 여부를 확인하고 활성화합니다.

```bash
systemctl --user list-unit-files podman-restart.service
systemctl --user enable podman-restart.service
```

설치된 Podman 패키지가 user용 `podman-restart.service`를 제공하지 않는다면 운영 표준에
맞는 Quadlet user unit을 사용하거나 재부팅 후 다음 순서로 수동 기동합니다.

```bash
podman start ecm-rag-it-postgres
cd /mnt/appl/ecm-rag
./bin/start.sh
```

재부팅 시험 후 실제 자동 기동 여부를 확인합니다.

```bash
podman ps --filter name=ecm-rag-it-postgres
podman inspect ecm-rag-it-postgres \
  --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}'
```

## 11. 운영 명령과 로그

```bash
# 상태
podman ps -a --filter name=ecm-rag-it-postgres

# 시작·중지
podman start ecm-rag-it-postgres
podman stop --time 60 ecm-rag-it-postgres

# PostgreSQL 로그
podman logs --tail 200 ecm-rag-it-postgres
podman logs --follow ecm-rag-it-postgres

# DB 내부 접속
podman exec -it ecm-rag-it-postgres psql -U ragtest -d ragdb

# 데이터 경로
podman inspect ecm-rag-it-postgres \
  --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```

`podman logs`는 PostgreSQL 서버 로그입니다. ECM-RAG 로그는 별도 파일입니다.

```text
/mnt/appl/ecm-rag/artifacts/logs/api.log
/mnt/appl/ecm-rag/artifacts/logs/worker.log
```

## 12. 장애 점검

### 12.1 컨테이너가 시작되지 않음

```bash
podman ps -a --filter name=ecm-rag-it-postgres
podman logs --tail 200 ecm-rag-it-postgres
podman inspect ecm-rag-it-postgres --format '{{.State.Error}}'
```

- `permission denied`: 6절의 `podman unshare chown`과 mount의 `:Z` 확인
- `address already in use`: `ss -lntp | grep 55432`로 포트 점유 확인
- registry 접속 시도: 이미지 load 여부를 확인하고 `--pull=never` 사용
- 기존 컨테이너 이름 충돌: 신규 테스트 서버인지 확인하고 기존 컨테이너를 임의 삭제하지 않음

### 12.2 애플리케이션에서 Connection refused

```bash
podman ps --filter name=ecm-rag-it-postgres
podman inspect ecm-rag-it-postgres \
  --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}'
grep '^RAG_DB_DSN=' /mnt/appl/ecm-rag/.env
```

동일 서버의 rootless Podman 구성에서는 다음 DSN을 사용합니다.

```dotenv
RAG_DB_DSN=postgresql://ragtest:rag12%23%24@10.2.12.98:55432/ragdb
```

### 12.3 `vector` extension 오류

일반 PostgreSQL 이미지를 사용하면 `vector` extension 파일이 없습니다. 이미지가 반드시
`pgvector/pgvector:pg17`인지 확인합니다.

```bash
podman inspect ecm-rag-it-postgres --format '{{.ImageName}}'
podman exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb -c \
  "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name='vector';"
```

### 12.4 env 비밀번호 변경 후 로그인이 안 됨

`POSTGRES_PASSWORD`는 비어 있는 데이터 경로를 처음 초기화할 때만 반영됩니다. 기존 DB의
비밀번호는 SQL `ALTER ROLE`로 변경하고 애플리케이션 `.env`와 Podman env 파일을 함께
갱신해야 합니다. 비밀번호를 명령행이나 로그에 출력하지 않습니다.

## 13. 완료 체크리스트

- [ ] Rocky Linux 9 버전과 architecture 기록
- [ ] Podman RPM 및 이미지 archive SHA-256 검증
- [ ] rootless Podman 확인
- [ ] bind mount와 SELinux `:Z` 확인
- [ ] 컨테이너 health 상태 `healthy`
- [ ] PostgreSQL 주 버전 17 확인
- [ ] `vector`, `pg_trgm` 확장 확인
- [ ] `rag.chunks.embedding`이 `vector(1024)`인지 확인
- [ ] 빈 DB에 최신 `001_pgvector.sql` 적용
- [ ] 애플리케이션 소스와 기존 `.env` 미변경 확인
- [ ] 애플리케이션 DB 연결 확인
- [ ] `/health/live`, `/health/ready` 성공
- [ ] TXT 업로드·worker 완료·검색 왕복 시험
- [ ] 재부팅 후 Podman DB와 ECM-RAG 기동 시험

## 14. 공식 참고 자료

- Podman rootless mode: <https://docs.podman.io/en/stable/markdown/podman.1.html#rootless-mode>
- Podman restart policy: <https://docs.podman.io/en/stable/markdown/options/restart.html>
- Podman volume의 rootless UID와 SELinux `:Z`: <https://docs.podman.io/en/stable/markdown/options/volume.html>
- pgvector 공식 이미지와 PostgreSQL별 tag: <https://github.com/pgvector/pgvector#docker>
