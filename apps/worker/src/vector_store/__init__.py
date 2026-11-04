from typing import Any

from src.config import settings
from src.vector_store.qdrant_store import (
    QDRANT_POINT_NAMESPACE,
    QdrantVectorStore,
    QdrantVectorStoreError,
    VectorStoreError,
    generate_point_id,
)
from src.vector_store.supabase_vector_store import (
    SupabaseVectorStore,
    SupabaseVectorStoreError,
)


def get_vector_store(**kwargs: Any) -> Any:
    """
    Factory returning the configured vector store backend.
    Default: SupabaseVectorStore (pgvector) for zero-downtime durability on $0 budget.
    """
    backend = getattr(settings, "VECTOR_STORE_BACKEND", "supabase").lower()
    if backend in ("supabase", "pgvector", "postgres"):
        return SupabaseVectorStore(**kwargs)
    return QdrantVectorStore(**kwargs)


__all__ = [
    "QDRANT_POINT_NAMESPACE",
    "QdrantVectorStore",
    "QdrantVectorStoreError",
    "SupabaseVectorStore",
    "SupabaseVectorStoreError",
    "VectorStoreError",
    "generate_point_id",
    "get_vector_store",
]
