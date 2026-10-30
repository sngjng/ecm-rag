-- 007의 기본 장애 답변 템플릿에 오류 발생 시각 표시 요구사항을 보강한다.
-- 관리자가 다른 항목을 수정했더라도 기존 기본 안내 문구가 남아 있을 때만 해당 한 줄을 바꾼다.
UPDATE rag.response_templates
   SET content = replace(
         content,
         '- 오류 코드, 예외 클래스, 핵심 메시지를 정리합니다.',
         '- 오류 코드, 예외 클래스, 핵심 메시지를 정리하고 확인 가능한 경우 발생 타임스탬프를 함께 표시합니다.'
       ),
       updated_by = 'system:migration-008',
       updated_at = now()
 WHERE template_key = 'incident_resolution_guide'
   AND position(
         '- 오류 코드, 예외 클래스, 핵심 메시지를 정리합니다.'
         IN content
       ) > 0;
