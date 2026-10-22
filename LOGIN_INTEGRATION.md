# `login.py` 사내 로그인 연동 계약

최종 현행화: 2026-10-22

## 현재 상태

현재는 `RAG_AUTH_ENABLED=false`가 기본이므로 로그인 화면과 세션 검증을 사용하지 않습니다.
FastAPI는 임시 사용자로 업로드 감사 이력을 기록합니다.

## 구현 위치

향후 로그인 연동은 [`service/login.py`](service/login.py)의 다음 함수 본문에 구현합니다.

```python
def authenticate_employee(employee_id: str, password: str) -> EmployeeIdentity:
    ...
```

성공 시 다음 필드의 `EmployeeIdentity`를 반환합니다.

- `employee_id`: 사번, 필수
- `employee_name`: 성명, 필수
- `department`: 부서
- `email`: 이메일
- `roles`: 역할 문자열 목록

로그인 실패는 `HTTPException(401, ...)`, 사내 연동 장애는 502 또는 503으로 변환합니다.
비밀번호, 인증 토큰, 사내 시스템 응답 원문은 로그·DB·HTTP 응답에 저장하거나 노출하지
않습니다.

## FastAPI 연결

화면 또는 외부 클라이언트가 호출할 endpoint는 이미 준비되어 있습니다.

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "employee_id": "사번",
  "password": "사용자 입력 비밀번호"
}
```

`service/routes/auth.py`가 입력을 받고 `service/auth.py`의 provider dispatcher를 거쳐
`service/login.py`를 호출합니다. 인증 성공 후에는 기존 구현이 PostgreSQL 세션을 만들고
HttpOnly 쿠키를 발급하므로 endpoint나 업로드·검색 route를 다시 수정할 필요가 없습니다.

```text
POST /api/v1/auth/login
  → service/routes/auth.py
  → service/auth.py (provider=python_module)
  → service/login.py:authenticate_employee()
  → rag.auth_sessions 저장
  → HttpOnly 세션 쿠키 반환
```

## 활성화

구현과 단위·통합 시험을 마친 뒤 `.env`를 변경하고 서비스를 재시작합니다.

```dotenv
RAG_AUTH_ENABLED=true
```

`config/settings.yaml`의 기본 provider는 `python_module`입니다. 기존 Java wrapper 방식을
사용해야 할 때만 profile에서 `provider: java_command`와 `RAG_AUTH_COMMAND`를 설정합니다.

## 확인 항목

1. 정상/잘못된 계정, 잠금, 비밀번호 만료 처리
2. 사내 인증 서버 timeout과 장애의 502/503 변환
3. 비밀번호와 토큰이 애플리케이션·연동 서버 로그에 남지 않는지 확인
4. 로그인 후 `/api/v1/auth/me`에서 사원정보 확인
5. 로그아웃 및 세션 만료 후 기존 쿠키가 401로 거부되는지 확인
6. 업로드 감사 이력의 사번·성명·부서가 로그인 사용자와 일치하는지 확인
