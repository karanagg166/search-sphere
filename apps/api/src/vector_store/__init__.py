"""PostgreSQL/pgvector runtime with no Qdrant client import or fallback."""

from typing import Any

from src.config import settings
from src.vector_store.contracts import (
    QDRANT_POINT_NAMESPACE,
    QdrantVectorStoreError,
    VectorStoreError,
    generate_point_id,
)
from src.vector_store.supabase_vector_store import (
    SupabaseVectorStore,
    SupabaseVectorStoreError,
)


def get_vector_store(**kwargs: Any) -> SupabaseVectorStore:
    backend = settings.VECTOR_STORE_BACKEND.lower()
    if backend not in ("supabase", "pgvector", "postgres"):
        raise ValueError("Search Sphere requires PostgreSQL/pgvector vector storage.")
    return SupabaseVectorStore(**kwargs)


def __getattr__(name):
    # Explicit archived imports only; production never selects this backend.
    if name == "QdrantVectorStore":
        from src.vector_store.qdrant_store import QdrantVectorStore
        return QdrantVectorStore
    raise AttributeError(name)


__all__ = [
    "QDRANT_POINT_NAMESPACE", "QdrantVectorStoreError", "VectorStoreError",
    "generate_point_id", "SupabaseVectorStore", "SupabaseVectorStoreError",
    "get_vector_store",
]
