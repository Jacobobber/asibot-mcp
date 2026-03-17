"""Semantic search over the document store."""

import logging

from asibot.config import settings
from asibot.rag.store import get_collection

logger = logging.getLogger(__name__)


def search_documents(query: str, top_k: int | None = None) -> list[dict]:
    """Search for relevant document chunks."""
    k = top_k or settings.default_top_k
    collection = get_collection()

    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for i in range(len(results["ids"][0])):
        distance = results["distances"][0][i] if results["distances"] else 0
        score = 1.0 - distance

        hits.append({
            "text": results["documents"][0][i],
            "source": results["metadatas"][0][i].get("source", "unknown"),
            "source_name": results["metadatas"][0][i].get("source_name", "unknown"),
            "chunk_index": results["metadatas"][0][i].get("chunk_index", 0),
            "total_chunks": results["metadatas"][0][i].get("total_chunks", 1),
            "score": round(score, 4),
        })

    return hits


def list_sources() -> list[dict]:
    """List all ingested sources with chunk counts."""
    collection = get_collection()

    if collection.count() == 0:
        return []

    all_data = collection.get(include=["metadatas"])

    source_counts: dict[str, dict] = {}
    for meta in all_data["metadatas"]:
        source = meta.get("source", "unknown")
        if source not in source_counts:
            source_counts[source] = {
                "source": source,
                "source_name": meta.get("source_name", "unknown"),
                "chunk_count": 0,
            }
        source_counts[source]["chunk_count"] += 1

    return sorted(source_counts.values(), key=lambda x: x["source"])
