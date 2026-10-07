# PostgreSQL 병행 구축 작업 기록

작성일: 2026-09-23  
기준 코드: 기존 `insurance-rag-v0.1.1-commented.zip`에 PostgreSQL 경로를 별도로 추가함.

## 변경 파일

| 위치 | 기능 |
| --- | --- |
| `web/upload.html` | HTML 업로드·진행 상태 화면 |
| `service/api.py` | 업로드, 상태 확인, 검색 REST API |
| `service/worker.py` | 대기 파일의 복호화 연동, 파싱, 임베딩, 적재 |
| `service/parsers.py` | 기존 Docling/표 파서 재사용 + 텍스트·로그·소스 코드 처리 |
| `service/store.py`, `sql/001_pgvector.sql` | PostgreSQL 스키마와 트랜잭션 저장 |
| `service/embedding.py`, `service/search.py` | 외부 BGE-M3 호출, 정확/FTS/벡터 검색, RRF 및 선택적 rerank |
| `service/drm.py` | Java DRM 실행 파일 연계 계약 |
| `ARCHITECTURE_EDITABLE.mmd` | 텍스트로 편집 가능한 구성도 |
| `DEPARTMENT_BRIEF.md` | 부서장 보고용 문서 |

## 실행 준비와 순서

1. PostgreSQL 17과 pgvector, pg_trgm을 설치한 뒤 DB `ragdb`에 `sql/001_pgvector.sql`을 실행. `raguser`에게 `rag` 스키마의 테이블/시퀀스 읽기·쓰기 권한 부여. `CREATE EXTENSION`은 DBA가 실행. 운영 계정 권한은 보안 정책에 따라 최소화.
2. Rocky Linux 9, Python 3.11 가상환경에서 `requirements.txt`를 수작업으로 설치. 폐쇄망이라면 동일 플랫폼/파이썬 버전의 wheel과 모델 아티팩트를 반입. 기존 v0.1.1 의존성이 많으므로 설치 시 기존 환경의 torch/Docling 충돌을 먼저 확인.
3. `.env.pgvector.example`를 참고해 **셸/서비스 매니저 환경 변수**를 주입. `.env` 파일은 자동으로 읽지 않는다. BGE-M3 endpoint는 OpenAI 호환 `/v1/embeddings`여야 하며 1024차원으로 응답해야 한다.
4. 프로젝트 최상위에서 API 시작: `python -m uvicorn service.api:app --host 127.0.0.1 --port 8300`. 내부망 접근이 필요하면 승인된 인터페이스/방화벽·TLS 프록시를 구성.
5. 별도 터미널/서비스에서 워커 시작: `python -m service.worker`. 업로드 페이지는 `http://서버:8300/`. 워커가 가동되어야 대기 작업이 처리된다.
6. 검색 호출 예: `curl -H 'X-API-Key: 실제키' -H 'Content-Type: application/json' -d '{"query":"ORA-01555 발생 이력","limit":5}' http://127.0.0.1:8300/api/search`.

## 입력 매핑

- `document`: PDF, DOCX, UTF-8 TXT/MD. PDF는 기존 Docling JSON·canonical JSON·표 병합 정보를 남기고 본문/표 청크를 저장한다. DOCX는 문단과 표 행을 순서대로 추출한다.
- `incident`: UTF-8 TXT/MD. 장애 원인/조치는 현재 문단 단위로 인덱싱하며 별도 발생 일시·조치 필드 자동 구조화는 후속.
- `error_trace`: UTF-8 LOG/TXT. Java `at ...` 스택 프레임, 에러코드, 예외명, 원문 및 fingerprint를 저장한다.
- `source_code`: UTF-8 Python/Java는 AST 경계로, SQL/JS/shell/config는 120라인 파일 단위로 저장한다. Java는 tree-sitter grammar가 오프라인에서 사용 가능해야 한다. 업로드 폼의 `저장소 내 상대 경로`를 함께 입력해야 스택의 파일 위치와 연결하기 쉽다.

## DRM Java 연동 계약

`RAG_DRM_COMMAND=/srv/drm/bin/decrypt-wrapper` 처럼 실행 파일 경로를 지정하면 워커가 `<원본경로> <복호화출력경로>` 인자 두 개를 전달한다. Java wrapper가 회사 라이브러리로 복호화 후 출력 파일을 기록하고 exit 0을 반환하도록 구현하면 된다. 오류는 0 이외 상태 코드로 반환한다. `subprocess`는 shell을 사용하지 않는다. 복호화된 임시 파일은 처리 후 삭제한다. 단, Docling JSON과 DB 청크에도 평문이 저장되므로 저장소 접근 권한과 보존 정책이 필요하다. 암호화 형식별 실제 파일명/복호화 후 확장자가 다른 경우 wrapper와 업로드 정책을 조정해야 한다.

## 운영과 검증 포인트

- 파일은 `RAG_UPLOAD_ROOT/UUID/원래파일명`에 저장된다. DB에는 `queued → processing → completed/failed` 상태가 남는다. 실패 작업은 원인을 확인하고 `UPDATE rag.assets SET status='queued', error_detail=NULL WHERE asset_id='...' AND status='failed'`로 재시도할 수 있다. 중간에 워커가 강제 종료되어 `processing`에 남은 건은 재시작 전 관리자가 원인 확인 후 상태를 재설정한다.
- 새 PostgreSQL 서비스는 기존 `storage/chroma.py` 및 Chroma 데이터에 쓰지 않는다. Chroma는 별도 서비스로 유지한다.
- 검색 결과에는 asset ID·원본 파일·페이지 또는 줄 범위가 포함된다. HTML 화면은 등록 전용이며 OpenWebUI 답변 연계는 `/api/search` 결과를 FastAPI Tool로 감싼 후 수행한다.
- PDF 표의 낮은 검증 점수는 assets.metadata 및 canonical 산출물에서 확인. `ocr_router.py`의 OCR fallback은 정책만 있으므로 자동 재처리는 후속 개발.
- 1차 정확 검색은 문자열과 FTS simple 사전을 사용한다. 한글 형태소 분석, 프로젝트 전체 코드 호출 그래프, 동일 장애 자동 연결, 권한 분리는 후속 작업. 개발 시범 사용 범위를 넘길 때 감사·권한·개인정보 처리를 보강한다.

## 테스트 범위

로컬 코드 컴파일 및 순수 파서 동작을 확인한다. 실제 Rocky Linux 9, Python 3.11, PostgreSQL 17, Docling 모델, BGE-M3 endpoint, DRM Java 라이브러리에 대한 통합 테스트는 사내 환경에서 수행해야 한다. 적재/검색/대용량 PDF 성능 수치는 아직 측정하지 않았다.
