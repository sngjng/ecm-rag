-- 장애 해결 가이드의 공용 답변 템플릿을 저장한다.
-- 조회와 분석은 인증 사용자에게 허용하지만 수정 endpoint는 admin 역할만 사용한다.
CREATE TABLE IF NOT EXISTS rag.response_templates (
  template_key varchar(100) PRIMARY KEY,
  content text NOT NULL,
  updated_by varchar(50),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT response_templates_content_length
    CHECK (char_length(content) BETWEEN 1 AND 4000)
);

INSERT INTO rag.response_templates (template_key, content)
VALUES (
  'incident_resolution_guide',
  '# 장애 해결 가이드

## 1. 핵심 장애 요약
- 발생한 오류와 영향 범위를 간결하게 설명합니다.

## 2. 주요 오류 코드 및 예외
- 오류 코드, 예외 클래스, 핵심 메시지를 정리하고 확인 가능한 경우 발생 타임스탬프를 함께 표시합니다.

## 3. 추정 원인
- 입력 로그와 등록 문서 근거를 구분하여 가능성이 높은 순서로 설명합니다.

## 4. 확인 절차
- 운영자가 안전하게 수행할 확인 명령과 확인 포인트를 순서대로 제시합니다.

## 5. 해결 가이드
- 즉시 조치, 근본 조치, 재발 방지 조치를 구분합니다.

## 6. 추가 필요 정보
- 판단 근거가 부족하면 필요한 로그·설정·환경 정보를 명시합니다.'
)
ON CONFLICT (template_key) DO NOTHING;
