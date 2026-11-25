CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_chunks (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID        NOT NULL,
    document_id     UUID        NOT NULL,
    document_title  TEXT,
    section         TEXT,
    chunk_index     INT         NOT NULL,
    content         TEXT        NOT NULL,
    codes           TEXT[]      NOT NULL DEFAULT '{}',
    embedding       vector(1024) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS document_chunks_tenant ON document_chunks (tenant_id);
CREATE INDEX IF NOT EXISTS document_chunks_codes ON document_chunks USING gin (codes);