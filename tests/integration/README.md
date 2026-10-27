# PostgreSQL 서비스 통합 시험

## 자동 통합 테스트

`test_postgres_service.py`는 Python 3.11과 PostgreSQL 17 + pgvector 환경에서 다음
핵심 흐름을 자동 검증합니다.

- `/health/live`, `/health/ready`와 임시 무세션 사용자 응답
- Asset 생성·목록·수정·soft delete
- TXT 문서 업로드와 PostgreSQL queue 등록
- worker claim, 파싱, deterministic embedding, pgvector 적재
- FTS·pgvector 기반 검색 결과와 1024차원 벡터 저장
- 기존 문서 후보 확인 후 새 Version 연결과 업로드 감사 이력
- 비슷한 제목(`builder.jar`/`builder-api.jar`)만으로 중복 후보가 되지 않는지 확인

테스트 DB에 `sql/001_pgvector.sql`을 적용한 뒤 다음과 같이 실행합니다.

```bash
export RAG_PROFILE=unit-test
export RAG_DB_DSN='postgresql://ragtest:비밀번호@127.0.0.1:55432/ragdb'
.venv/bin/python -m pytest -q
```

`RAG_DB_DSN`이 없으면 PostgreSQL 통합 테스트만 skip되고 나머지 단위 테스트는
계속 실행됩니다. 통합 테스트는 전용 테스트 DB의 `rag.assets` 및 종속 데이터를
초기화하므로 운영 DB를 DSN으로 지정하면 안 됩니다.

## 폐쇄망 운영 인수시험

다음 검증은 Rocky Linux 9/Python 3.11/PostgreSQL 17/실제 모델 환경에서 수행합니다.

1. `sql/001_pgvector.sql` 신규 설치 및 최소권한 계정 접속
2. Asset CRUD와 soft delete
3. 동일 Asset 신규 버전 등록 시 이전 `is_current=false` 전환
4. PDF/DOCX/TXT/장애/로그/Java/Python 각 1건 처리 완료
5. Worker 강제 종료 후 lease 만료·자동 재처리
6. 잘못된 DRM 출력, 모델 timeout, DB 재시작 시 재시도/실패 상태
7. exact error code, FTS, vector, metadata filter, reranker 결과 검증
8. 최신 승인 버전 기본 필터와 `include_obsolete=true` 검증
9. OpenWebUI에서 파일명·버전·페이지/라인 인용 표시
10. 기본 10MiB 파일 제한, 경로 traversal, 세션 만료·로그아웃, TLS proxy 검증
11. 복호화 임시파일 삭제와 canonical/DB 평문 접근권한 확인
12. 백업·복구 후 vector index와 검색 결과 일치 확인
13. JAR CFR 디컴파일과 직접 Java 업로드의 package/class/method·예외·오류코드 적재
14. 스택트레이스의 오류 frame과 `code_symbols` 연결, 디컴파일 line 차이 안내 확인
15. `/api/v1/chat`과 `/api/v1/external/answer`의 동일 근거·답변 정책과 외부 ACL 확인
16. `RAG_AUTH_ENABLED=true` 전환 후 로그인 세션·만료·로그아웃 회귀 시험
