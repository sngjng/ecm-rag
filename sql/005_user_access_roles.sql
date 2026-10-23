-- 사원별 ECM-RAG 접근 권한을 관리한다.
-- 뷰어(viewer)는 조회, 업로더(uploader)는 관리자 기능을 제외한 전체 기능,
-- 관리자(admin)는 데이터 관리와 권한 관리까지 사용할 수 있다.
CREATE TABLE IF NOT EXISTS rag.user_access_roles (
  employee_id text PRIMARY KEY,
  employee_name text,
  department text,
  upper_department text,
  role text NOT NULL CHECK (role IN ('viewer', 'uploader', 'admin')),
  updated_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_user_access_roles_role
  ON rag.user_access_roles(role, employee_id);

COMMENT ON TABLE rag.user_access_roles IS
  'ECM-RAG 사원별 접근 권한. 미등록 사원은 로그인 시 uploader 권한을 받는다.';
