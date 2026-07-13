"""Worker-side ingestion: text extraction, chunking, and the RQ job body."""
import logging
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app import db, qdrant_store, telegram_api
from app.config import settings
from app.models import get_embedder

logger = logging.getLogger(__name__)

MIN_EXTRACTED_CHARS = 50  # below this, treat as "no usable text" (e.g. scanned/image PDF)

_PLAIN_TEXT_EXTENSIONS = {".txt", ".md"}


def _extract_pdf_text(path: str) -> str:
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx_text(path: str) -> str:
    return "\n".join(paragraph.text for paragraph in Document(path).paragraphs)


def _extract_plain_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def extract_text(path: str) -> str | None:
    """PDF (text-based only, no OCR), DOCX, TXT, and MD. Returns None if the
    format is unsupported, extraction fails, or the result yields too little
    text to be useful (e.g. a scanned/image PDF)."""
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".pdf":
            text = _extract_pdf_text(path)
        elif suffix == ".docx":
            text = _extract_docx_text(path)
        elif suffix in _PLAIN_TEXT_EXTENSIONS:
            text = _extract_plain_text(path)
        else:
            logger.warning("Unsupported file extension %s for %s", suffix, path)
            return None
    except Exception:
        logger.exception("Failed to extract text from %s", path)
        return None
    if len(text.strip()) < MIN_EXTRACTED_CHARS:
        return None
    return text


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Sliding window over raw characters, sized/stepped by `size`/`overlap`.
    Not a token count -- for typical English text ~size characters is well
    under the embedding/cross-encoder models' token limits at the current
    defaults, but a much larger CHUNK_SIZE could still exceed them (silently
    truncated by sentence-transformers, not an error here)."""
    chunks = []
    start = 0
    text_len = len(text)
    step = size - overlap
    while start < text_len:
        end = min(start + size, text_len)
        # Avoid splitting a word in half: if this isn't the last chunk and
        # the cut point isn't already on whitespace, back off to the nearest
        # preceding space in this window. Falls back to the raw cut if no
        # space is found (e.g. one long unbroken token).
        if end < text_len and not text[end].isspace():
            last_space = text.rfind(" ", start, end)
            if last_space > start:
                end = last_space
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def _mark_failed(doc: dict, reason: str) -> None:
    db.set_document_status(doc["id"], "failed")
    telegram_api.send_message(doc["uploaded_by"], f"Couldn't index \"{doc['filename']}\": {reason}")


def process_document(document_id: int) -> None:
    """RQ job body. Enqueued by the bot right after an admin upload, with a
    retry policy (see app.bot) — safe to re-run from scratch on failure since
    both the Qdrant and Postgres writes below are idempotent (deterministic
    point IDs / ON CONFLICT upsert), so a retry just finishes the job rather
    than duplicating anything."""
    doc = db.get_document(document_id)
    if doc is None:
        logger.error("process_document called for unknown document_id=%s", document_id)
        return

    text = extract_text(doc["storage_path"])
    if text is None:
        # Deterministic failure (bad format / no extractable text) — retrying
        # would produce the same result, so fail immediately instead of raising.
        _mark_failed(
            doc,
            "no extractable text found (supported formats: PDF, DOCX, TXT, MD — "
            "scanned/image PDFs aren't supported).",
        )
        return

    try:
        chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
        embeddings = get_embedder().encode(chunks, convert_to_numpy=True).tolist()

        qdrant_store.upsert_chunks(document_id, embeddings)
        db.insert_chunks(document_id, chunks)
        db.set_document_status(document_id, "ready")
    except Exception:
        logger.exception("process_document failed for document_id=%s", document_id)
        raise  # re-raise so RQ's retry policy (see app.bot) can retry or give up

    try:
        telegram_api.send_message(
            doc["uploaded_by"],
            f"\"{doc['filename']}\" indexed — {len(chunks)} chunks, ready to query.",
        )
    except Exception:
        # Indexing above already succeeded and is durably committed — a failure
        # to *notify* (e.g. the user blocked the bot) must not trigger a retry
        # of the whole job, which would just redo the expensive embedding pass
        # for no benefit. Best-effort only.
        logger.exception(
            "Failed to notify user %s that document_id=%s finished indexing",
            doc["uploaded_by"],
            document_id,
        )


def notify_final_failure(job, connection, type, value, traceback) -> None:
    """RQ on_failure callback. RQ calls this on EVERY failed attempt, not
    just the last one — so this must check job.retries_left itself and only
    act once RQ has actually given up (no more retries pending). Once it
    does act, this reliably reports a permanent failure and cleans up any
    partial Qdrant write from an earlier attempt (Qdrant has no
    document-status concept of its own, so a failed document must not leave
    orphaned, content-less vectors searchable)."""
    if job.retries_left:
        return  # RQ will retry this job again — don't clean up or notify yet

    document_id = job.args[0]
    try:
        qdrant_store.delete_document(document_id)
    except Exception:
        # Cleanup is best-effort — if Qdrant is still unreachable (the same
        # reason we're giving up), don't let that also block marking the
        # document failed and notifying the admin below.
        logger.exception("Failed to clean up Qdrant points for document_id=%s", document_id)

    doc = db.get_document(document_id)
    if doc is None:
        return
    _mark_failed(doc, "indexing kept failing after several retries — check the logs.")
