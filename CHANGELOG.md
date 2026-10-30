# Changelog

## Unreleased

- 인증 사용자용 `incident_guide.html`을 추가해 최대 1,000,000자의 장애 로그에서 오류 코드,
  예외, 최하위 원인과 애플리케이션 호출 프레임을 추출하고 PostgreSQL/pgvector 내부 검색과
  LLM을 결합한 해결 가이드를 생성; 조회 청크는 화면/API 응답에서 숨기고 관리자만 수정할
  수 있는 공용 답변 템플릿과 `007_incident_guide_template.sql` migration 추가; 자연어 질문
  단독 요청을 지원하고 템플릿 제목·순서 검증, 1회 재생성 및 서버 형식 보정 적용;
  화면명을 로그분석 AI로 변경하고 오류별 타임스탬프와 내부 RAG 교차 검증 적용;
  stack frame을 호출자→오류 메서드→예외 전파 순서로 재구성하고 RAG 소스 심볼 일치 여부를
  표시하는 메서드 흐름 도식, 출력 길이 도달 시 이어쓰기 및
  `008_incident_timestamp_template.sql` migration 제공
- 일반 문서에 PPTX 업로드를 추가하고 슬라이드별 제목·본문·표·발표자 노트 및 포함
  이미지의 EasyOCR 결과를 검색 청크와 Markdown으로 변환; EasyOCR 장치를
  `cpu/gpu/auto`로 선택해 원문 다운로드·MD 보기·pgvector 검색에 연결
- PostgreSQL 요청별 신규 연결을 프로세스별 동기 connection pool로 전환하고, pool 반환 전
  업로드 advisory lock을 명시적으로 해제하도록 보강
- hybrid 검색을 stack/identifier/FTS/vector lane, 후보 조회, rerank, 인접 문맥 단계로
  분리하고 embedding·reranker 실행 중에는 DB 연결을 점유하지 않도록 변경
- LLM·embedding API에 프로세스별 HTTP keep-alive client 재사용과 timeout·연결 오류,
  HTTP 408/429/5xx 대상 제한된 지수 backoff 재시도 적용
- 설정을 `.env + 단일 완결형 Profile YAML` 2단계로 단순화하고 공통
  `config/settings.yaml`과 deep-merge를 제거; `test`는 실제 폐쇄망 모델 연동용으로
  전환하고 외부 서비스 없는 자동화 설정은 `unit-test` profile로 분리; 기존 테스트
  서버의 원문 다운로드가 유지되도록 test 저장 경로는 Profile YAML의
  `artifacts/uploads`, `artifacts/processed`로 고정 관리
- 문서 검색 결과 작업을 `원문 다운로드`와 `MD 뷰어` 두 개로 정리하고, 별도
  `md_view.html`에서 H1~H3 기반 좌측 목차, 현재 항목 강조, 원문·MD 다운로드를 제공;
  운영 목록의 `Markdown==3.10.2`로 서버에서
  렌더링하고 HTML·위험 URL을 허용 목록으로 정제해 저장형 XSS 차단
- 단건 업로드 제한을 서버·테스트 profile·업로드 화면에서 5MiB에서 10MiB로 상향
- 문서 자산 전용 MD 다운로드 endpoint를 추가해 뷰어 내부에서 제공하고, PDF의 보존
  Markdown 또는 기존 문서의 저장 청크를 이용해 재업로드 없이 내려받도록 구성
- Rocky Linux 9 폐쇄망 테스트 환경에서 애플리케이션 수정과 기존 데이터 이전 없이
  PostgreSQL 17·pgvector를 rootless Podman으로 신규 구축할 수 있도록 RPM·이미지 반입,
  데이터 영속화, SELinux, 빈 DB 초기화, 재부팅 자동기동 및 장애 점검 가이드 추가
- 접근 권한을 뷰어(조회 전용)·업로더(관리자 제외)·관리자(전체)로 표준화하고 HTML 직접
  접근과 FastAPI endpoint 모두에 동일한 서버 권한 검사를 적용
- `user_access_roles` migration과 관리자 화면의 사용자 권한 등록·수정·삭제 탭 추가;
  권한 변경 시 기존 세션 폐기 및 미등록 사원의 업로더 권한 기본 적용
- 뷰어·업로더·관리자 사용자 표시를 각각 팝콘·개미·건틀릿 아이콘으로 구분하고 역할별
  로그인 후 이동 화면과 메뉴 노출을 조정
- 제공된 JPype/Java VO 로그인 규격을 `service/login.py`에 구현하고 JVM·classpath·EAI
  식별자를 환경변수로 분리; 동시 로그인 JVM 초기화 보호와 비밀번호 비로그 처리 적용
- 실제 EAI 정상 응답의 `emp_no`, `user_id`, `user_name`, `dept_name`, `up_dept_name`을
  세션에 보존하는 `004_eai_login_identity.sql` migration과 고정 fixture 회귀 테스트 추가
- 실제 인증 사용자를 업로드·검색 화면에
  `성명 (사번) / 부서 / 상위부서 / 권한 아이콘` 형식으로 표시
- 무인증 `/api/v1/external/answer`의 정상 질문·스택트레이스·입력 오류·용량 제한을
  실제 HTTP로 검사하는 표준 라이브러리 기반 12개 케이스 테스트 스크립트 추가
- JSON `include_chunks` 파라미터로 LLM 답변만 반환하거나 검색 근거 청크까지 포함하도록
  외부 API 응답 크기를 선택하는 기능 추가
- JSON 객체·JSON 문자열·일반 문자열·스택트레이스와 대표 4xx 오류를 테스트 PC에서
  바로 실행할 수 있는 curl 명령 및 기대 결과 문서 추가
- 업로드·검색 화면의 데이터 관리 링크를 기본 숨김 처리하고 인증 사용자 역할에
  `admin`이 포함된 경우에만 표시하도록 변경
- 관리자 자산 논리 삭제 시 pgvector는 보존되어 검색에서만 제외되고, 청크 물리 삭제 시
  embedding과 연결 오류·심볼 데이터가 함께 제거되는 삭제 정책을 운영 문서에 명시
- **데이터 관리** `admin.html`과 `/api/v1/admin`을 추가해 PostgreSQL 자산 metadata의 CRUD·복구,
  Version 조회, pgvector 검색 청크 CRUD, 처리 작업 조회·재시도를 한 화면에서 지원
- 관리자 청크 본문 생성·수정 시 임베딩을 자동 재생성하고 raw vector 직접 입력을 차단;
  인증 활성화 시 `admin` 역할을 강제하고 임시 모드의 네트워크 제한 주의사항 문서화
- 사내 로그인 구현 전 임시 운영을 위해 `RAG_AUTH_ENABLED=false` 모드 추가: 첫 화면을
  간편 업로드로 변경하고 세션 없는 요청을 공통 임시 사용자 감사 이력으로 처리
- 임시 인증 모드에서는 업로드·검색 화면의 사용자 정보와 로그아웃 버튼을 표시하지 않음
- `/upload.html` 상세 업로드 경로를 추가하고 임시 모드에서 `/login`을 첫 화면으로 이동
- 향후 `service/login.py` 구현만으로 기존 `POST /api/v1/auth/login`과 세션 발급을 사용할
  수 있도록 `python_module` 인증 provider와 명시적인 placeholder 계약 추가
- 구현 코드와 일치하도록 README, 아키텍처, 화면/API 흐름, 운영·로컬 설치, Java 인증,
  통합 시험 및 부서 보고 문서를 2026-10-22 기준으로 전면 현행화
- `upload.html`과 `spl_upload.html` 우측 처리 파이프라인 아래에 `search.html`로 이동하는
  문서 조회 메뉴 추가
- `search.html` 상단에 기본 업로드 화면인 `spl_upload.html`로 이동하는 문서 업로드 메뉴 추가
- JAR 디컴파일 소스가 답변 근거에 포함될 때 원본 스택트레이스 라인과 디컴파일 결과의
  라인 범위가 다를 수 있음을 `notices` 및 답변 본문에 자동 표시
- 서로 다른 JAR·문서의 유사한 이름으로 발생하는 오탐을 막기 위해 중복 후보 조건에서
  제목 유사도를 제거하고 해시·경로·파일명·정확한 제목 일치만 사용
- 무인증 내부망 `/api/v1/external/answer`에서 JSON `stack_trace`, query 내 trace 및
  `text/plain` trace를 자동 판별하고 구조화된 예외·프레임 분석 결과를 반환
- 예외 유형, 최하위 원인, 오류 코드, Java 호출 프레임과 정규화 fingerprint를
  PostgreSQL/pgvector 및 JAR/Java 심볼 검색 질의에 반영
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
