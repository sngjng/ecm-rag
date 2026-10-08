# PostgreSQL 단일 저장소 개편 작업 기록

작성일: 2026-10-07

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
| metadata context와 표시 본문 분리 | `display_content`, `embedding_content` 컬럼 |
| Parent/Neighbor 문맥 확장 | parent/previous/next ID와 검색 결과 `neighbor_context` |
| 문서 간 관계 | `asset_relations` CRUD와 relation type 제약 |

## 실행 순서

1. `sql/001_pgvector.sql` 적용
2. `RAG_PROFILE`과 비밀 환경 변수 설정
3. `python -m uvicorn service.api:app --host 0.0.0.0 --port 8300`
4. 별도 서비스로 `python -m service.worker`
5. `/health/ready` 확인 후 HTML 또는 API로 업로드
6. `/api/v1/ingestion/jobs/{job_id}`에서 완료 상태 확인
7. `/api/v1/search`를 OpenWebUI tool에서 호출

## API 요약

- `POST/GET /api/v1/assets`
- `GET/PATCH/DELETE /api/v1/assets/{asset_id}`
- `GET /api/v1/assets/{asset_id}/versions`
- `POST/GET/DELETE /api/v1/assets/{asset_id}/relations...`
- `POST /api/v1/ingestion/uploads`
- `GET /api/v1/ingestion/jobs/{job_id}`
- `POST /api/v1/ingestion/jobs/{job_id}/retry`
- `POST /api/v1/search`
- `GET /health/live`, `GET /health/ready`

## 검증 제한

현재 작업 환경에는 프로젝트 Python 의존성과 대상 PostgreSQL/모델/DRM 서비스가 없어
구문 검사와 의존성 없는 단위 검증까지만 수행할 수 있습니다. 실제 통합 시험은 폐쇄망
Rocky Linux 9 환경에서 `tests/integration` 시나리오를 기준으로 수행해야 합니다.
