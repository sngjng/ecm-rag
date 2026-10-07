# Changelog

## 0.1.1 - 2026-09-11

- 코드 분석 편의를 위해 주요 Python 모듈 전체에 한국어 상세 주석과 docstring을 추가했습니다.
- Docling 파싱, Canonical schema, 보험약관 계층 정규화, 병합셀 복원, 표 청킹, BGE-M3 임베딩, Chroma 저장, reranker 흐름의 설계 의도를 코드 내부에 설명했습니다.
- `table_chunker.py`에 parent/previous/next 연결, overlap row, deterministic fact 생성 이유를 상세히 기록했습니다.
- `ingest_pdf.py`에 전체 ingestion 단계별 설명을 추가했습니다.
- `ChromaStore.upsert()`에 chunk/embedding 길이 검증을 추가했습니다.
- 테스트 코드에도 각 회귀 테스트가 보장하는 의미를 주석으로 명시했습니다.


## 2026-09-11 - v0.1.0

### Added

- Python 3.11 프로젝트 골격 생성
- `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`
- Docling 2.126.0 기반 PDF parser
- TableFormer accurate mode 적용
- `do_cell_matching=true/false` 비교 실행 옵션
- Canonical Pydantic schema
- 보험약관 조/특약/별표 heading parser 초안
- table cell/span extractor
- rowspan/colspan dense expansion
- hierarchical header builder
- row record/fact generator
- table validation score
- table continuation detector 초안
- text semantic chunker
- table-aware parent/child chunker
- table chunk `previous_chunk_id` / `next_chunk_id` 연결
- BGE-M3 embedding wrapper
- Chroma persistent store
- BM25, RRF, BGE reranker 모듈
- parsing/retrieval/RAG 평가 모듈 골격
- ingest/build-index/query CLI
- span/table chunk unit tests

### Design decisions

- Markdown을 canonical source에서 제외한다.
- 복잡한 병합표의 의미 연결을 위해 표 청크에 상위 헤더와 행 문맥을 반복 주입한다.
- 큰 표가 여러 청크로 나뉘어도 동일 `table_id`와 이전/다음 청크 링크로 재조립 가능하게 한다.
- 현재 업로드된 1,363페이지 보험약관을 validation 대상 문서로 사용한다.

## 2026-09-23: PostgreSQL 병행 구축 시범 코드
- HTML 업로드 화면, FastAPI 업로드/상태/검색, 별도 처리 워커 추가.
- PostgreSQL/pgvector DDL, BGE-M3 API 호출, hybrid RRF 검색 및 선택적 재정렬 추가.
- PDF 표 처리 경로 재사용, 운영 문서·로그·코드 유형별 파싱과 Java DRM 연동 계약 추가.
- 기존 Chroma 저장소는 유지. 자세한 범위·운영 절차는 WORKLOG_PGVECTOR.md 참고.
