-- 사내 EAI 로그인 응답의 user_id와 상위부서명을 세션 snapshot에 보존한다.
-- 기존 DB에 반복 적용해도 안전하며, 과거 세션은 employee_id를 user_id 기본값으로 사용한다.

ALTER TABLE rag.auth_sessions
  ADD COLUMN IF NOT EXISTS user_id text;

ALTER TABLE rag.auth_sessions
  ADD COLUMN IF NOT EXISTS upper_department text;

UPDATE rag.auth_sessions
   SET user_id = employee_id
 WHERE user_id IS NULL OR btrim(user_id) = '';
