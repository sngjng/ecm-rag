# 사내 Java 로그인 인터페이스 연동 계약

> 현행 기본 provider는 향후 구현할 `service/login.py`의 `python_module`입니다. 이 문서는
> Java wrapper를 선택적으로 유지해야 하는 환경에서만 적용합니다.

## 목적

FastAPI가 사내 로그인 Java 라이브러리의 구현 세부사항을 알지 않도록 실행 가능한
wrapper를 경계로 사용합니다. Python은 인증 요청과 사원정보 응답만 JSON으로 교환하며
비밀번호를 저장하거나 감사 이력에 기록하지 않습니다.

현재 배포 기본값은 로그인 구현 전 임시 모드(`RAG_AUTH_ENABLED=false`)입니다. 이 문서의
Java 연동 계약을 구현하고 시험한 뒤 값을 `true`로 바꾸면 로그인 화면과 세션 검증이
활성화됩니다.

## 실행 설정

```bash
export RAG_AUTH_ENABLED=true
export RAG_AUTH_COMMAND='/srv/ecm-rag/bin/company-auth-wrapper'
```

여러 인자가 필요하면 하나의 실행 명령 문자열로 설정할 수 있습니다.

```bash
export RAG_AUTH_COMMAND='java -jar /srv/ecm-rag/lib/company-auth-adapter.jar'
```

Python은 shell을 사용하지 않고 명령과 인자를 분리해 실행합니다. 운영 서비스 계정에
wrapper와 JAR 실행 권한만 부여하고 소유자 외 쓰기 권한은 제거해야 합니다.

local, test 및 unit-test profile에서는 Java 모듈 없이 `test / test` 계정을 사용할 수
있습니다. 이 계정은 `development_stub` provider에서만 동작하며 Rocky Linux 운영
profile은 기본 `java_command`를 유지합니다.

인증 활성화 시 로그인 성공 또는 유효한 기존 세션 확인 후 브라우저 기본 이동 위치는
`/spl_upload.html`입니다. 일반 자산·업로드·검색·채팅 API는 세션 인증 대상입니다.
임시 모드에서는 `/`이 간편 업로드를 바로 반환하고 같은 API들이 임시 사용자로 동작합니다.

## 표준입력 요청

UTF-8 JSON 한 건을 EOF까지 읽습니다.

```json
{
  "employee_id": "E12345",
  "password": "사용자 입력값"
}
```

비밀번호를 명령행 인자로 전달하지 않으므로 프로세스 목록에 노출되지 않습니다.

## 표준출력 응답

인증 성공 시 종료 코드 `0`과 JSON 한 건만 stdout에 출력합니다.

```json
{
  "authenticated": true,
  "employee_id": "E12345",
  "employee_name": "홍길동",
  "department": "IDC본부",
  "email": "user@company.local",
  "roles": ["uploader"]
}
```

- `employee_id`, `employee_name`: 필수
- `department`, `email`, `roles`: 선택
- 디버그 메시지와 라이브러리 로그는 stdout이 아니라 stderr로 출력
- stderr에 비밀번호, 인증 토큰 또는 개인정보 전체를 출력하지 않음

인증 실패는 0이 아닌 종료 코드로 반환합니다. Python API는 상세 내부 오류를 사용자에게
노출하지 않고 동일한 로그인 실패 메시지로 변환합니다.

## 세션과 업로드 이력

인증 성공 후 FastAPI는 48바이트 이상의 무작위 토큰을 생성합니다.

- 브라우저: `HttpOnly`, `SameSite=Strict`, 운영환경 `Secure` 쿠키
- PostgreSQL: 원문이 아닌 SHA-256 토큰 해시와 사원정보 snapshot
- 기본 만료: 8시간, YAML의 `security.authentication.session_hours`로 변경
- 로그아웃: DB 세션 폐기 후 쿠키 삭제

문서 접수 시 `upload_audit`에 다음 값을 Version/Job과 같은 트랜잭션으로 저장합니다.

- Asset ID, Version ID, Job ID
- 신규 자산 또는 기존 자산의 새 버전
- 사번, 성명, 부서
- 접속 IP, User-Agent, 접수 시각

내부 시스템 연동용 `POST /api/v1/external/answer`만 의도적으로 세션/API 키 검증을 하지
않습니다. Java 로그인 예외 경로가 아니라 별도 내부망 경계이며, 운영에서는 방화벽 또는
reverse proxy ACL로 승인된 사내 대역만 접근시켜야 합니다.

## DB 적용

신규 설치는 최신 `sql/001_pgvector.sql`을 적용합니다. 기존 DB에는 다음 SQL을 추가로
실행합니다.

```bash
psql -d ragdb -f sql/002_session_auth_audit.sql
```

## 구현 확인 항목

1. 비밀번호가 Java/Python 로그에 남지 않는지 확인
2. 잘못된 계정, 잠긴 계정, 비밀번호 만료 응답 확인
3. Java timeout과 프로세스 장애 시 API가 503을 반환하는지 확인
4. HTTPS에서 Secure 쿠키가 정상 발급되는지 확인
5. 로그아웃 및 8시간 만료 후 기존 쿠키가 거부되는지 확인
6. 업로드 감사 이력의 사번·부서가 실제 로그인 사용자와 일치하는지 확인
