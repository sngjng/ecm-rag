# 폐쇄망 통합 시험 체크리스트

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
10. 100 MB 파일 제한, 경로 traversal, API key, TLS proxy 검증
11. 복호화 임시파일 삭제와 canonical/DB 평문 접근권한 확인
12. 백업·복구 후 vector index와 검색 결과 일치 확인
