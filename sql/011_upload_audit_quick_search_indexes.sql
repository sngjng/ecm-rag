-- 사번 일치, 이름·부서 부분 일치, 등록일 범위 검색에 사용하는 인덱스.
CREATE INDEX IF NOT EXISTS idx_upload_audit_employee_lower
  ON rag.upload_audit (lower(employee_id));
CREATE INDEX IF NOT EXISTS idx_upload_audit_employee_name_trgm
  ON rag.upload_audit USING gin (employee_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_upload_audit_department_trgm
  ON rag.upload_audit USING gin (department gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_upload_audit_upper_department_trgm
  ON rag.upload_audit USING gin (upper_department gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_upload_audit_registered_order
  ON rag.upload_audit (created_at DESC, asset_id DESC);
