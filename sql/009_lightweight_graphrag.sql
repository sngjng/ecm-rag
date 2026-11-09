-- PostgreSQL·pgvector 위에 별도 그래프 DB 없이 엔터티/관계/근거를 저장한다.
-- 동일 엔터티는 유형+정규화 이름으로 통합하고 모든 관계는 원문 chunk 근거를 가진다.
CREATE TABLE IF NOT EXISTS rag.knowledge_entities (
  entity_id uuid PRIMARY KEY,
  entity_type text NOT NULL,
  canonical_name text NOT NULL,
  normalized_name text NOT NULL,
  description text,
  properties jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(entity_type, normalized_name)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_name
  ON rag.knowledge_entities(normalized_name);
CREATE INDEX IF NOT EXISTS idx_knowledge_entities_name_trgm
  ON rag.knowledge_entities USING gin(canonical_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS rag.chunk_entities (
  chunk_id text NOT NULL REFERENCES rag.chunks(chunk_id) ON DELETE CASCADE,
  entity_id uuid NOT NULL REFERENCES rag.knowledge_entities(entity_id) ON DELETE CASCADE,
  mention_text text,
  relevance real NOT NULL DEFAULT 1 CHECK (relevance >= 0 AND relevance <= 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(chunk_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_chunk_entities_entity
  ON rag.chunk_entities(entity_id, chunk_id);

CREATE TABLE IF NOT EXISTS rag.knowledge_relations (
  relation_id uuid PRIMARY KEY,
  source_entity_id uuid NOT NULL REFERENCES rag.knowledge_entities(entity_id) ON DELETE CASCADE,
  target_entity_id uuid NOT NULL REFERENCES rag.knowledge_entities(entity_id) ON DELETE CASCADE,
  relation_type text NOT NULL,
  description text,
  confidence real NOT NULL DEFAULT 1 CHECK (confidence >= 0 AND confidence <= 1),
  properties jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(source_entity_id, target_entity_id, relation_type),
  CHECK (source_entity_id <> target_entity_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source
  ON rag.knowledge_relations(source_entity_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target
  ON rag.knowledge_relations(target_entity_id, relation_type);

CREATE TABLE IF NOT EXISTS rag.relation_evidence (
  relation_id uuid NOT NULL REFERENCES rag.knowledge_relations(relation_id) ON DELETE CASCADE,
  chunk_id text NOT NULL REFERENCES rag.chunks(chunk_id) ON DELETE CASCADE,
  evidence_text text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(relation_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_relation_evidence_chunk
  ON rag.relation_evidence(chunk_id, relation_id);
