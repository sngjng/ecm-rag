-- PostgreSQL 17 + pgvector; ragdb에서 DBA가 한 번 실행한다.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS rag;

-- 한 업로드의 추적과 원본 버전을 보관한다. 기존 Chroma는 수정하지 않는다.
CREATE TABLE IF NOT EXISTS rag.assets (
  asset_id uuid PRIMARY KEY,
  filename text NOT NULL,
  content_type text NOT NULL CHECK (content_type IN ('document','incident','error_trace','source_code')),
  subtype text NOT NULL,
  title text NOT NULL,
  system_name text,
  version_label text,
  repository text,
  branch text,
  source_path text NOT NULL,
  sha256 char(64) NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','completed','failed')),
  error_detail text,
  chunk_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  indexed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_assets_type ON rag.assets(content_type, subtype);
CREATE INDEX IF NOT EXISTS idx_assets_system ON rag.assets(system_name);
CREATE INDEX IF NOT EXISTS idx_assets_hash ON rag.assets(sha256);

-- 청크 ID는 업로드 버전(asset_id)에 종속시킨다. 중복 업로드 시 구 버전은 남긴다.
CREATE TABLE IF NOT EXISTS rag.chunks (
  chunk_id text PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  kind text NOT NULL,
  content text NOT NULL,
  parent_id text,
  previous_chunk_id text,
  next_chunk_id text,
  page_start integer,
  page_end integer,
  line_start integer,
  line_end integer,
  identifier text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(1024),
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chunks_asset ON rag.chunks(asset_id);
CREATE INDEX IF NOT EXISTS idx_chunks_parent ON rag.chunks(parent_id);
CREATE INDEX IF NOT EXISTS idx_chunks_identifier ON rag.chunks(identifier);
CREATE INDEX IF NOT EXISTS idx_chunks_identifier_trgm ON rag.chunks USING gin(identifier gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_search ON rag.chunks USING gin(search_vector);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON rag.chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS rag.error_events (
  error_event_id uuid PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  occurred_at timestamptz,
  error_code text,
  exception_class text,
  message text,
  raw_trace text NOT NULL,
  fingerprint char(64) NOT NULL,
  system_name text
);
CREATE INDEX IF NOT EXISTS idx_errors_code ON rag.error_events(error_code);
CREATE INDEX IF NOT EXISTS idx_errors_exception ON rag.error_events(exception_class);
CREATE INDEX IF NOT EXISTS idx_errors_fingerprint ON rag.error_events(fingerprint);
CREATE TABLE IF NOT EXISTS rag.stack_frames (
  error_event_id uuid NOT NULL REFERENCES rag.error_events(error_event_id) ON DELETE CASCADE,
  frame_seq integer NOT NULL,
  class_name text,
  method_name text,
  file_name text,
  line_number integer,
  PRIMARY KEY(error_event_id, frame_seq)
);
CREATE INDEX IF NOT EXISTS idx_frames_location ON rag.stack_frames(file_name, line_number);
CREATE INDEX IF NOT EXISTS idx_frames_class_method ON rag.stack_frames(class_name, method_name);

CREATE TABLE IF NOT EXISTS rag.code_symbols (
  symbol_id uuid PRIMARY KEY,
  asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  chunk_id text NOT NULL REFERENCES rag.chunks(chunk_id) ON DELETE CASCADE,
  repository text,
  branch text,
  file_path text NOT NULL,
  language text NOT NULL,
  symbol_type text NOT NULL,
  class_name text,
  symbol_name text NOT NULL,
  signature text,
  start_line integer NOT NULL,
  end_line integer NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_lookup ON rag.code_symbols(symbol_name, class_name);
CREATE INDEX IF NOT EXISTS idx_symbols_file_line ON rag.code_symbols(file_path, start_line, end_line);
-- 추후 관계가 확정되면 문서/장애/코드 사이 링크를 별도 적재한다.
CREATE TABLE IF NOT EXISTS rag.asset_relations (
  source_asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  target_asset_id uuid NOT NULL REFERENCES rag.assets(asset_id) ON DELETE CASCADE,
  relation_type text NOT NULL,
  evidence text,
  PRIMARY KEY(source_asset_id, target_asset_id, relation_type)
);
