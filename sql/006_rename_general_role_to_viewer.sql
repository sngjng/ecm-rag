-- 이전 배포의 general 역할을 viewer로 변경한다.
-- CHECK 제약을 먼저 제거해야 기존 행의 값을 안전하게 변경할 수 있다.
ALTER TABLE rag.user_access_roles
  DROP CONSTRAINT IF EXISTS user_access_roles_role_check;

UPDATE rag.user_access_roles
   SET role='viewer', updated_at=now()
 WHERE role='general';

ALTER TABLE rag.user_access_roles
  ADD CONSTRAINT user_access_roles_role_check
  CHECK (role IN ('viewer', 'uploader', 'admin'));
