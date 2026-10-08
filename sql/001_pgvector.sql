-- PostgreSQL 17 + pgvector 단일 저장소 스키마.
-- 신규 DB에서 DBA가 먼저 vector/pg_trgm 확장을 설치한 뒤 이 파일을 실행한다.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS rag;

-- Asset은 문서/장애/로그/소스의 논리적 정체성이다. 파일 버전과 처리 상태는 분리한다.
CREATE TABLE IF NOT EXISTS rag.assets (
  asset_id uuid PRIMARY KEY,
  asset_type text NOT NULL CHECK (asset_type IN ('document','incident','error_trace','source_code')),
  subtype text NOT NULL DEFAULT 'general',
  title text NOT NULL,
  vendor text,
  product text,
  system_name text,
  service_name text,
  component text,
  owner_org text,
  security_level text NOT NULL DEFAULT 'internal',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_assets_type ON rag.assets(asset_type, subtype) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_scope ON rag.assets(system_name, product, component) WHERE deleted_at IS NULL;

-- 동일 Asset의 원본 버전, lifecycle, lineage를 관리한다.
CREATE TABLE IF NOT EXISTS rag.asset_versions (
  version_id uuid PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  version_label text NOT NULL,
  source_filename text NOT NULL,
  source_path text NOT NULL,
  sha256 char(64) NOT NULL,
  repository text,
  branch text,
  logical_path text,
  lifecycle_status text NOT NULL DEFAULT 'draft'
    CHECK (lifecycle_status IN ('draft','approved','obsolete')),
  is_current boolean NOT NULL DEFAULT true,
  effective_from date,
  effective_to date,
  parser_name text,
  parser_version text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  indexed_at timestamptz,
  UNIQUE(asset_id, sha256)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_current_version
  ON rag.asset_versions(asset_id) WHERE is_current;
CREATE INDEX IF NOT EXISTS idx_versions_lifecycle
  ON rag.asset_versions(lifecycle_status, is_current, effective_from);
CREATE INDEX IF NOT EXISTS idx_versions_hash ON rag.asset_versions(sha256);

-- API와 처리 워커 사이의 영속 큐다. lease 만료 작업은 다른 워커가 자동 재획득한다.
CREATE TABLE IF NOT EXISTS rag.ingestion_jobs (
  job_id uuid PRIMARY KEY,
  version_id uuid NOT NULL UNIQUE REFERENCES rag.asset_versions(version_id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','processing','completed','failed','cancelled')),
  priority integer NOT NULL DEFAULT 100,
  attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  worker_id text,
  locked_at timestamptz,
  heartbeat_at timestamptz,
  error_detail text,
  chunk_count integer NOT NULL DEFAULT 0,
  report jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim
  ON rag.ingestion_jobs(status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_lease
  ON rag.ingestion_jobs(status, heartbeat_at) WHERE status='processing';

-- display_content는 사용자/LLM 제시용, embedding_content는 metadata context가 포함된 검색용이다.
CREATE TABLE IF NOT EXISTS rag.chunks (
  chunk_id text PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  version_id uuid NOT NULL REFERENCES rag.asset_versions(version_id) ON DELETE CASCADE,
  chunk_seq integer NOT NULL,
  kind text NOT NULL,
  display_content text NOT NULL,
  embedding_content text NOT NULL,
  parent_id text,
  previous_chunk_id text,
  next_chunk_id text,
  page_start integer,
  page_end integer,
  line_start integer,
  line_end integer,
  identifier text,
  heading_path text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  embedding_model text NOT NULL,
  embedding vector(1024) NOT NULL,
  search_vector tsvector GENERATED ALWAYS AS
    (to_tsvector('simple', coalesce(identifier, '') || ' ' || display_content)) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(version_id, chunk_seq)
);
CREATE INDEX IF NOT EXISTS idx_chunks_asset_version ON rag.chunks(asset_id, version_id);
CREATE INDEX IF NOT EXISTS idx_chunks_parent ON rag.chunks(parent_id);
CREATE INDEX IF NOT EXISTS idx_chunks_identifier ON rag.chunks(identifier);
CREATE INDEX IF NOT EXISTS idx_chunks_identifier_trgm ON rag.chunks USING gin(identifier gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_search ON rag.chunks USING gin(search_vector);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON rag.chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS rag.error_events (
  error_event_id uuid PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  version_id uuid NOT NULL REFERENCES rag.asset_versions(version_id) ON DELETE CASCADE,
  chunk_id text REFERENCES rag.chunks(chunk_id) ON DELETE CASCADE,
  occurred_at timestamptz,
  error_code text,
  exception_class text,
  severity text,
  message text,
  root_cause text,
  raw_trace text NOT NULL,
  fingerprint char(64) NOT NULL,
  system_name text
);
CREATE INDEX IF NOT EXISTS idx_errors_code ON rag.error_events(error_code);
CREATE INDEX IF NOT EXISTS idx_errors_exception ON rag.error_events(exception_class);
CREATE INDEX IF NOT EXISTS idx_errors_fingerprint ON rag.error_events(fingerprint);
CREATE INDEX IF NOT EXISTS idx_errors_time ON rag.error_events(occurred_at);

CREATE TABLE IF NOT EXISTS rag.stack_frames (
  error_event_id uuid NOT NULL REFERENCES rag.error_events(error_event_id) ON DELETE CASCADE,
  frame_seq integer NOT NULL,
  class_name text,
  method_name text,
  file_name text,
  line_number integer,
  is_application_code boolean,
  PRIMARY KEY(error_event_id, frame_seq)
);
CREATE INDEX IF NOT EXISTS idx_frames_location ON rag.stack_frames(file_name, line_number);
CREATE INDEX IF NOT EXISTS idx_frames_class_method ON rag.stack_frames(class_name, method_name);

CREATE TABLE IF NOT EXISTS rag.code_symbols (
  symbol_id uuid PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  version_id uuid NOT NULL REFERENCES rag.asset_versions(version_id) ON DELETE CASCADE,
  chunk_id text NOT NULL REFERENCES rag.chunks(chunk_id) ON DELETE CASCADE,
  repository text,
  branch text,
  file_path text NOT NULL,
  language text NOT NULL,
  symbol_type text NOT NULL,
  package_name text,
  class_name text,
  symbol_name text NOT NULL,
  signature text,
  start_line integer NOT NULL,
  end_line integer NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_lookup ON rag.code_symbols(symbol_name, class_name);
CREATE INDEX IF NOT EXISTS idx_symbols_name_trgm ON rag.code_symbols USING gin(symbol_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_symbols_file_line ON rag.code_symbols(file_path, start_line, end_line);

CREATE TABLE IF NOT EXISTS rag.asset_relations (
  source_asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  target_asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  relation_type text NOT NULL CHECK
    (relation_type IN ('supersedes','references','related_to','derived_from','resolved_by','applies_to')),
  evidence text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(source_asset_id, target_asset_id, relation_type),
  CHECK (source_asset_id <> target_asset_id)
);
