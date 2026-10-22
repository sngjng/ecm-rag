# 외부 질의응답 API curl 테스트

## 1. 실행 기준

아래 명령은 ECM-RAG 서버 내부에서 실행하는 기준입니다. 다른 회사망 PC에서 시험할 때는
`127.0.0.1`을 실제 ECM-RAG 서버 IP로 변경합니다. 이 endpoint는 내부망 연동용이므로
세션 쿠키와 API key를 보내지 않습니다. 별도 `export` 없이 각 명령에 전체 URL을 직접
사용합니다.

> **중요:** URL 앞뒤에 `<`와 `>`를 입력하지 않습니다. Markdown 자동 링크가 꺾쇠까지
> 복사되는 일을 막기 위해 아래 명령은 `'http'://'127.0.0.1:8300/...'`처럼 나눠
> 표기했습니다. Bash는 붙어 있는 문자열을 하나로 합치므로 실제 curl에는
> `http://127.0.0.1:8300/...`가 전달됩니다.

모든 예제는 응답 본문 마지막에 실제 HTTP 상태를 출력합니다. JSON을 보기 좋게 출력해야
한다면 `curl` 결과를 파일로 저장하거나 설치된 환경에서만 `jq`를 추가로 사용합니다.

## 2. 정상 질문

### TC-01 JSON 객체

등록된 자료의 고유 문구로 질문을 바꿔 시험합니다.

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: application/json; charset=utf-8' \
  -d '{
    "query": "업로드한 문서 중 A서버 설정 관련 문서를 찾아줘",
    "limit": 5,
    "include_chunks": true
  }'
```

기대 결과:

- HTTP `200`
- `request_type`: `question`
- `chunks_included`: `true`
- `generation_status`: `completed`, `unavailable`, `out_of_scope` 중 하나
- 관련 자료가 등록돼 있다면 `results`가 한 건 이상

### TC-02 LLM 답변만 반환

검색 청크는 내부적으로 답변 생성에 사용하지만 HTTP 응답의 `results`에서는 제외합니다.

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: application/json; charset=utf-8' \
  -d '{
    "query": "업로드한 문서 중 A서버 설정 관련 문서를 찾아줘",
    "limit": 5,
    "include_chunks": false
  }'
```

기대 결과:

- HTTP `200`
- `chunks_included`: `false`
- `answer`, `generation_status`, `notices`, `request_type` 포함
- `results` 필드는 포함되지 않음

### TC-03 JSON 문자열

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: application/json' \
  -d '"A서버 설정 관련 문서를 찾아줘"'
```

기대 결과: HTTP `200`, `request_type=question`

### TC-04 일반 문자열

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: text/plain; charset=utf-8' \
  --data-binary 'A서버 설정 관련 문서를 찾아줘'
```

기대 결과: HTTP `200`, `request_type=question`

## 3. 스택트레이스 분석

### TC-05 JSON의 stack_trace 필드

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: application/json; charset=utf-8' \
  --data-binary @- <<'JSON'
{
  "query": "원인과 관련 소스 및 조치 방법을 알려줘",
  "stack_trace": "java.lang.RuntimeException: DB query failed APP1001\n  at com.company.asset.AssetService.load(AssetService.java:143)\n  at org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:933)\nCaused by: java.sql.SQLSyntaxErrorException: ORA-00942: table or view does not exist\n  at com.company.asset.AssetRepository.find(AssetRepository.java:81)",
  "limit": 5,
  "include_chunks": true
}
JSON
```

기대 결과:

- HTTP `200`
- `request_type`: `stack_trace`
- `stack_trace_analysis.primary_exception`: `java.lang.RuntimeException`
- `stack_trace_analysis.error_codes`: `APP1001`, `ORA-00942` 포함
- `stack_trace_analysis.application_frames`: `AssetService.load`, `AssetRepository.find` 정보 포함
- 업로드한 JAR/Java와 package·class·method가 일치하면 `results`에 관련 소스 청크 포함

### TC-06 text/plain 자동 판별

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: text/plain; charset=utf-8' \
  --data-binary $'java.lang.IllegalStateException: response already committed\n  at com.company.gateway.ResponseWriter.write(ResponseWriter.java:107)\n  at com.company.gateway.GatewayService.send(GatewayService.java:63)'
```

기대 결과: HTTP `200`, `request_type=stack_trace`, `stack_trace_analysis` 포함

## 4. 등록 문서 범위 밖 질문

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: application/json' \
  -d '{"query":"오늘 날씨가 어때?","limit":3}'
```

관련 자료가 없으면 HTTP `200`, `generation_status=out_of_scope`, 빈 `results`가 정상입니다.

## 5. 입력 오류

### TC-07 손상된 JSON

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: application/json' \
  --data-binary '{"query":'
```

기대 결과: HTTP `400`

### TC-08 빈 본문

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: text/plain' \
  --data-binary ''
```

기대 결과: HTTP `422`

### TC-09 잘못된 stack_trace 타입

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: application/json' \
  -d '{"query":"오류 분석","stack_trace":["문자열이 아닌 배열"]}'
```

기대 결과: HTTP `422`

### TC-10 limit 허용 범위 초과

```bash
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: application/json' \
  -d '{"query":"A서버 설정 문서","limit":999}'
```

기대 결과: HTTP `422`

### TC-11 16 KiB 초과

```bash
head -c 16385 /dev/zero | tr '\0' 'x' | \
curl -sS -w '\nHTTP_STATUS=%{http_code}\n' \
  -X POST 'http'://'127.0.0.1:8300/api/v1/external/answer' \
  -H 'Content-Type: text/plain; charset=utf-8' \
  --data-binary @-
```

기대 결과: HTTP `413`

## 6. 정상 응답 확인 항목

| 필드 | 확인 내용 |
|---|---|
| `answer` | 등록 문서 근거를 사용한 답변 또는 상태 안내 |
| `generation_status` | `completed`, `unavailable`, `out_of_scope` |
| `request_type` | `question` 또는 `stack_trace` |
| `chunks_included` | 요청한 청크 반환 여부 |
| `results` | `include_chunks=true`일 때 검색된 자산·버전·청크와 점수·출처 |
| `notices` | JAR 디컴파일 라인 번호 차이 등의 주의사항 |
| `stack_trace_analysis` | 스택 요청일 때만 예외·오류코드·호출 프레임 정보 |

`generation_status=unavailable`이면서 `results`가 존재하면 검색은 성공했지만 LLM 호출만
실패한 상태입니다. `out_of_scope`와 빈 `results`는 등록 문서 범위 밖 질문에 대한 정상
응답입니다.
