-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Unified embeddings table for all embeddable content:
-- - Concept text chunks (title, abstract, etc.)
-- - KMS terms (instruments, platforms, science keywords)
CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY,
    type VARCHAR(50) NOT NULL,
    external_id VARCHAR(100) NOT NULL,
    attribute VARCHAR(100) NOT NULL,
    text_content TEXT NOT NULL,
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Index for fast lookups and deletes by external_id
CREATE INDEX IF NOT EXISTS idx_embeddings_external_id
    ON embeddings(external_id);

-- Index for filtering by type
CREATE INDEX IF NOT EXISTS idx_embeddings_type
    ON embeddings(type);

-- Composite index for common query patterns
CREATE INDEX IF NOT EXISTS idx_embeddings_type_attribute
    ON embeddings(type, attribute);

-- HNSW index for approximate nearest neighbor search
CREATE INDEX IF NOT EXISTS idx_embeddings_vector
    ON embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Unique constraint to prevent duplicate entries
CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_unique
    ON embeddings(external_id, attribute);
