"""Tests for the ingest pipeline."""

import tempfile

from asibot.rag.ingest import _chunk_text, ingest_file


def test_chunk_text_short():
    chunks = _chunk_text("Hello world")
    assert len(chunks) == 1
    assert chunks[0] == "Hello world"


def test_chunk_text_with_overlap():
    text = "A" * 2000
    chunks = _chunk_text(text)
    assert len(chunks) > 1


def test_chunk_text_paragraph_break():
    text = "A" * 800 + "\n\n" + "B" * 800
    chunks = _chunk_text(text)
    assert len(chunks) >= 2


def test_ingest_file_not_found():
    result = ingest_file("/nonexistent/file.txt")
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


def test_ingest_file_unsupported_type():
    with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
        f.write(b"test")
        f.flush()
        result = ingest_file(f.name)
    assert result["status"] == "error"
    assert "unsupported" in result["error"].lower()


def test_ingest_text_file():
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
        f.write("This is a test document with some content for testing purposes.")
        f.flush()
        result = ingest_file(f.name)
    assert result["status"] == "success"
    assert result["chunks_added"] >= 1


def test_ingest_empty_file():
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
        f.write("")
        f.flush()
        result = ingest_file(f.name)
    assert result["status"] == "skipped"
