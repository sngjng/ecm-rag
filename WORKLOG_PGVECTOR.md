# PostgreSQL 단일 저장소 개편 작업 기록

최초 작성일: 2026-10-07 / 최종 현행화: 2026-10-22

## 반영 사항

- FastAPI endpoint를 Asset CRUD, ingestion, search, health router로 분리
- PostgreSQL을 Asset→Version→Job→Chunk/Embedding 원장으로 재설계
- API 처리와 DRM/파싱/임베딩 Worker 프로세스 분리
- Worker lease, heartbeat, 자동 재시도와 수동 retry endpoint 추가
- 기본 YAML, local/Rocky Linux 9/test profile 및 환경변수 치환 추가
- embedding 모델 차원에 맞춘 PostgreSQL SQL 렌더링 도구 추가
- display text와 metadata-enriched embedding text 분리
- 장애 필드 구조화, 에러 이벤트/스택 프레임, 코드 심볼 저장 강화
- exact + FTS + pgvector + RRF + 선택적 reranker 검색 구성
- 업로드 HTML을 새 API와 Asset/Version metadata에 맞게 갱신
- 편집 가능한 Mermaid 구성도와 운영 보고 문서 갱신
- 사내 Java 로그인 후 기본 화면을 `spl_upload.html`로 연결하고 업로드·검색 화면 간 이동 메뉴 추가
- 기존 문서 후보를 SHA-256·논리 경로·파일명·정확한 제목 일치로만 판정하도록 변경
- Java JAR를 CFR로 디컴파일하고 `.java`와 같은 안전 분석기로 package/class/method,
  예외 유형, 오류코드를 수집하는 소스 검색 경로 추가
- Java 스택트레이스에서 예외·원인·오류코드·호출 프레임·fingerprint를 추출해
  `error_events`, `stack_frames`, `code_symbols`와 연결하는 검색 경로 추가
- 로그인 없는 내부망 연동용 `POST /api/v1/external/answer`와 JSON/text 입력 계약 추가
- `search.html`의 검색 근거 기반 LLM 답변, 원문 다운로드, 자산유형별 등록 건수 표시 추가
- 무관한 일반대화 차단, 8K LLM 문맥 예산과 context 초과 1회 축소 재시도 추가
- JAR 디컴파일 근거 사용 시 원본 라인 번호와 복원 소스 라인 범위가 다를 수 있다는
  `notices` 및 답변 안내 추가
- 사내 로그인 구현 전 임시 인증 비활성 모드와 공통 임시 감사 사용자 추가
- 첫 화면을 간편 업로드로 변경하고 `RAG_AUTH_ENABLED=true` 전환 시 기존 세션 인증 복구

## 공유 설계 대화 반영표

| 설계 원칙 | 프로젝트 반영 |
| --- | --- |
| MD=사람/LLM 본문, JSON=구조, JSONL=검색 단위 | PDF 처리 시 `document.md`, `docling.json`, `canonical.json`, `chunks.jsonl`, `metadata.json` 보존 |
| Asset→Version→Element/Chunk→Embedding | `assets`, `asset_versions`, `chunks` 관계로 구현 |
| 최신 승인 문서 기본 검색 | `is_current`, `lifecycle_status` 필터와 `include_obsolete` 옵션 |
| 문서·장애·Trace·코드별 처리 | Worker parser router와 별도 구조화 함수 |
| 에러코드/심볼은 exact 우선 | identifier B-tree/trigram + exact 검색 레인 |
| 자연어·키워드 hybrid 검색 | PostgreSQL FTS + pgvector + RRF |
| Stack Trace→Code 위치 | `error_events`, `stack_frames`, `code_symbols`의 파일·라인 정보 |
| 디컴파일 코드 추적 | 원본 line은 참고값으로 두고 package/class/method를 우선 식별자로 사용 |
| metadata context와 표시 본문 분리 | `display_content`, `embedding_content` 컬럼 |
| Parent/Neighbor 문맥 확장 | parent/previous/next ID와 검색 결과 `neighbor_context` |
| 문서 간 관계 | `asset_relations` CRUD와 relation type 제약 |

## 실행 순서

1. `sql/001_pgvector.sql` 적용
2. `RAG_PROFILE`과 비밀 환경 변수 설정
3. `python -m uvicorn service.api:app --host 0.0.0.0 --port 8300`
4. 별도 서비스로 `python -m service.worker`
5. `/health/ready` 확인 후 임시 모드는 `/`, 인증 모드는 `/login`에서 시작
6. `/api/v1/ingestion/jobs/{job_id}`에서 완료 상태 확인
7. `/search.html`, `/api/v1/chat` 또는 `/api/v1/search`로 조회
8. 외부 시스템은 내부망 ACL 적용 후 `/api/v1/external/answer` 호출

## API 요약

- `POST/GET /api/v1/assets`
- `GET/PATCH/DELETE /api/v1/assets/{asset_id}`
- `GET /api/v1/assets/{asset_id}/versions`
- `POST/GET/DELETE /api/v1/assets/{asset_id}/relations...`
- `POST /api/v1/ingestion/uploads`
- `GET /api/v1/ingestion/jobs/{job_id}`
- `POST /api/v1/ingestion/jobs/{job_id}/retry`
- `POST /api/v1/search`
- `POST /api/v1/chat`
- `POST /api/v1/external/answer` (세션/API 키 없음, 내부망 ACL 필수)
- `GET /health/live`, `GET /health/ready`

## 현행 운영 기준

- ChromaDB 코드는 사용하지 않으며 관계형 원장, FTS와 벡터를 PostgreSQL에 통합합니다.
- 업로드는 1요청 1파일, 기본 5MiB 상한입니다. 다중 사용자의 작업은 PostgreSQL queue와
  복수 worker가 `FOR UPDATE SKIP LOCKED`로 나눠 처리합니다.
- 실패한 동일 원본 작업은 새 Asset을 만들지 않고 재접수할 수 있으며, 성공한 동일
  SHA-256 원본의 중복 Version 생성은 거부합니다.
- 현재 임시 모드에서는 화면/API도 세션 없이 임시 사용자로 동작합니다. 인증 활성화 후
  화면용 `/api/v1/chat`은 세션을 요구하지만 외부 연동 endpoint는 계속 인증하지 않으므로
  방화벽/reverse proxy에서 허용 대역을 반드시 제한합니다.
- 기본 저장 경로는 `artifacts/uploads`, `artifacts/processed`이고 test profile은
  `artifacts/test/*`, Rocky Linux 9 profile은 `/srv/ecm-rag/uploads`,
  `/srv/ecm-rag/processed`를 사용합니다.

## 검증 제한

로컬에서는 단위 테스트와 test profile의 deterministic embedding을 검증할 수 있습니다.
실제 PostgreSQL/pgvector, 사내 모델, `login.py` 인증·Java DRM, JDK/CFR 및 GPU를 포함하는 통합·부하·
보안 시험은 대상 폐쇄망 Rocky Linux 9 환경에서 `tests/integration` 시나리오와
`OPERATIONS_MANUAL.md`의 인수 체크리스트를 기준으로 수행해야 합니다.
