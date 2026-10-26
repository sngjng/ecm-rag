# Rocky Linux 9 폐쇄망 Podman PostgreSQL·pgvector 설치 가이드

## 1. 목적과 기준 구성

이 문서는 새로운 폐쇄망 Rocky Linux 9 테스트 서버에 기존 ECM-RAG 테스트 DB와 동일한
PostgreSQL 17·pgvector 환경을 rootless Podman으로 구축하는 절차입니다. 애플리케이션과
DB 컨테이너는 같은 서버에서 실행하는 구성을 기준으로 합니다.

| 항목 | 기준값 |
|---|---|
| OS | Rocky Linux 9 x86_64 |
| 컨테이너 실행 계정 | `sngjng` 일반 계정 |
| 컨테이너 이미지 | `docker.io/pgvector/pgvector:pg17` |
| 컨테이너 이름 | `ecm-rag-it-postgres` |
| PostgreSQL 컨테이너 포트 | `5432` |
| 호스트 접속 주소 | `127.0.0.1:55432` |
| 테스트 DB·계정 | `ragdb` / `ragtest` |
| 호스트 데이터 경로 | `/home/sngjng/ecm-rag-data/postgresql17` |
| 필수 확장 | `vector`, `pg_trgm` |
| 기본 embedding 차원 | `vector(1024)` |

`127.0.0.1`에만 포트를 게시하므로 DB는 회사 네트워크의 다른 PC에 직접 노출되지 않습니다.
외부 사용자는 FastAPI의 8300 포트로 접근하고, 동일 서버의 애플리케이션만 PostgreSQL에
접속합니다. DB를 다른 서버에 설치하는 구성은 방화벽·`pg_hba.conf`·TLS 정책을 별도로
설계해야 합니다.

아래의 `ragtest_password`는 기존 테스트 환경과 맞춘 예시입니다. 신규 환경에서는 별도
비밀번호로 변경하고 프로젝트 `.env`의 DSN에도 같은 값을 사용합니다.

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
- DB 데이터와 백업을 저장할 충분한 `/home` 공간
- 최초 RPM 설치, subuid/subgid 설정 및 linger 활성화에 사용할 관리자 지원
- 이후 컨테이너는 `sngjng` 일반 계정으로 실행

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

`pg17` 태그는 향후 다른 빌드를 가리킬 수 있으므로 **현재 검증이 끝난 테스트 환경의
이미지를 export하는 방법을 우선**합니다. 기존 환경이 Docker이면 다음과 같이 저장합니다.

```bash
mkdir -p "$HOME/ecm-rag-offline/images"

docker image inspect pgvector/pgvector:pg17 \
  --format 'image_id={{.Id}} digests={{json .RepoDigests}}'
docker save \
  --output "$HOME/ecm-rag-offline/images/pgvector-pg17.tar" \
  pgvector/pgvector:pg17
```

기존 검증 환경도 Podman이면 다음 명령을 사용합니다.

```bash
mkdir -p "$HOME/ecm-rag-offline/images"

podman image inspect docker.io/pgvector/pgvector:pg17 \
  --format 'image_id={{.Id}} digests={{json .RepoDigests}}'

podman save --format docker-archive \
  --output "$HOME/ecm-rag-offline/images/pgvector-pg17.tar" \
  docker.io/pgvector/pgvector:pg17
```

기존 검증 이미지를 가져올 수 없을 때만 인터넷 연결 서버에서 새로 pull합니다. 이 경우
새 이미지로 회귀 테스트가 필요합니다.

```bash
podman pull docker.io/pgvector/pgvector:pg17
podman image inspect docker.io/pgvector/pgvector:pg17 \
  --format 'image_id={{.Id}} digests={{json .RepoDigests}}'
podman save --format docker-archive \
  --output "$HOME/ecm-rag-offline/images/pgvector-pg17.tar" \
  docker.io/pgvector/pgvector:pg17
```

어느 방법을 사용하든 archive의 체크섬을 생성합니다.

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

관리자가 `sngjng`의 subordinate UID/GID 범위를 확인합니다.

```bash
grep '^sngjng:' /etc/subuid
grep '^sngjng:' /etc/subgid
```

출력이 없다면 다른 사용자 범위와 겹치지 않는 값을 관리자가 배정합니다. 아래 범위는
예시이며 기존 서버 정책을 우선합니다.

```bash
sudo usermod --add-subuids 100000-165535 sngjng
sudo usermod --add-subgids 100000-165535 sngjng
```

변경 후 `sngjng` 세션을 완전히 종료했다가 다시 로그인하고 확인합니다.

```bash
podman info --format '{{.Host.Security.Rootless}}'
```

`true`가 출력되어야 합니다.

## 5. 이미지 반입과 검증

이 절부터는 별도 안내가 없는 한 `sngjng` 계정으로 실행합니다.

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

DB 데이터는 컨테이너 writable layer가 아닌 호스트 bind mount에 저장합니다. 따라서
컨테이너를 다시 만들어도 같은 경로를 연결하면 데이터가 유지됩니다.

```bash
export ECM_RAG_PGDATA=/home/sngjng/ecm-rag-data/postgresql17
export ECM_RAG_PG_ENV=/home/sngjng/.config/ecm-rag/postgres.env

install -d -m 700 "$ECM_RAG_PGDATA"
install -d -m 700 /home/sngjng/.config/ecm-rag

umask 077
vi "$ECM_RAG_PG_ENV"
```

`postgres.env`에는 다음 값을 저장합니다.

```dotenv
POSTGRES_USER=ragtest
POSTGRES_PASSWORD=ragtest_password
POSTGRES_DB=ragdb
```

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

```bash
podman run -d \
  --name ecm-rag-it-postgres \
  --pull=never \
  --restart=unless-stopped \
  -p 127.0.0.1:55432:5432 \
  --env-file /home/sngjng/.config/ecm-rag/postgres.env \
  -v /home/sngjng/ecm-rag-data/postgresql17:/var/lib/postgresql/data:Z \
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

## 8. ECM-RAG 스키마와 마이그레이션

### 8.1 기본 `001_pgvector.sql`

신규 DB에는 프로젝트의 기본 스키마를 먼저 적용합니다. 이 파일이 embedding 차원
`vector(1024)`와 `vector`, `pg_trgm` 확장을 생성합니다.

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

embedding 모델의 차원이 1024가 아니라면 원본 SQL을 직접 수정하지 말고
`config/settings.yaml`의 `models.embedding.dimension`을 설정한 후 렌더링 파일을
적용합니다.

```bash
source /mnt/appl/venv/venv_ecmrag/bin/activate
python scripts/render_pgvector_sql.py \
  --output artifacts/sql/001_pgvector.rendered.sql

podman cp artifacts/sql/001_pgvector.rendered.sql \
  ecm-rag-it-postgres:/tmp/001_pgvector.rendered.sql
podman exec ecm-rag-it-postgres \
  psql -v ON_ERROR_STOP=1 -U ragtest -d ragdb \
  -f /tmp/001_pgvector.rendered.sql
```

1024차원 기본 모델을 사용할 때는 렌더링 과정이 필요하지 않습니다.

### 8.2 증분 마이그레이션

프로젝트 루트 `/mnt/appl/ecm-rag/.env`에 다음 DB 설정을 포함합니다.

```dotenv
RAG_PROFILE=test
RAG_DB_DSN=postgresql://ragtest:ragtest_password@127.0.0.1:55432/ragdb
ECM_RAG_PYTHON=/mnt/appl/venv/venv_ecmrag/bin/python
ECM_RAG_USER=sngjng
```

비밀번호에 `@`, `:`, `/`, `#`, `%` 같은 문자가 있으면 DSN의 비밀번호를 URL encoding해야
합니다. `.env`에는 실제 환경의 LLM, embedding, Java, CFR 경로도 함께 설정합니다.

기본 스키마 적용 후 현재 프로젝트의 `002`부터 `006`까지를 확인합니다. 각 SQL은 반복
실행 가능한 형태이며 `bin/start.sh`도 서비스 시작 전에 같은 검사를 수행합니다.

```bash
cd /mnt/appl/ecm-rag
set -a
source .env
set +a

"$ECM_RAG_PYTHON" -m scripts.apply_migrations
```

예상 출력:

```text
적용 완료: 002_session_auth_audit.sql
적용 완료: 003_code_symbol_stack_lookup.sql
적용 완료: 004_eai_login_identity.sql
적용 완료: 005_user_access_roles.sql
적용 완료: 006_rename_general_role_to_viewer.sql
```

`001_pgvector.sql`이 없는 DB에서는 마이그레이션 명령이 의도적으로 실패합니다.

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
sudo loginctl enable-linger sngjng
```

`sngjng` 계정에서 Podman restart unit의 존재 여부를 확인하고 활성화합니다.

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

## 12. 백업과 복구

### 12.1 논리 백업

```bash
install -d -m 700 /home/sngjng/ecm-rag-backup
export ECM_RAG_DB_BACKUP="/home/sngjng/ecm-rag-backup/ragdb-$(date +%Y%m%d-%H%M%S).dump"

podman exec ecm-rag-it-postgres \
  pg_dump -U ragtest -d ragdb --format=custom \
  > "$ECM_RAG_DB_BACKUP"

test -s "$ECM_RAG_DB_BACKUP"
sha256sum "$ECM_RAG_DB_BACKUP" > "$ECM_RAG_DB_BACKUP.sha256"
printf 'backup=%s\n' "$ECM_RAG_DB_BACKUP"
```

### 12.2 복구

복구는 대상 DB를 변경하므로 API와 worker를 먼저 중지하고 백업 파일과 대상 DB를
재확인합니다.

```bash
cd /mnt/appl/ecm-rag
./bin/stop.sh

sha256sum --check /복구경로/ragdb.dump.sha256

podman exec -i ecm-rag-it-postgres \
  pg_restore -U ragtest -d ragdb \
  --clean --if-exists --no-owner \
  < /복구경로/ragdb.dump

./bin/start.sh
```

PostgreSQL 실행 중 bind mount 파일을 직접 복사하면 일관되지 않은 백업이 될 수 있으므로
온라인 파일 복사를 백업으로 사용하지 않습니다.

## 13. 장애 점검

### 13.1 컨테이너가 시작되지 않음

```bash
podman ps -a --filter name=ecm-rag-it-postgres
podman logs --tail 200 ecm-rag-it-postgres
podman inspect ecm-rag-it-postgres --format '{{.State.Error}}'
```

- `permission denied`: 6절의 `podman unshare chown`과 mount의 `:Z` 확인
- `address already in use`: `ss -lntp | grep 55432`로 포트 점유 확인
- registry 접속 시도: 이미지 load 여부를 확인하고 `--pull=never` 사용
- 기존 데이터의 DB 버전 불일치: 데이터를 삭제하지 말고 먼저 백업·복구 계획 수립

### 13.2 애플리케이션에서 Connection refused

```bash
podman ps --filter name=ecm-rag-it-postgres
podman inspect ecm-rag-it-postgres \
  --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}'
grep '^RAG_DB_DSN=' /mnt/appl/ecm-rag/.env
```

동일 서버의 rootless Podman 구성에서는 다음 DSN을 사용합니다.

```dotenv
RAG_DB_DSN=postgresql://ragtest:ragtest_password@127.0.0.1:55432/ragdb
```

### 13.3 `vector` extension 오류

일반 PostgreSQL 이미지를 사용하면 `vector` extension 파일이 없습니다. 이미지가 반드시
`pgvector/pgvector:pg17`인지 확인합니다.

```bash
podman inspect ecm-rag-it-postgres --format '{{.ImageName}}'
podman exec ecm-rag-it-postgres \
  psql -U ragtest -d ragdb -c \
  "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name='vector';"
```

### 13.4 env 비밀번호 변경 후 로그인이 안 됨

`POSTGRES_PASSWORD`는 비어 있는 데이터 경로를 처음 초기화할 때만 반영됩니다. 기존 DB의
비밀번호는 SQL `ALTER ROLE`로 변경하고 애플리케이션 `.env`와 Podman env 파일을 함께
갱신해야 합니다. 비밀번호를 명령행이나 로그에 출력하지 않습니다.

## 14. 완료 체크리스트

- [ ] Rocky Linux 9 버전과 architecture 기록
- [ ] Podman RPM 및 이미지 archive SHA-256 검증
- [ ] rootless Podman 확인
- [ ] bind mount와 SELinux `:Z` 확인
- [ ] 컨테이너 health 상태 `healthy`
- [ ] PostgreSQL 주 버전 17 확인
- [ ] `vector`, `pg_trgm` 확장 확인
- [ ] `rag.chunks.embedding`이 `vector(1024)`인지 확인
- [ ] `001_pgvector.sql` 및 `002`~`006` 적용
- [ ] 애플리케이션 DB 연결 확인
- [ ] `/health/live`, `/health/ready` 성공
- [ ] TXT 업로드·worker 완료·검색 왕복 시험
- [ ] 재부팅 후 Podman DB와 ECM-RAG 기동 시험
- [ ] `pg_dump` 백업 생성 및 복구 리허설

## 15. 공식 참고 자료

- Podman rootless mode: <https://docs.podman.io/en/stable/markdown/podman.1.html#rootless-mode>
- Podman restart policy: <https://docs.podman.io/en/stable/markdown/options/restart.html>
- Podman volume의 rootless UID와 SELinux `:Z`: <https://docs.podman.io/en/stable/markdown/options/volume.html>
- pgvector 공식 이미지와 PostgreSQL별 tag: <https://github.com/pgvector/pgvector#docker>
