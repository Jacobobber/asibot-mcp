"""Document ingestion: parse files, chunk text, store in ChromaDB."""

import hashlib
import logging
from pathlib import Path

from asibot.config import settings
from asibot.rag.store import get_collection

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
}


def ingest_file(file_path: str) -> dict:
    """Ingest a single file into the vector store.

    Returns dict with: file_path, chunks_added, status, error (if any).
    """
    path = Path(file_path).expanduser().resolve()

    if not path.exists():
        return {"file_path": str(path), "chunks_added": 0, "status": "error", "error": f"File not found: {path}"}

    if not path.is_file():
        return {"file_path": str(path), "chunks_added": 0, "status": "error", "error": f"Not a file: {path}"}

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return {
            "file_path": str(path),
            "chunks_added": 0,
            "status": "error",
            "error": f"Unsupported file type: {ext}. Supported: {', '.join(SUPPORTED_EXTENSIONS.keys())}",
        }

    try:
        text = _extract_text(path)
        if not text.strip():
            return {"file_path": str(path), "chunks_added": 0, "status": "skipped", "error": "No text content"}

        chunks = _chunk_text(text)
        _store_chunks(path, chunks)

        return {"file_path": str(path), "chunks_added": len(chunks), "status": "success"}
    except Exception as e:
        logger.exception("Failed to ingest %s", path)
        return {"file_path": str(path), "chunks_added": 0, "status": "error", "error": str(e)}


def ingest_directory(directory_path: str, pattern: str = "**/*") -> dict:
    """Ingest all supported files in a directory."""
    dir_path = Path(directory_path).expanduser().resolve()

    if not dir_path.exists():
        return {"directory": str(dir_path), "error": f"Directory not found: {dir_path}"}
    if not dir_path.is_dir():
        return {"directory": str(dir_path), "error": f"Not a directory: {dir_path}"}

    results = []
    for file_path in sorted(dir_path.glob(pattern)):
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            result = ingest_file(str(file_path))
            results.append(result)

    files_processed = sum(1 for r in results if r["status"] == "success")
    total_chunks = sum(r["chunks_added"] for r in results)
    errors = [r for r in results if r["status"] == "error"]

    return {
        "directory": str(dir_path),
        "pattern": pattern,
        "files_processed": files_processed,
        "files_skipped": len(results) - files_processed - len(errors),
        "total_chunks": total_chunks,
        "errors": errors,
        "results": results,
    }


def ingest_text(text: str, source: str, source_name: str) -> dict:
    """Ingest raw text directly (used by connectors)."""
    if not text.strip():
        return {"source": source, "chunks_added": 0, "status": "skipped", "error": "No text content"}

    try:
        chunks = _chunk_text(text)
        _store_chunks_raw(source, source_name, chunks)
        return {"source": source, "chunks_added": len(chunks), "status": "success"}
    except Exception as e:
        logger.exception("Failed to ingest text from %s", source)
        return {"source": source, "chunks_added": 0, "status": "error", "error": str(e)}


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(path)
    elif ext == ".docx":
        return _extract_docx(path)
    else:
        return path.read_text(encoding="utf-8", errors="replace")


def _extract_pdf(path: Path) -> str:
    import pymupdf

    doc = pymupdf.open(str(path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages)


def _extract_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n".join(para.text for para in doc.paragraphs)


def _chunk_text(text: str) -> list[str]:
    chunk_size = settings.chunk_size
    overlap = settings.chunk_overlap

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            search_start = start + int(chunk_size * 0.8)
            break_pos = text.rfind("\n\n", search_start, end)
            if break_pos != -1:
                end = break_pos + 2

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = max(start + 1, end - overlap)

    return chunks


def _store_chunks(source_path: Path, chunks: list[str]) -> None:
    _store_chunks_raw(str(source_path), source_path.name, chunks)


def _store_chunks_raw(source: str, source_name: str, chunks: list[str]) -> None:
    collection = get_collection()

    existing = collection.get(where={"source": source})
    if existing["ids"]:
        collection.delete(ids=existing["ids"])
        logger.info("Deleted %d existing chunks for %s", len(existing["ids"]), source)

    ids = []
    documents = []
    metadatas = []

    for i, chunk in enumerate(chunks):
        chunk_id = hashlib.sha256(f"{source}::{i}::{chunk[:100]}".encode()).hexdigest()[:16]
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({
            "source": source,
            "source_name": source_name,
            "chunk_index": i,
            "total_chunks": len(chunks),
        })

    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    logger.info("Stored %d chunks for %s", len(chunks), source)
