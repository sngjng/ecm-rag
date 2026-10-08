# 운영 지식자산 PostgreSQL 통합 구축 보고안

작성일: 2026-10-07

## 목적

폐쇄망 LLM 서비스에 운영문서, 제조사 가이드, 장애이력, 에러 로그, 소스코드를 통합하고
문서 버전·승인상태·보안등급과 벡터 검색을 PostgreSQL 한 시스템에서 관리합니다.

## 구현 범위

| 구분 | 구현 내용 | 효과 |
| --- | --- | --- |
| 자산관리 | Asset·Version·Relation PostgreSQL CRUD | 최신/승인 버전과 문서 관계 추적 |
| 업로드 | HTML 및 독립 ingestion endpoint | 수집 경로와 처리 상태 표준화 |
| 처리 | 별도 Worker, lease, heartbeat, 자동 재시도 | API 응답과 대용량 파싱 격리 |
| 검색 | exact·FTS·pgvector·RRF·선택적 reranker | 자연어와 에러코드/심볼 동시 검색 |
| 코드/로그 | AST 심볼, 에러 이벤트, 스택 프레임 | 장애 위치와 실제 코드 연결 기반 |
| 설정 | 공통 YAML + 환경별 profile | OS·모델·솔루션 교체 비용 최소화 |
| DRM | Java 실행 wrapper 계약 | 사내 라이브러리 독립 구현 가능 |

## 구성

편집 가능한 원본은 `ARCHITECTURE_EDITABLE.mmd`로 제공합니다. API 프로세스는 CRUD와
작업 접수·검색만 담당하고, 별도 Worker가 DRM·파싱·임베딩·적재를 수행합니다.

## 단계별 적용

1. PostgreSQL 17과 `vector`, `pg_trgm` 설치 및 최소권한 계정 구성
2. 폐쇄망 wheel·Docling artifact·모델 반입
3. 대표 문서/장애/로그/코드로 기능·성능·복구 시험
4. OpenWebUI tool에서 `/api/v1/search` 연계 및 인용 검증
5. 실제 질문 100~300개로 Recall@K, MRR, 인용 정확도, 응답시간 평가

## 운영 전 확인사항

- DB 백업·복구, TLS, API key 교체, 사용자별 권한과 감사로그
- DRM 평문 artifact·DB 청크의 접근권한과 보존/폐기 정책
- 대형 PDF 처리시간, Worker 수, GPU/CPU 자원 한도
- embedding 차원과 SQL `vector(N)`의 일치 여부
- OpenWebUI에서 Asset/Version/Page/Line 근거 표시

## 현재 경계

기능 코드는 PostgreSQL CRUD·ingestion·검색 경로까지 포함합니다. 실제 사내 DRM,
PostgreSQL, BGE-M3, reranker, OpenWebUI 환경의 통합·부하·보안 시험은 대상 망에서 별도로
수행해야 합니다.
