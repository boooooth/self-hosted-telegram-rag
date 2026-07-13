-- Telegram RAG bot schema.
-- Applied automatically on first Postgres container start via
-- docker-entrypoint-initdb.d (see docker-compose.yml).

CREATE TABLE IF NOT EXISTS users (
    telegram_user_id BIGINT PRIMARY KEY,
    username         TEXT,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_admin         BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS documents (
    id            SERIAL PRIMARY KEY,
    filename      TEXT NOT NULL,
    storage_path  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'ready', 'failed')),
    uploaded_by   BIGINT NOT NULL REFERENCES users (telegram_user_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id            SERIAL PRIMARY KEY,
    document_id   INTEGER NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    content_tsv   tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_content_tsv_idx ON chunks USING GIN (content_tsv);

CREATE TABLE IF NOT EXISTS queries (
    id                   SERIAL PRIMARY KEY,
    user_id              BIGINT NOT NULL REFERENCES users (telegram_user_id),
    question             TEXT NOT NULL,
    retrieved_chunk_ids  TEXT[] NOT NULL DEFAULT '{}',
    answer               TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
