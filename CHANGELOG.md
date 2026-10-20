# Changelog

## Unreleased

- 8K LLM context window에 맞춰 system prompt·질문·최근 이력·검색 근거의 문자 예산을
  계산하고, context 초과 응답 시 이력 제거와 근거 축소 후 한 번 자동 재시도
- 로그인 성공 및 기존 인증 세션 확인 후 기본 이동 화면을 `spl_upload.html`로 변경
- 벡터 cosine distance 관련성 기준을 추가해 날씨·인사 등 등록 문서와 무관한
  일반대화는 LLM을 호출하지 않고 문서 전용 안내로 응답
- JAR 디컴파일 Java와 단일 Java 업로드에서 Exception/Error/Throwable 선언·발생·처리,
  업무 오류 코드를 동일한 청크 metadata와 embedding 문맥으로 분류

- 소스코드 자산의 JAR 업로드와 CFR 기반 Java 디컴파일 추가
- 디컴파일 결과를 package/class/method 심볼 및 pgvector 청크로 저장
- Java stack trace 직접 입력 또는 저장된 오류 코드·예외명을 code symbol과 연결하는 검색 lane 추가
- JAR 경로 이탈·암호화·압축 해제 크기·클래스 수 제한과 운영 설정/문서 추가
- JAR 검사·디컴파일·심볼 분석·임베딩 건수를 작업 API, 업로드 모달과 worker 로그에 표시
- 동일 원본의 이전 처리 작업이 실패하면 중복 Asset을 만들지 않고 기존 job을 자동 재접수
- API 접근 로그와 Worker 애플리케이션 로그에 밀리초 단위 타임스탬프를 공통 적용
- JAR 디컴파일 소스 분석을 순수 Python 안전 분석기로 분리해 tree-sitter ABI 충돌에
  의한 Worker segmentation fault 방지
- 오류 이벤트에서 Java 내부 클래스 심볼을 연결하는 SQL의 psycopg 리터럴 `%`
  이스케이프 오류 수정
- psycopg가 SQL 내부 설명 주석의 `%`까지 placeholder로 해석해 발생하던 UTF-8
  디코딩 오류 수정
- Ollama/vLLM의 HTTP 4xx 응답 상태·URL·제한된 오류 본문을 기록해 모델명, context
  한도, 요청 옵션 오류를 운영 로그에서 구분할 수 있도록 개선

## 0.3.0 - 2026-10-08

- API 키 입력을 제거하고 사내 Java 로그인 모듈 연동형 서버 세션 인증 추가
- 로그인 사번·성명·부서와 신규/버전등록 구분을 PostgreSQL 업로드 감사 이력에 저장
- 업로드 시 파일 해시·파일명·제목·시스템·서비스·소스 경로 기반 기존 문서 후보 판별
- Asset ID 직접 입력을 제거하고 기존 문서 후보 확인 대화상자 추가
- 사용자 확인 후 기존 Asset의 새 버전 또는 별도 신규 Asset으로 등록하는 2단계 흐름 추가
- 중복 판별 제목 유사도와 최대 후보 수를 YAML 설정으로 분리

## 0.2.0 - 2026-10-07

- PostgreSQL RDB·FTS·pgvector 단일 저장소 구조로 통합
- Asset/Version/Relation CRUD endpoint 분리
- ingestion API와 독립 Worker 처리 라인 분리
- lease·heartbeat·재시도 기반 영속 작업 queue 추가
- YAML base/profile/환경변수 설정 계층 추가
- 장애·에러·stack frame·code symbol 구조화 강화
- 업로드 UI, 아키텍처, 보고서, 운영 절차 갱신

## 0.1.1

- Docling canonical JSON, 구조 인식형 텍스트/표 청킹 및 상세 한국어 주석 추가
