# 운영 지식자산 PostgreSQL 통합 구축 보고안

최초 작성일: 2026-10-07 / 최종 현행화: 2026-10-22

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
| 사용자 화면 | 로그인·간편/상세 업로드·대화형 검색·원문 다운로드 | 비개발자 단건 등록과 조회 지원 |
| Java 분석 | JAR CFR 디컴파일 및 Java 공통 심볼·예외·오류코드 분석 | 스택트레이스에서 관련 소스 근거 탐색 |
| 외부 연동 | 내부망 무인증 질의응답 endpoint | 기존 시스템에서 JSON/문자열로 호출 가능 |

## 구성

편집 가능한 원본은 `ARCHITECTURE_EDITABLE.mmd`로 제공합니다. API 프로세스는 CRUD와
작업 접수·검색만 담당하고, 별도 Worker가 DRM·파싱·임베딩·적재를 수행합니다.

현재 임시 모드의 사용자 흐름은 첫 화면 `/`(간편 업로드)이며 필요 시 상세 업로드
(`/upload.html`)와 문서 조회(`/search.html`)로 이동합니다. 인증 활성화 후에는
`/login` → `/spl_upload.html` 흐름을 사용합니다. 검색 화면은 근거 문서와 답변을 함께
표시하고 근거 카드에서 원본을 내려받습니다.

외부 시스템은 `POST /api/v1/external/answer`로 일반 질문 또는 스택트레이스를 전달할 수
있습니다. 이 endpoint는 내부망 전용으로 세션/API 키가 없으므로 방화벽 또는 reverse
proxy에서 허용 대역을 제한해야 합니다.

## 단계별 적용

1. PostgreSQL 17과 `vector`, `pg_trgm` 설치 및 최소권한 계정 구성
2. 폐쇄망 wheel·Docling artifact·모델 반입
3. 대표 문서/장애/로그/코드로 기능·성능·복구 시험
4. JDK·CFR를 반입하고 JAR/Java 심볼·예외·오류코드와 스택트레이스 연결 시험
5. OpenWebUI tool 또는 외부 시스템에서 검색·답변 endpoint 연계 및 인용 검증
6. 실제 질문 100~300개로 Recall@K, MRR, 인용 정확도, 응답시간 평가

## 운영 전 확인사항

- DB 백업·복구, TLS, 사내 로그인 세션, 사용자별 권한과 업로드 감사로그
- DRM 평문 artifact·DB 청크의 접근권한과 보존/폐기 정책
- 대형 PDF 처리시간, Worker 수, GPU/CPU 자원 한도
- embedding 차원과 SQL `vector(N)`의 일치 여부
- OpenWebUI에서 Asset/Version/Page/Line 근거 표시
- 무인증 외부 endpoint의 사내 CIDR ACL과 8300 포트 비인가 대역 차단
- JAR 클래스 수·압축 해제 크기·처리시간 상한 및 worker heartbeat 확인
- 디컴파일 소스 근거에는 원본 스택트레이스 line과 차이가 날 수 있음을 사용자에게 표시

## 현행 운영 정책

- ChromaDB는 사용하지 않으며 자산 원장·검색 인덱스·벡터를 PostgreSQL에 통합합니다.
- 사내 로그인 구현 전에는 첫 화면을 간편 업로드로 열고 임시 사용자로 감사 이력을
  기록합니다. 이 기간에는 서비스 전체를 사내 허용 대역으로 제한하며, 구현 완료 후
  `RAG_AUTH_ENABLED=true`로 기존 Java 로그인·세션 인증을 활성화합니다.
- 파일은 1회 1건, 기본 10MiB 이하로 접수하고 queue 적체 시 신규 요청을 제한합니다.
- 기존 문서는 동일 SHA-256, 저장소 논리 경로, 파일명 또는 정확한 제목 일치로만 후보를
  제시합니다. 제목이 비슷하다는 이유로 자동 연결하지 않으며 사용자가 새 버전/신규 자산을
  최종 선택합니다.
- 일반대화나 등록 문서와 무관한 질문은 LLM 오류처럼 보이지 않도록 지원 범위 안내로
  응답합니다.

## 현재 경계

기능 코드는 PostgreSQL CRUD·ingestion·검색·검색 근거 기반 LLM 답변·외부 스택트레이스
분석·JAR/Java 소스 연결까지 포함합니다. 실제 사내 DRM/인증, PostgreSQL, 임베딩/LLM,
reranker, OpenWebUI 환경의 통합·부하·보안 시험은 대상 망에서 별도로 수행해야 합니다.
