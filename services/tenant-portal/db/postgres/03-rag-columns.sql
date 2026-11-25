-- RAG columns for document_chunks. Maintained by hand (not part of init.sql).
-- Idempotent: safe to run again.
ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT 'BAAI/bge-m3',
    ADD COLUMN IF NOT EXISTS content_hash    TEXT,
    ADD COLUMN IF NOT EXISTS content_tsv     tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED;

CREATE INDEX IF NOT EXISTS document_chunks_tsv
    ON document_chunks USING gin (content_tsv);