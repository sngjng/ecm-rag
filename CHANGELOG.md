# Changelog

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
