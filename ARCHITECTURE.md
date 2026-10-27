# 아키텍처

최종 현행화: 2026-10-22

## 경계

서비스는 API, 처리 Worker, PostgreSQL, `login.py` 인증·Java DRM 및 외부 모델 adapter
경계로 나뉩니다. 기존 Java 인증 wrapper는 선택 provider로만 유지합니다.
API는 대용량 파싱을 수행하지 않고 파일 저장과 DB 작업 등록까지만 담당합니다. Worker는
독립 프로세스로 `ingestion_jobs`를 확보해 파싱부터 적재까지 수행합니다.

기본값은 실제 사내망 검증 전의 임시 운영 모드로, 공통 `authorize` 의존성이 세션 대신
설정된 임시 사용자 정보를 반환합니다. 따라서 문서 접수 시에도 임시 사번·성명·부서
snapshot을 Version/Job과 원자적으로 감사 이력에 저장합니다. `service/login.py`에는
JPype/EAI Java VO 인증이 구현되어 있으며, 사내 환경 설정 후 `RAG_AUTH_ENABLED=true`로
전환하면 PostgreSQL 서버 세션과 HttpOnly 쿠키 검증을 같은 route에 적용합니다.

임시 모드에서는 브라우저 API도 세션 없이 사용할 수 있습니다. 인증 활성화 후에는
브라우저의 `/api/v1/chat` 등 일반 API가 세션 인증을 사용하고, 사내 다른 시스템용
`/api/v1/external/answer`만 계속 세션/API 키 없이 열립니다. 두 모드 모두 인터넷 공개를
전제로 하지 않으며 방화벽 또는 reverse proxy의 사내 CIDR ACL이 필요합니다.

## 화면과 API 경계

```text
/                       기본 간편 업로드(spl_upload.html)
/login                  임시 모드: / 이동 / 인증 모드: login.py 연동 화면
/spl_upload.html        간편 단건 업로드(자산유형·제목·원본파일)
/upload.html            상세 업로드(upload.html)
/search.html            검색 근거 기반 대화·원문 다운로드
/admin.html             RDB 자산·pgvector 청크·처리 작업 관리

/api/v1/assets          Asset CRUD·통계·버전·원문·관계
/api/v1/ingestion       업로드 접수·작업 상태·재시도
/api/v1/search          조건부 인증 검색 결과 API
/api/v1/chat            조건부 인증 검색+LLM 답변 API
/api/v1/admin/*         관리자 역할 기반 자산·청크 CRUD·작업 재시도·사용자 권한 관리
/api/v1/external/answer 무인증 내부망 검색+LLM/스택트레이스 분석 API
```

업로드·검색 화면에서 관리자 화면으로 이동할 수 있습니다. 관리자 API는 원시 vector를
클라이언트에 노출하지 않으며 본문 변경 시 임베딩 adapter를 통해 vector를 다시 생성합니다.
자산 삭제는 soft delete, 청크 삭제는 관련 오류 이벤트·코드 심볼까지 cascade되는 물리
삭제입니다. `user_access_roles`의 뷰어·업로더·관리자 역할을 화면과 API 양쪽에서 강제하며
임시 인증 모드는 업로더로 동작해 관리자 기능을 사용할 수 없습니다.

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

원본 파일은 DB가 아니라 profile별 `paths.upload_root`에 저장하며 PostgreSQL에는 경로와
checksum을 기록합니다. 파서 산출물과 JAR 디컴파일 결과는 `paths.artifact_root/{version_id}`
아래에 저장합니다. 기본/local, test, Rocky Linux 9의 경로는 각각 `artifacts/*`,
`artifacts/test/*`, `/srv/ecm-rag/{uploads,processed}`입니다.

## Worker 복구

작업 확보는 `FOR UPDATE SKIP LOCKED`로 이루어집니다. 처리 중 worker는 heartbeat를
갱신하며, lease가 만료된 작업은 다른 worker가 재획득합니다. YAML의 `max_attempts`를
소진하면 작업은 `failed`로 종료되고 API를 통해 명시적으로 재시도할 수 있습니다.

## 검색

1. 질문 또는 스택트레이스에서 에러코드·예외·package·class·method를 추출
2. 오류 frame → `code_symbols`와 식별자 exact/substring 후보 검색
3. PostgreSQL `simple` FTS로 lexical 후보 검색
4. 같은 embedding 모델로 질문을 벡터화해 cosine 후보 검색
5. 후보를 RRF로 결합하고 `max_vector_distance`로 무관한 일반대화 차단
6. 선택적으로 BGE reranker 적용
7. 결과 청크의 앞뒤 문맥을 함께 반환하고 LLM에는 설정된 문맥 예산만 전달

기본 검색은 `is_current=true`, `lifecycle_status=approved`를 적용하며 과거 분석 요청에서만
`include_obsolete=true`로 완화합니다.

JAR는 CFR subprocess로 소스 형태를 복원하지만 대상 바이트코드를 실행하지 않습니다.
복원된 `.java`와 직접 업로드한 `.java`는 같은 순수 Python 분석기를 사용해 심볼,
`throws`/`throw new`/`catch`, Exception/Error/Throwable 유형과 업무 오류코드를 metadata와
embedding 문맥에 저장합니다. 답변 근거가 디컴파일 코드이면 원본 스택트레이스 line과
복원 소스 line 범위가 다를 수 있다는 안내를 `notices`와 답변 본문에 추가합니다.

## 중복과 작업 복구

- 후보 조건: 동일 SHA-256, 동일 repository+logical_path, 동일 파일명, 또는 대소문자·공백을
  정규화한 정확한 제목 일치
- 제목 유사도만으로는 후보를 만들지 않으며 후보가 있어도 자동 병합하지 않음
- 사용자가 기존 Asset의 새 Version 또는 별도 신규 Asset을 선택
- 성공한 동일 SHA-256 Version은 거부하고, 이전 처리 실패 건은 기존 작업을 재접수
- JAR 장기 작업은 검사·디컴파일·Java 분석·임베딩·저장 단계와 처리 건수를 heartbeat에 기록

## 설정

코드는 환경별 경로·포트·모델명을 두지 않습니다. `.env`의 `RAG_PROFILE`이
`config/profiles/{RAG_PROFILE}.yaml` 하나를 직접 선택하고 환경 변수 표현식을 치환합니다.
DSN, 외부 모델 API 키, 선택적인 Java 인증 wrapper 경로처럼 민감하거나 서버별인 값은
`.env`에서만 주입합니다.

대표 환경 변수는 `RAG_DB_DSN`, `RAG_LLM_*`, `RAG_EMBED_*`, `RAG_AUTH_JVM_PATH`,
`RAG_AUTH_JAVA_DIR`, 선택 wrapper용 `RAG_AUTH_COMMAND`,
`RAG_DRM_COMMAND`, `RAG_JAVA_COMMAND`, `RAG_CFR_JAR_PATH`입니다. 설정 적용 순서는
`.env` 로드 → 단일 profile YAML 선택 → `${ENV:-default}` 치환입니다.
