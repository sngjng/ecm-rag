# ECM RAG PostgreSQL Service

Python 3.11 기반의 폐쇄망 문서 자산화·검색 서비스입니다. PostgreSQL 17의 관계형
데이터, Full Text Search, `pg_trgm`, `pgvector`를 하나의 원장으로 사용합니다.

## 처리 대상

- 운영 매뉴얼·제조사 가이드·보험약관: Docling JSON을 canonical artifact로 보존
- 장애 이력: 증상·원인·조치·결과를 구조화하고 의미 검색용 문장을 별도 생성
- 에러 로그·스택 트레이스: 에러코드 exact 검색과 frame 위치 검색
- Python/Java/SQL/JavaScript/Shell/YAML: AST 또는 파일 단위 코드 검색

## 실행 구조

```text
업로드 HTML / OpenWebUI
        │
        ▼
FastAPI
  ├─ /api/v1/assets       Asset CRUD
  ├─ /api/v1/ingestion    파일/작업 접수
  └─ /api/v1/search       hybrid retrieval
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
[WORKLOG_PGVECTOR.md](WORKLOG_PGVECTOR.md)를 참고하세요.

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

```bash
python scripts/render_pgvector_sql.py --output artifacts/sql/001_pgvector.rendered.sql
psql -d ragdb -f artifacts/sql/001_pgvector.rendered.sql
```

## 환경 설정

일반 설정은 YAML에서, 비밀번호와 API 키는 환경 변수에서 관리합니다.

```bash
export RAG_PROFILE=rockylinux9
export RAG_DB_DSN='postgresql://raguser:비밀번호@127.0.0.1:5432/ragdb'
export RAG_API_KEY='내부-서비스-키'
export RAG_RERANK_MODEL_PATH='/srv/models/bge-reranker-v2-m3'
export RAG_DOCLING_ARTIFACTS='/srv/models/docling'
export RAG_DRM_COMMAND='/srv/drm/bin/decrypt-wrapper'
```

- 공통값: `config/settings.yaml`
- 로컬 개발: `config/profiles/local.yaml`
- Rocky Linux 9: `config/profiles/rockylinux9.yaml`
- 테스트: `config/profiles/test.yaml`
- 별도 설정 파일: `RAG_CONFIG_FILE=/etc/ecm-rag/settings.yaml`

## 프로세스별 실행

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

업로드 화면은 `http://서버:8300/`에서 사용합니다.

## 검색

```bash
curl -H 'X-API-Key: 내부-서비스-키' \
  -H 'Content-Type: application/json' \
  -d '{"query":"SRVE0255E 과거 장애와 관련 코드","limit":5}' \
  http://127.0.0.1:8300/api/v1/search
```

검색은 식별자 exact/substring, PostgreSQL FTS, pgvector cosine 후보를 RRF로 합친 뒤
YAML에서 활성화한 경우에만 로컬 reranker를 적용합니다. 최신·승인 버전 우선 정책도
YAML로 조정할 수 있습니다.

## DRM 계약

`solutions.drm.enabled=true`일 때 설정된 실행 파일에 `<input_path> <output_path>` 두
인자를 전달합니다. 사용자가 Java 라이브러리로 구현할 wrapper는 성공 시 exit code 0과
비어 있지 않은 출력 파일을 반환해야 합니다. 복호화 임시 파일은 worker가 삭제하지만,
canonical artifact와 DB 청크에는 평문이 남으므로 별도 접근·보존 정책이 필요합니다.
