-- 업로드 시점의 상위부서를 감사 이력에 함께 보존한다.
ALTER TABLE rag.upload_audit
  ADD COLUMN IF NOT EXISTS upper_department text;
