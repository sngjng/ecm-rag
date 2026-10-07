# Architecture

## 1. 목표

1,000페이지 이상 보험약관에서 본문·특별약관·별표·표·이미지를 구조적으로 보존한 뒤 검색 가능한 canonical representation을 만든다.

## 2. 전체 흐름

```text
PDF
 ├─ Page profiling
 ├─ Docling accurate table parsing
 ├─ Canonical JSON
 │   ├─ text/article hierarchy
 │   └─ tables/cells/span/page provenance
 ├─ Table validation
 │   └─ low score → OCR / alternative parser 후보
 ├─ Normalization
 │   ├─ span expansion
 │   ├─ header paths
 │   ├─ inherited row context
 │   └─ deterministic row facts
 ├─ Chunking
 │   ├─ text: article-aware
 │   └─ table: Parent → child chunks
 ├─ BGE-M3
 ├─ Chroma
 ├─ lexical/RRF
 ├─ reranker
 └─ context expansion → LLM
```

## 3. 보험약관 특화 계층

가능한 경우 다음 구조를 보존한다.

```text
상품
 → 보통약관 / 특별약관 / 별표
 → 특약
 → 관 / 절
 → 조
 → 항 / 호
 → 표 / 각주
```

페이지 경계는 의미 경계로 사용하지 않는다.

## 4. 복잡표 처리 원칙

### 4.1 Markdown 금지

병합 구조를 잃기 때문에 Markdown은 canonical source가 아니다.

### 4.2 span expansion

`rowspan`, `colspan`에 의해 한 번만 기록된 상위 셀을 dense grid로 확장한다. 청크가 표 중간에서 분리되어도 상위 조건이 남는다.

### 4.3 table child chunk

각 child는 반드시 다음을 가진다.

- `parent_id/table_id`
- 원래 표의 source page
- hierarchical header
- 상속된 row header
- 관련 footnote
- previous/next chunk id

### 4.4 retrieval representation

canonical JSON과 embedding text를 분리한다.

예:

```text
[표] 암진단비 지급기준
[문맥] 2-4 암진단Ⅱ 특별약관 > 제3조 보험금의 지급사유
구분 = 일반암; 지급사유 = 최초 진단; 지급금액 = 가입금액 100%
```

## 5. 페이지를 넘어가는 표

`table_linker.py`는 현재 header similarity + column count + page continuity를 제공한다. 실제 대상 PDF 평가 후 caption/layout/bbox 기반 점수를 추가한다.

## 6. 실패 처리

완전 자동화보다 **검증 가능성**을 우선한다.

- score >= 0.90: 일반 처리
- 0.80~0.90: 경고 기록
- < 0.80: OCR/alternative parser 재처리 후보

임계값은 실제 gold set 평가 후 조정한다.

## 7. 폐쇄망

BGE-M3/reranker/Docling 모델은 인터넷이 가능한 환경에서 사전 다운로드하고 내부 모델 경로를 넘길 수 있도록 설계한다.
