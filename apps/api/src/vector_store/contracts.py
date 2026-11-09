"""Backend-independent vector errors and stable chunk identities."""

import uuid

# Preserve existing chunk IDs across the pgvector-only runtime cutover.
POINT_NAMESPACE = uuid.UUID("7b6f3f0e-54d9-4f71-a477-0d5885f8cfbb")
QDRANT_POINT_NAMESPACE = POINT_NAMESPACE  # Historical import compatibility only.


class VectorStoreError(Exception):
    """A vector operation could not be completed."""


QdrantVectorStoreError = VectorStoreError  # Archived adapter/test compatibility.


def generate_point_id(document_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, f"{document_id}:{chunk_index}"))
