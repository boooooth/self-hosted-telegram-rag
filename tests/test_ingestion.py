"""Tests for app.ingestion's pure chunking logic and extract_text's format
dispatch / failure handling.

Not covered here (would need real infra or fixture PDF/DOCX files, out of
scope for this pass): _extract_pdf_text, _extract_docx_text themselves, and
process_document end-to-end (needs Postgres/Qdrant/RQ).
"""
from app.ingestion import MIN_EXTRACTED_CHARS, chunk_text, extract_text


class TestChunkText:
    def test_empty_text_produces_no_chunks(self):
        assert chunk_text("", size=100, overlap=20) == []

    def test_short_text_produces_single_chunk(self):
        assert chunk_text("hello world", size=100, overlap=20) == ["hello world"]

    def test_covers_entire_text(self):
        text = "the quick brown fox jumps over the lazy dog " * 30
        chunks = chunk_text(text, size=200, overlap=40)
        assert set(text.split()) <= set(" ".join(chunks).split())

    def test_produces_multiple_chunks_for_long_text(self):
        text = "word " * 1000
        chunks = chunk_text(text, size=200, overlap=40)
        assert len(chunks) > 1

    def test_no_chunk_exceeds_requested_size(self):
        text = "the quick brown fox jumps over the lazy dog " * 30
        chunks = chunk_text(text, size=200, overlap=40)
        assert all(len(c) <= 200 for c in chunks)

    def test_avoids_splitting_words_when_a_space_is_available(self):
        text = "the quick brown fox jumps over the lazy dog " * 30
        chunks = chunk_text(text, size=100, overlap=20)
        source_words = set(text.split())
        for chunk in chunks:
            for token in chunk.split():
                assert token in source_words, f"chunk contains a word fragment: {token!r}"

    def test_falls_back_to_raw_cut_when_no_whitespace_in_window(self):
        # One long unbroken "word" -- no space to snap to. Must still
        # terminate and produce size-bounded chunks rather than hang.
        text = "a" * 500
        chunks = chunk_text(text, size=100, overlap=20)
        assert chunks
        assert all(set(c) == {"a"} for c in chunks)
        assert all(len(c) <= 100 for c in chunks)


class TestExtractText:
    def test_unsupported_extension_returns_none(self, tmp_path):
        path = tmp_path / "file.xyz"
        path.write_text("hello, this content is long enough to pass the min-chars check")
        assert extract_text(str(path)) is None

    def test_plain_text_below_min_chars_returns_none(self, tmp_path):
        path = tmp_path / "file.txt"
        path.write_text("short")
        assert len("short") < MIN_EXTRACTED_CHARS
        assert extract_text(str(path)) is None

    def test_plain_text_extraction_returns_content(self, tmp_path):
        path = tmp_path / "file.txt"
        content = "a" * (MIN_EXTRACTED_CHARS + 10)
        path.write_text(content)
        assert extract_text(str(path)) == content

    def test_markdown_extension_is_treated_as_plain_text(self, tmp_path):
        path = tmp_path / "file.md"
        content = "# Heading\n\n" + "content " * MIN_EXTRACTED_CHARS
        path.write_text(content)
        assert extract_text(str(path)) == content

    def test_missing_file_returns_none_not_raises(self, tmp_path):
        path = tmp_path / "does_not_exist.txt"
        assert extract_text(str(path)) is None

    def test_extraction_exception_is_caught_and_returns_none(self, tmp_path, monkeypatch):
        import app.ingestion as ingestion_module

        def _boom(path):
            raise OSError("simulated extraction failure")

        monkeypatch.setattr(ingestion_module, "_extract_plain_text", _boom)
        path = tmp_path / "file.txt"
        path.write_text("doesn't matter, extraction is mocked to fail")
        assert ingestion_module.extract_text(str(path)) is None
