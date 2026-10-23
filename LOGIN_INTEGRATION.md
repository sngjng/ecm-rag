# `login.py` 사내 EAI 로그인 연동

최종 현행화: 2026-10-23

## 1. 구현 상태

[`service/login.py`](service/login.py)에 사용자가 제공한 JPype/Java VO 로그인 규격을
FastAPI용으로 적용했습니다. 외부 사내망과 Java 라이브러리가 없는 개발 PC에서는 실제
EAI 호출을 검증할 수 없으므로 기본 설정은 `RAG_AUTH_ENABLED=false`입니다.

인증을 활성화하면 다음 경로로 동작합니다.

```text
POST /api/v1/auth/login
  → service/routes/auth.py
  → service/auth.py (provider=python_module)
  → service/login.py:authenticate_employee()
  → JPype JVM / OutboundProcess / ZZUL0001VO / ZZUL0002VO
  → rag.auth_sessions 저장
  → HttpOnly 세션 쿠키 반환
```

Java 클래스·property·JAR는 모두 `/Appl/chatllm/java`를 classpath에 포함해 읽습니다.
FastAPI 프로세스 전체에 영향을 주는 `os.chdir()`는 사용하지 않습니다.

## 2. 정상 응답 매핑

2026-10-23 사내 EAI 실호출에서 확인된 정상 응답은 다음과 같이 애플리케이션 세션에
매핑됩니다.

| EAI `UserVO` | 예시 | ECM-RAG 세션 |
|---|---|---|
| `emp_no` | `IB1020` | `employee_id` |
| `user_id` | `IB1020` | `user_id` |
| `user_name` | `오최나` | `employee_name` |
| `dept_name` | `아키텍쳐팀` | `department` |
| `up_dept_name` | `ACS센터` | `upper_department` |

`emp_no`가 비어 있으면 `user_id`를 `employee_id`로 사용합니다. 인증된 일반 사용자의
기본 역할은 `uploader`입니다. 비밀번호, 암호화 값, 전체 Java 응답 VO는 저장하거나
로그에 출력하지 않습니다.

## 3. 설치 라이브러리

운영 pip 목록과 같은 버전을 직접 요구사항에 포함했습니다.

```text
jpype1==1.7.1
```

전체 프로젝트 의존성을 다시 설치하거나, 이미 나머지가 설치된 서버에서는 다음 패키지만
추가할 수 있습니다.

```bash
python -m pip install JPype1==1.7.1
```

## 4. 환경 설정

프로젝트 루트 `.env`에 다음 값을 설정합니다.

```dotenv
RAG_AUTH_ENABLED=true
RAG_AUTH_JVM_PATH=/Product/jdk/open/jdk-21.0.11+10/lib/server/libjvm.so
RAG_AUTH_JAVA_DIR=/Appl/chatllm/java
RAG_AUTH_CLASSPATH=
RAG_AUTH_INTERFACE_ID=IR_HI-FMSCLIENT_0001
RAG_AUTH_SYSTEM_ID=000164
```

`RAG_AUTH_CLASSPATH`가 비어 있으면 다음 항목을 자동으로 사용합니다.

```text
/Appl/chatllm/java/jackson-databind-2.10.2.jar
/Appl/chatllm/java/jackson-core-2.10.2.jar
/Appl/chatllm/java/jackson-annotations-2.10.2.jar
/Appl/chatllm/java/eai-app.jar
/Appl/chatllm/java/jef-application-parser-jdk5-0.1.jar
/Appl/chatllm/java/JsonConverter-0.1.jar
/Appl/chatllm/java/json-simple-1.1.jar
/Appl/chatllm/java
```

배치 파일명이나 위치가 다른 환경에서는 Linux `:` 구분자로 `RAG_AUTH_CLASSPATH` 전체를
명시합니다. Java 클래스명이 다른 환경에서는 아래 선택 변수를 사용할 수 있습니다.

```dotenv
RAG_AUTH_OUTBOUND_CLASS=cis.internal.process.outbound.OutboundProcess
RAG_AUTH_ENCRYPT_CLASS=cis.internal.util.EncryptUtil
RAG_AUTH_REQUEST_VO_CLASS=ZZUL0001VO
RAG_AUTH_RESPONSE_VO_CLASS=ZZUL0002VO
```

## 5. DB migration과 기동

기존 DB에는 `user_id`와 `upper_department` 컬럼을 추가해야 합니다. `bin/start.sh`는 모든
필수 migration을 자동 확인하지만, 수동 적용 시 다음 명령을 사용합니다.

```bash
cd /mnt/appl/ecm-rag
set -a
source .env
set +a

"$ECM_RAG_PYTHON" -m scripts.apply_migrations
./bin/stop.sh
./bin/start.sh
```

적용되는 신규 파일은 [`sql/004_eai_login_identity.sql`](sql/004_eai_login_identity.sql)과
[`sql/005_user_access_roles.sql`](sql/005_user_access_roles.sql)입니다. 최초 관리자 사번은
`.env`에 `RAG_AUTH_ADMIN_EMPLOYEE_IDS=IB1020`처럼 지정한 뒤 로그인합니다. 관리자 화면의
`사용자 권한` 탭에서 뷰어·업로더·관리자 권한을 등록할 수 있으며 미등록 사원은 업로더
권한으로 로그인됩니다.

## 6. 사내망 확인 절차

로그인 화면에서 시험하거나 curl로 endpoint를 직접 호출합니다. 실제 비밀번호가 shell
history에 남지 않도록 운영 확인은 브라우저 사용을 권장합니다.

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "employee_id": "IB1020",
  "password": "사용자 입력 비밀번호"
}
```

정상 응답 예시는 다음과 같습니다.

```json
{
  "employee_id": "IB1020",
  "user_id": "IB1020",
  "employee_name": "오최나",
  "department": "아키텍쳐팀",
  "upper_department": "ACS센터",
  "email": "",
  "roles": ["uploader"],
  "authentication_enabled": true,
  "temporary_access": false
}
```

확인 항목:

1. 정상 계정으로 로그인 후 `/api/v1/auth/me` 응답의 다섯 개 사원정보 확인
2. 잘못된 비밀번호와 잠긴 계정이 HTTP `401`인지 확인
3. Java/JVM/classpath 또는 EAI 장애가 HTTP `503`인지 확인
4. JVM이 API 프로세스당 한 번만 초기화되는지 로그 확인
5. 비밀번호와 암호화 결과가 API 로그·DB에 남지 않는지 확인
6. 업로드 감사 이력의 사번·성명·부서가 로그인 사용자와 일치하는지 확인
7. 로그아웃·세션 만료 후 기존 쿠키가 HTTP `401`로 거부되는지 확인

## 7. 현재 검증 범위

개발 환경에서는 실제 EAI 성공 응답을 고정 fixture로 사용해 다음 매핑을 단위
테스트합니다. 테스트 자체는 사내 EAI를 다시 호출하지 않습니다.

```text
IB1020 / 오최나 / 아키텍쳐팀 / ACS센터
```

```bash
RAG_PROFILE=local python -m unittest tests.test_login_eai_response -v
```

실제 JVM 기동, 암호화, `IR_HI-FMSCLIENT_0001` 통신과 계정 오류 유형은 사내 테스트
환경에서 최종 검증해야 합니다.
