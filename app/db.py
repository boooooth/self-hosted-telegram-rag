"""Thin Postgres access layer. Plain SQL, no ORM."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings.database_url, min_size=1, max_size=10, open=True)
    return _pool


@contextmanager
def _cursor():
    with get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur


def upsert_user(telegram_user_id: int, username: str | None, is_admin: bool) -> None:
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (telegram_user_id, username, is_admin, first_seen_at, last_seen_at)
            VALUES (%s, %s, %s, now(), now())
            ON CONFLICT (telegram_user_id) DO UPDATE
                SET username = EXCLUDED.username,
                    is_admin = EXCLUDED.is_admin,
                    last_seen_at = now()
            """,
            (telegram_user_id, username, is_admin),
        )


def insert_document(filename: str, storage_path: str, uploaded_by: int) -> int:
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (filename, storage_path, status, uploaded_by)
            VALUES (%s, %s, 'pending', %s)
            RETURNING id
            """,
            (filename, storage_path, uploaded_by),
        )
        return cur.fetchone()["id"]


def get_document(document_id: int) -> dict | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM documents WHERE id = %s", (document_id,))
        return cur.fetchone()


def set_document_status(document_id: int, status: str) -> None:
    with _cursor() as cur:
        cur.execute("UPDATE documents SET status = %s WHERE id = %s", (status, document_id))


def insert_chunks(document_id: int, chunk_texts: list[str]) -> None:
    """Upserts the current chunk set, then deletes any leftover rows from a
    previous ingestion attempt that produced more chunks than this one (e.g.
    CHUNK_SIZE changed between runs) -- otherwise those higher-indexed rows
    stay orphaned and searchable forever. Both statements run in the same
    connection/transaction, so this is all-or-nothing."""
    with _cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks (document_id, chunk_index, content)
            VALUES (%s, %s, %s)
            ON CONFLICT (document_id, chunk_index) DO UPDATE SET content = EXCLUDED.content
            """,
            [(document_id, idx, text) for idx, text in enumerate(chunk_texts)],
        )
        cur.execute(
            "DELETE FROM chunks WHERE document_id = %s AND chunk_index >= %s",
            (document_id, len(chunk_texts)),
        )


def search_chunks_fts(query: str, limit: int) -> list[dict]:
    """Lexical half of hybrid retrieval. Returns chunk_id/document_id/content/rank."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT
                document_id || ':' || chunk_index AS chunk_id,
                document_id,
                content,
                ts_rank(content_tsv, query) AS rank
            FROM chunks, plainto_tsquery('english', %s) AS query
            WHERE content_tsv @@ query
            ORDER BY rank DESC
            LIMIT %s
            """,
            (query, limit),
        )
        return cur.fetchall()


def get_chunks_by_ids(chunk_ids: list[str]) -> dict[str, dict]:
    """Hydrate dense-retrieval hits (which only carry chunk_id/document_id) with content."""
    if not chunk_ids:
        return {}
    doc_ids: list[int] = []
    chunk_indexes: list[int] = []
    for chunk_id in chunk_ids:
        document_id_str, chunk_index_str = chunk_id.split(":")
        doc_ids.append(int(document_id_str))
        chunk_indexes.append(int(chunk_index_str))
    with _cursor() as cur:
        cur.execute(
            """
            SELECT c.document_id || ':' || c.chunk_index AS chunk_id, c.document_id, c.content
            FROM chunks c
            JOIN unnest(%s::int[], %s::int[]) AS t(document_id, chunk_index)
                ON c.document_id = t.document_id AND c.chunk_index = t.chunk_index
            """,
            (doc_ids, chunk_indexes),
        )
        return {row["chunk_id"]: row for row in cur.fetchall()}


def log_query(user_id: int, question: str, retrieved_chunk_ids: list[str], answer: str) -> None:
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO queries (user_id, question, retrieved_chunk_ids, answer)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, question, retrieved_chunk_ids, answer),
        )
