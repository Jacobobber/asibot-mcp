"""ChromaDB vector store wrapper. Singleton collection, local persistent storage."""

import logging

import chromadb

from asibot.config import settings

logger = logging.getLogger(__name__)

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None


def get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        settings.ensure_dirs()
        _client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        logger.info("ChromaDB initialized at %s", settings.chroma_dir)
    return _client


def get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        client = get_client()
        _collection = client.get_or_create_collection(
            name=settings.chroma_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "Collection '%s' ready (%d documents)",
            settings.chroma_collection_name,
            _collection.count(),
        )
    return _collection
