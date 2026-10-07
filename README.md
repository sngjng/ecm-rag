# 최신 확장: PostgreSQL 병행 구축

업로드 페이지, FastAPI, 워커, PostgreSQL/pgvector, DRM 연동, 보고 자료의 실행 순서는 [WORKLOG_PGVECTOR.md](WORKLOG_PGVECTOR.md)를 보세요. 편집 가능한 전체 구성도는 [ARCHITECTURE_EDITABLE.mmd](ARCHITECTURE_EDITABLE.mmd), 부서장 보고안은 [DEPARTMENT_BRIEF.md](DEPARTMENT_BRIEF.md)입니다. 기존 Chroma 경로는 아래 원래 안내와 같이 유지됩니다.

---

# insurance-rag

> **v0.1.1 commented edition**: 코드 분석을 쉽게 하기 위해 주요 모듈에 한국어 상세 docstring과 인라인 주석을 추가했습니다. 기능 흐름은 v0.1.0과 동일하게 유지하며, 일부 방어 검증만 보강했습니다.


Python 3.11 기반 보험약관 정형화/RAG 파이프라인 초안입니다.

이 프로젝트의 핵심 원칙은 **Markdown을 원본 정형 데이터로 사용하지 않는 것**입니다. Docling의 구조화 JSON을 canonical source로 보존하고, 검색을 위해 별도의 retrieval text를 생성합니다.

## 현재 대상 문서

- 현대해상 내삶엔(3N)맞춤간편건강보험 Hi2607 약관
- 업로드 문서 기준 1,363페이지
- 본문, 특별약관, 별표, 복잡 병합표, 이미지/시각화 페이지가 혼재

## 핵심 설계

1. Docling `TableFormerMode.ACCURATE`
2. Canonical JSON 보존
3. rowspan/colspan 확장
4. 복잡 표는 Table Parent → Row Group → Child Chunk 구조
5. 청크 분리 시 header/context/footnote 재주입
6. `previous_chunk_id`, `next_chunk_id`, `parent_id` 유지
7. BGE-M3 dense embedding
8. ChromaDB 저장
9. BM25/RRF + BGE reranker 확장 가능 구조
10. 저품질 표는 validation report를 통해 OCR/2차 parser 대상으로 분리

## 설치

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

폐쇄망에서는 PyTorch/CUDA wheel을 대상 서버 환경에 맞게 먼저 준비하는 것을 권장합니다.

## 1차 파싱

```bash
python scripts/ingest_pdf.py "/path/to/policy.pdf" --out ./artifacts
```

Docling cell matching이 복잡한 병합표를 잘못 합치는 경우 비교 테스트:

```bash
python scripts/ingest_pdf.py "/path/to/policy.pdf" --out ./artifacts_no_match --no-cell-matching
```

생성물:

```text
artifacts/canonical/*.docling.json
artifacts/canonical/*.canonical.json
artifacts/chunks/*.chunks.jsonl
artifacts/reports/*.report.json
```

## 벡터 인덱스

```bash
python scripts/build_index.py artifacts/chunks/<file>.chunks.jsonl \
  --model /offline/models/bge-m3
```

## 검색 테스트

```bash
python scripts/query.py "암진단 보험금 지급 조건은?" \
  --model /offline/models/bge-m3 \
  --reranker /offline/models/bge-reranker-v2-m3
```

## 중요

현재 v0.1.0은 실행 가능한 기반 골격입니다. 보험약관의 모든 표를 자동으로 100% 완벽하게 복원한다고 가정하지 않습니다. `table_validator.py`가 실패 후보를 분리하고, 실제 약관의 어려운 표 페이지를 기준으로 규칙/2차 parser를 추가하는 방식으로 개선합니다.
