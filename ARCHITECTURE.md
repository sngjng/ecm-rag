# 아키텍처

## 경계

서비스는 API, 처리 Worker, PostgreSQL, Java 인증·DRM 및 외부 모델 adapter 경계로 나뉩니다.
API는 대용량 파싱을 수행하지 않고 파일 저장과 DB 작업 등록까지만 담당합니다. Worker는
독립 프로세스로 `ingestion_jobs`를 확보해 파싱부터 적재까지 수행합니다.

사용자는 Java 사내 인증 adapter를 통해 로그인합니다. FastAPI는 PostgreSQL 서버 세션과
HttpOnly 쿠키로 요청을 인증하고, 문서 접수 시 사번·성명·부서 snapshot을 감사 이력에
Version/Job과 원자적으로 저장합니다.

## 데이터 모델

```text
Asset
 ├─ Asset Version
 │   ├─ Ingestion Job
 │   ├─ Chunk ─ Embedding
 │   ├─ Error Event ─ Stack Frame
 │   └─ Code Symbol
 └─ Asset Relation ─ Asset
Auth Session ─ Employee snapshot
Upload Audit ─ Asset Version + Employee snapshot
```

- Asset: 문서·장애·로그·코드의 논리적 정체성과 ECM metadata
- Asset Version: 원본 파일, checksum, lifecycle, 현재 버전, parser lineage
- Chunk: `display_content`와 metadata를 포함한 `embedding_content`를 분리
- Relation: 문서·장애·코드 사이의 references/resolved_by/applies_to 관계
- Auth Session: 해시된 세션 토큰, 사원정보 snapshot, 만료·폐기 상태
- Upload Audit: 문서 접수자와 신규/버전등록 구분을 보존하는 감사 이력

## Worker 복구

작업 확보는 `FOR UPDATE SKIP LOCKED`로 이루어집니다. 처리 중 worker는 heartbeat를
갱신하며, lease가 만료된 작업은 다른 worker가 재획득합니다. YAML의 `max_attempts`를
소진하면 작업은 `failed`로 종료되고 API를 통해 명시적으로 재시도할 수 있습니다.

## 검색

1. 에러코드·예외·파일·심볼 식별자를 exact/substring으로 검색
2. PostgreSQL `simple` FTS로 lexical 후보 검색
3. 같은 embedding 모델로 질문을 벡터화해 cosine 후보 검색
4. 세 레인을 RRF로 결합
5. 선택적으로 BGE reranker 적용
6. 결과 청크의 앞뒤 문맥을 함께 반환

기본 검색은 `is_current=true`, `lifecycle_status=approved`를 적용하며 과거 분석 요청에서만
`include_obsolete=true`로 완화합니다.

## 설정

코드에는 환경별 경로·포트·모델명을 두지 않습니다. `config/settings.yaml`을 기본으로
`config/profiles/{RAG_PROFILE}.yaml`을 deep-merge한 뒤 환경 변수 표현식을 치환합니다.
DSN, 외부 모델 API 키, Java 인증 wrapper 경로처럼 민감하거나 서버별인 값만 환경 변수로 주입합니다.
