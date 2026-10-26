# ECM 문서 자산화 플랫폼

Python 3.11 기반의 폐쇄망 ECM(Enterprise Content Management) 문서 자산화
플랫폼입니다. 조직에서 생성·수집되는 비정형 문서를 표준화된 자산으로 등록하고,
원본과 변환 산출물, 메타데이터, 처리 이력 및 검색 인덱스를 일관되게 관리하는 것이
이 프로젝트의 목적입니다.

RAG는 보험약관 질의응답 같은 특정 업무를 위한 최종 목적이 아니라, 자산화된 ECM
문서를 정확하게 탐색하고 향후 사내 LLM·OpenWebUI 등에서 재사용할 수 있도록 하는
검색 기술 계층입니다. PostgreSQL 17의 관계형 데이터, Full Text Search, `pg_trgm`,
`pgvector`를 하나의 문서 자산 원장으로 사용합니다.

## 프로젝트 목표

- 사내 문서와 기술 자료를 단일 업로드 경로로 접수하고 자산 식별자를 부여
- 원본 문서, DRM 처리 결과, canonical artifact와 검색 청크의 계보(lineage) 보존
- 문서 유형별 파싱·정규화·청킹을 통한 재사용 가능한 표준 데이터 생성
- 문서 메타데이터, 버전, 처리 상태, 오류 및 재처리 이력을 PostgreSQL에서 관리
- 키워드·식별자·벡터 기반 통합 검색으로 ECM 문서의 발견성과 활용성 향상
- API와 worker를 분리하여 접수 서비스와 대용량 문서 처리 프로세스를 독립 운영
- 환경별 차이를 YAML profile로 분리하여 폐쇄망 운영환경의 변경 비용 최소화

## 프로젝트 범위

이 프로젝트는 특정 보험상품이나 보험약관을 대상으로 답변을 생성하는 서비스가
아닙니다. ECM 문서의 수집, 변환, 구조화, 보존, 추적, 검색 및 외부 서비스 연계를 위한
공통 기반을 구축합니다. 현재 구현에는 검색 근거 기반 LLM 답변 화면과 내부 시스템용
질의응답 API까지 포함되며, 업무별 프롬프트나 별도 UI는 동일 검색 API 위에 확장할 수
있습니다.

## 처리 대상

- 사내 규정·지침·보고서·계약서·업무 문서: 원본과 Docling JSON을 자산으로 보존
- 운영 매뉴얼·제조사 가이드·기술 문서: 문서 구조와 표·문단 정보를 정규화
- 장애 이력: 증상·원인·조치·결과를 구조화하고 의미 검색용 문장을 별도 생성
- 에러 로그·스택 트레이스: 에러코드 exact 검색과 frame 위치 검색
- Python/Java/SQL/JavaScript/Shell/YAML: AST 또는 파일 단위 코드 검색
- Java JAR: CFR 디컴파일 후 package/class/method 심볼과 벡터 검색

## 실행 구조

```text
로그인 / 업로드 / 검색 / 관리자 HTML / OpenWebUI / 내부 시스템
        │
        ▼
FastAPI
  ├─ /api/v1/assets       Asset CRUD
  ├─ /api/v1/ingestion    파일/작업 접수
  ├─ /api/v1/search       ECM 자산 통합 검색
  ├─ /api/v1/chat         검색 근거 기반 LLM 질의응답
  ├─ /api/v1/admin        RDB 자산·pgvector 청크·처리 작업 관리
  ├─ /api/v1/external/answer  내부망 무인증 질의·스택트레이스 분석
  └─ /api/v1/assets/.../download  원본 다운로드(인증 활성화 시 세션 검증)
        │
        ▼
PostgreSQL queue ◀──── 별도 Python worker
                         │
                         ├─ Java DRM adapter
                         ├─ 유형별 parser/chunker
                         ├─ BGE-M3 embedding API
                         └─ PostgreSQL + pgvector 적재
```

편집 가능한 전체 구성도는 [ARCHITECTURE_EDITABLE.mmd](ARCHITECTURE_EDITABLE.mmd),
설계 설명은 [ARCHITECTURE.md](ARCHITECTURE.md), 변경 작업 기록은
[WORKLOG_PGVECTOR.md](WORKLOG_PGVECTOR.md)를 참고하세요. 업로드 화면의 입력 필드부터
호출 API, DB queue, worker와 검색까지 이어지는 코드 리뷰용 업무 흐름은
[SCREEN_API_WORKFLOW.md](SCREEN_API_WORKFLOW.md)에 정리되어 있습니다.

회사망 PC 최초 설치, 연동 설정, 기동·점검·백업·장애 대응 및 소스 디버깅 절차는
[운영자 매뉴얼](OPERATIONS_MANUAL.md)을 기준 문서로 사용하세요.

## 설치

Rocky Linux 9의 Python 3.11 가상환경에서 대상 서버의 CPU/CUDA에 맞는 PyTorch wheel을
먼저 준비한 뒤 의존성을 수작업으로 설치합니다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

PostgreSQL 17에서 DBA 권한으로 다음 스키마를 적용합니다. `vector(1024)`는 기본
BGE-M3 차원과 일치합니다. 다른 차원의 embedding 모델을 사용할 때는 스키마의
`vector(N)`도 일치해야 합니다. YAML profile에서 차원을 변경한 뒤 설치 SQL을 렌더링할
수 있습니다.

Rocky Linux 9 폐쇄망 테스트 서버에서 PostgreSQL 17·pgvector를 rootless Podman으로
구축할 때는 이미지/RPM 반입, bind mount, SELinux, migration, 자동기동과 백업 절차를 포함한
[`PODMAN_POSTGRESQL_PGVECTOR_SETUP.md`](PODMAN_POSTGRESQL_PGVECTOR_SETUP.md)를 따릅니다.

```bash
python scripts/render_pgvector_sql.py --output artifacts/sql/001_pgvector.rendered.sql
psql -d ragdb -f artifacts/sql/001_pgvector.rendered.sql
```

## 환경 설정

일반 설정은 YAML에서, DB 비밀번호와 외부 모델 주소 등 서버별 값은 환경 변수에서 관리합니다.

사내 EAI 로그인은 `service/login.py`에 JPype 방식으로 구현되어 있습니다. 다만 사내망과
Java 라이브러리가 없는 환경에서는 검증할 수 없으므로 기본값은 계속
`RAG_AUTH_ENABLED=false`입니다. 이 경우
첫 화면 `/`에서 `spl_upload.html`을 바로 표시하고 세션 없이 업로드·검색을 사용할 수
있습니다. 업로드 감사 이력에는 `temporary / 임시 사용자 / IDC본부`가 기록됩니다.
사내 테스트 환경에서 Java 경로를 설정하고 `.env`의 `RAG_AUTH_ENABLED=true`로 변경하면
로그인·HttpOnly 세션 검증이 적용됩니다. 임시 모드에서는 8300 포트를 승인된 사내 대역에만
노출해야 합니다.

```bash
export RAG_PROFILE=rockylinux9
export RAG_DB_DSN='postgresql://raguser:비밀번호@127.0.0.1:5432/ragdb'
export RAG_AUTH_ENABLED=false
export RAG_AUTH_JVM_PATH='/Product/jdk/open/jdk-21.0.11+10/lib/server/libjvm.so'
export RAG_AUTH_JAVA_DIR='/Appl/chatllm/java'
export RAG_AUTH_COMMAND='/srv/ecm-rag/bin/company-auth-wrapper'
export RAG_RERANK_MODEL_PATH='/srv/models/bge-reranker-v2-m3'
export RAG_DOCLING_ARTIFACTS='/srv/models/docling'
export RAG_DRM_COMMAND='/srv/drm/bin/decrypt-wrapper'
export RAG_JAVA_COMMAND='java'
export RAG_CFR_JAR_PATH='/srv/ecm-rag/tools/cfr-0.152.jar'
```

JAR 업로드는 Python 패키지를 추가로 설치하지 않습니다. JDK의 `java` 실행 파일과
별도로 반입한 CFR JAR가 필요합니다. 업로드 화면에서 자산유형을 `소스 코드`로 선택하면
`.jar`를 접수할 수 있으며, worker가 클래스를 실행하지 않고 디컴파일한 Java 소스를
클래스·메서드 단위로 임베딩합니다. 질문에 Java stack trace를 붙여 넣으면 검색기가
`package + class + method`를 `rag.code_symbols`와 먼저 정확 매칭한 뒤 FTS·pgvector
결과와 결합합니다. JAR 디컴파일 소스와 단일 `.java` 파일은 동일한 공통 분석기를
사용하여 `throws`, `throw new`, `catch`, Exception/Error/Throwable 유형과 업무 오류
코드를 청크 metadata 및 embedding 문맥에 함께 저장합니다.

기존 문서 후보는 동일 SHA-256, 동일 `repository + logical_path`, 동일 파일명 또는
대소문자·공백을 정규화한 정확한 제목 일치로만 제시합니다. 제목이 비슷하다는 이유만으로
후보를 만들거나 자동 병합하지 않으며 사용자가 새 버전과 별도 신규 자산 중 하나를
확인합니다.

- 공통값: `config/settings.yaml`
- 로컬 개발: `config/profiles/local.yaml`
- Rocky Linux 9: `config/profiles/rockylinux9.yaml`
- 테스트: `config/profiles/test.yaml`
- 별도 설정 파일: `RAG_CONFIG_FILE=/etc/ecm-rag/settings.yaml`

## 프로세스별 실행

### sngjng 계정 일괄 실행

API와 Worker는 sudo 없이 전용 스크립트로 함께 시작·종료할 수 있습니다. 스크립트는
자신의 위치를 기준으로 프로젝트 루트를 계산하므로 실행 디렉터리와 무관합니다.

```bash
cd /mnt/c/Users/HDS/Documents/ChatGPT/ecm-rag
chmod 750 bin/start.sh bin/stop.sh

bin/start.sh
bin/stop.sh
```

- 기본 실행 계정: `sngjng`
- PID: `artifacts/run/`
- 로그: `artifacts/logs/api.log`, `artifacts/logs/worker.log`
- 선택 환경변수: `ECM_RAG_HOST`, `ECM_RAG_PORT`, `ECM_RAG_PYTHON`, `ECM_RAG_STOP_TIMEOUT`
- 동시성 환경변수: `ECM_RAG_API_WORKERS`, `ECM_RAG_API_LIMIT_CONCURRENCY`,
  `ECM_RAG_API_BACKLOG`, `ECM_RAG_WORKER_PROCESSES`
- `.env`가 있으면 시작 시 자동으로 읽습니다.
- 시작 전에 `sql/002_session_auth_audit.sql`과 `003_code_symbol_stack_lookup.sql`을
  idempotent하게 자동 적용합니다.
- 원본 파일은 `server.max_upload_mb` 설정에 따라 현재 1건당 최대 5MiB까지 허용합니다.

### 다중 사용자 부하 보호

기본값은 API worker 2개, 문서 처리 worker 2개입니다. API 프로세스마다 동시 요청을
100건으로 제한하고 대기 socket backlog는 256으로 제한합니다. PostgreSQL의
`queued + processing` 작업이 100건에 도달하면 신규 업로드를 HTTP 429와
`Retry-After` 헤더로 거부하여 파일 저장소와 처리 서버를 보호합니다.

문서 처리 worker는 작업을 한 건씩 가져가며 PostgreSQL `FOR UPDATE SKIP LOCKED`로
서로 다른 작업을 안전하게 나눠 처리합니다. 동일 SHA-256 원본이 동시에 접수되면
PostgreSQL advisory lock으로 한 요청만 등록 단계에 진입시키고 나머지는 재시도 가능한
HTTP 409로 응답합니다. 서버 사양에 따라 YAML 기본값 또는 위 환경변수를 조정합니다.

### 개별 실행

```bash
# API: 파일 저장과 PostgreSQL 작업 등록까지만 수행
python -m uvicorn service.api:app --host 0.0.0.0 --port 8300

# Worker: DRM, 파싱, 청킹, 임베딩, 적재를 독립 수행
python -m service.worker
```

상태 확인:

```bash
curl http://127.0.0.1:8300/health/live
curl http://127.0.0.1:8300/health/ready
```

현재 임시 모드의 첫 화면 `/`은 간편 업로드이며 `/login`도 `/`로 이동합니다. 간편
업로드는 `/spl_upload.html`, 상세 업로드는 `/upload.html`, 문서 질의응답은
`/search.html`, 운영 데이터 관리는 `/admin.html`에서 사용합니다. 업로드·검색 화면의
데이터 관리 링크는 기본적으로 숨겨지고 `/api/v1/auth/me`의 `roles`에 `admin`이 있는
사용자에게만 표시됩니다. 인증을 활성화하면
`/login`에서 로그인 후 간편 업로드로 이동합니다. 기존
DB에는 `sql/002_session_auth_audit.sql`과
`sql/003_code_symbol_stack_lookup.sql`을 추가 적용해야 합니다.

인증을 활성화한 local/test profile의 화면 확인 계정은 `test / test`입니다. 운영
`rockylinux9` profile에서는 개발 계정이 비활성화되고 기본 `python_module` provider가
`service/login.py`를 호출합니다.

`admin.html`의 화면 제목은 **데이터 관리**이며, `assets` 메타데이터의
생성·조회·수정·논리 삭제·복구, Version 조회,
`chunks`의 생성·조회·수정·물리 삭제, 실패 작업 재시도를 지원합니다. 청크 본문을 등록하거나
수정하면 임베딩 서버를 호출해 pgvector를 자동 생성하므로 사용자가 raw vector를 직접
입력하지 않습니다. 자산 삭제는 `deleted_at`만 기록하므로 Version과 청크의 embedding은
DB에 보존되고 일반 검색에서만 제외됩니다. 청크 물리 삭제는 해당 행의 embedding과 연결된
오류 이벤트·스택 프레임·코드 심볼도 함께 삭제합니다.
권한은 PostgreSQL의 `user_access_roles`에서 관리합니다. 뷰어는 조회 화면, 업로더는
관리자 화면을 제외한 기능, 관리자는 모든 화면과 권한 관리를 사용할 수 있습니다.
미등록 사원은 업로더 권한이 적용되며 권한 변경 시 기존 세션은 즉시 폐기됩니다. 최초
관리자는 `.env`의 `RAG_AUTH_ADMIN_EMPLOYEE_IDS`에 사번을 쉼표로 구분해 지정합니다.
인증 비활성 임시 사용자는 업로더 권한으로 동작하며 관리자 화면/API에는 접근할 수 없습니다.

원본과 변환 산출물은 PostgreSQL 데이터 디렉터리와 별도로 저장됩니다.

| profile | 원본 | 변환·디컴파일 산출물 |
|---|---|---|
| local/default | `artifacts/uploads` | `artifacts/processed` |
| test | `artifacts/test/uploads` | `artifacts/test/processed` |
| rockylinux9 | `/srv/ecm-rag/uploads` | `/srv/ecm-rag/processed` |

PostgreSQL의 실제 데이터 디렉터리는 DB 설치 방식이 관리하며 `SHOW data_directory;`로
확인합니다. DB 백업과 위 파일 경로 백업은 같은 시점의 세트로 보관해야 합니다.

## 검색

```bash
curl -b session.cookie \
  -H 'Content-Type: application/json' \
  -d '{"query":"전자결재 연계 운영지침과 관련 장애 이력","limit":5}' \
  http://127.0.0.1:8300/api/v1/search
```

검색은 식별자 exact/substring, PostgreSQL FTS, pgvector cosine 후보를 RRF로 합친 뒤
`retrieval.max_vector_distance` 관련성 기준을 적용합니다. 기준을 통과한 문서가 없으면
일반대화로 판단해 LLM을 호출하지 않고 문서 관련 질문만 지원한다는 안내를 반환합니다.
YAML에서 활성화한 경우에만 로컬 reranker를 적용합니다. 최신·승인 버전 우선 정책도
YAML로 조정할 수 있습니다. `/api/v1/chat`은 이 검색 결과를 로컬 OpenAI 호환 LLM에
전달해 출처 번호가 포함된 답변과 근거 검색 결과를 함께 반환합니다. LLM 장애 시에도
검색 결과는 유지되며 `generation_status=unavailable`로 상태를 구분합니다.

LLM 연결 정보와 생성 제한은 코드가 아닌 `config/settings.yaml` 및 환경변수로 관리합니다.
`context_window_tokens`는 실제 모델 context와 동일하게 설정해야 하며, 서비스는
`max_tokens`, `prompt_safety_tokens`, 최근 대화 이력을 제외한 남은 예산만 검색 근거에
사용합니다. 그래도 모델이 context 초과를 반환하면 이력을 제거하고 근거를 절반으로
줄여 한 번 자동 재시도합니다.

```bash
export RAG_LLM_URL='http://LLM서버:포트/v1'
export RAG_LLM_MODEL='운영-모델명'
export RAG_LLM_API_KEY='필요한 경우에만 설정'
```

검색 결과의 문서 카드를 클릭하면 해당 버전의 원본을 다운로드합니다. 인증 활성화 후에는
세션 쿠키도 확인합니다.
서버는 DB 경로를 그대로 신뢰하지 않고 `paths.upload_root` 내부 파일인지 재검증합니다.

### 내부망 외부 시스템 질의응답 API

`POST /api/v1/external/answer`는 세션 쿠키나 API 키 없이 내부 시스템에서 호출하는
연동 endpoint입니다. Rocky Linux profile은 `0.0.0.0`으로 수신하며 실제 접근 범위는
내부망 방화벽과 reverse proxy에서 제한합니다. JSON 객체, JSON 문자열, 일반 문자열을
모두 받을 수 있고 PostgreSQL/pgvector 검색 결과를 근거로 LLM 답변을 반환합니다.
복사해서 실행할 수 있는 정상·스택트레이스·입력 오류별 curl 명령은
[외부 API curl 테스트 문서](EXTERNAL_ANSWER_CURL_TEST.md)를 참고하세요.

```bash
# JSON 객체: 검색 필터와 결과 제한도 함께 전달 가능
curl -X POST http://서버주소:8300/api/v1/external/answer \
  -H 'Content-Type: application/json' \
  -d '{"query":"장애 대응 절차를 알려줘","limit":5}'

# 일반 문자열
curl -X POST http://서버주소:8300/api/v1/external/answer \
  -H 'Content-Type: text/plain; charset=utf-8' \
  --data-binary '장애 대응 절차를 알려줘'

# 스택트레이스 분석: query에는 담당자가 원하는 분석 방향을 선택적으로 지정
curl -X POST http://서버주소:8300/api/v1/external/answer \
  -H 'Content-Type: application/json' \
  -d '{
    "query":"원인과 관련 소스, 조치 방법을 알려줘",
    "stack_trace":"java.lang.NullPointerException: value\\n at com.company.OrderService.load(OrderService.java:42)",
    "limit":5
  }'
```

JSON 요청에서 `include_chunks=true`(기본값)를 사용하면 응답에 `answer`,
`generation_status`, `results`가 포함됩니다. `false`이면 동일하게 검색 청크로 LLM 답변을
생성하되 HTTP 응답에서 `results`만 제외합니다. 응답의 `chunks_included`로 실제 반환 여부를
확인할 수 있습니다. `results`는 답변에 사용한 문서 청크와 자산·버전·페이지 등의 출처
정보입니다. 스택트레이스로 판별되면 추가로
`request_type=stack_trace`와 `stack_trace_analysis`가 반환됩니다. 분석 결과에는 fingerprint,
주 예외, 최하위 원인, 오류 코드, 전체 호출 프레임 및 애플리케이션 호출 프레임이 포함됩니다.
원문을 `query` 또는 `text/plain`으로 직접 보내도 자동 판별되지만, 외부 시스템 연동에서는
질문과 원문을 구분할 수 있는 `stack_trace` 필드 사용을 권장합니다.

답변 근거에 JAR 디컴파일 소스가 포함되면 `notices`와 `answer` 본문에 원본 소스 라인
번호와 디컴파일 결과의 라인 범위가 다를 수 있다는 안내가 함께 반환됩니다. 이 경우
라인 번호보다 패키지·클래스·메서드를 우선 기준으로 확인합니다.

실행 중인 외부 endpoint의 지원 입력·오류 응답을 한 번에 확인하려면 표준 라이브러리 기반
테스트 스크립트를 사용합니다. 기본 실행은 12개 케이스를 검사하며, 등록 문서가 반드시
검색되어야 하는 인수시험에서는 실제 문서의 고유 문구를 `--query`에 지정하고
`--expect-results`를 추가합니다.

```bash
cd /mnt/appl/ecm-rag
set -a
source .env
set +a

# 케이스 목록만 확인
"$ECM_RAG_PYTHON" scripts/test_external_answer.py --list

# 전체 계약 시험
"$ECM_RAG_PYTHON" scripts/test_external_answer.py \
  --base-url http://127.0.0.1:8300

# 등록 자료 검색까지 필수 확인
"$ECM_RAG_PYTHON" scripts/test_external_answer.py \
  --base-url http://127.0.0.1:8300 \
  --query "실제로 등록한 문서의 고유 문구" \
  --expect-results
```

로그인부터 검색까지 한 번에 확인하려면 API 호출 예제를 실행합니다. 비밀번호는 명령행
인자 대신 환경변수 또는 보안 입력으로 전달합니다.

```bash
ECM_RAG_PASSWORD=test .venv/bin/python scripts/query_uploaded_documents.py \
  "의료비 선택적복리후생" --asset-type document --limit 3
```

## `login.py` 사내 EAI 로그인

FastAPI의 `POST /api/v1/auth/login`은 `service/login.py`에서 JPype로 사내 EAI Java VO를
호출합니다. 정상 응답의 `emp_no`, `user_id`, `user_name`, `dept_name`, `up_dept_name`을
PostgreSQL 세션에 snapshot으로 저장하고 업로드 감사 이력에는 사번·성명·부서를 사용합니다.
설정·migration·활성화 절차는
[`LOGIN_INTEGRATION.md`](LOGIN_INTEGRATION.md)를 참고하세요. 기존 Java wrapper가 필요한
환경은 선택적으로 [`JAVA_AUTH_INTEGRATION.md`](JAVA_AUTH_INTEGRATION.md)를 사용할 수 있습니다.

## DRM 계약

`solutions.drm.enabled=true`일 때 설정된 실행 파일에 `<input_path> <output_path>` 두
인자를 전달합니다. 사용자가 Java 라이브러리로 구현할 wrapper는 성공 시 exit code 0과
비어 있지 않은 출력 파일을 반환해야 합니다. 복호화 임시 파일은 worker가 삭제하지만,
canonical artifact와 DB 청크에는 평문이 남으므로 별도 접근·보존 정책이 필요합니다.
