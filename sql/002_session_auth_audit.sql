-- 사내 로그인 세션과 문서 업로드 감사 이력.
-- 기존 001_pgvector.sql 적용 DB에는 이 파일을 추가 적용한다.

CREATE TABLE IF NOT EXISTS rag.auth_sessions (
  session_id uuid PRIMARY KEY,
  token_hash char(64) NOT NULL UNIQUE,
  employee_id text NOT NULL,
  employee_name text NOT NULL,
  department text,
  email text,
  roles jsonb NOT NULL DEFAULT '[]'::jsonb,
  expires_at timestamptz NOT NULL,
  client_ip inet,
  user_agent text,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_active
  ON rag.auth_sessions(token_hash, expires_at) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_auth_sessions_employee
  ON rag.auth_sessions(employee_id, created_at DESC);

CREATE TABLE IF NOT EXISTS rag.upload_audit (
  audit_id uuid PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES rag.assets(asset_id),
  version_id uuid NOT NULL REFERENCES rag.asset_versions(version_id),
  job_id uuid NOT NULL REFERENCES rag.ingestion_jobs(job_id),
  action text NOT NULL CHECK (action IN ('new_asset','new_version')),
  employee_id text NOT NULL,
  employee_name text NOT NULL,
  department text,
  client_ip inet,
  user_agent text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_upload_audit_asset
  ON rag.upload_audit(asset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_upload_audit_employee
  ON rag.upload_audit(employee_id, created_at DESC);
