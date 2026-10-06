"""Use Qdrant's actual local engine to exercise mutations and mandatory filters."""
from qdrant_client import AsyncQdrantClient, models
import pytest

from src.processing.models.document import EmbeddedChunk, EmbeddedDocument
from src.vector_store.qdrant_store import QdrantVectorStore


def document(contents):
    return EmbeddedDocument(chunks=[EmbeddedChunk(chunk_index=i, content=text, token_count=2, start_page=1, end_page=1, page_numbers=[1], block_types=["text"], embedding=[1.0, 0.0, 0.0]) for i, text in enumerate(contents)])


@pytest.mark.asyncio
async def test_colliding_external_ids_reindex_and_delete_are_isolated():
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantVectorStore(client=client, vector_dimension=3, collection_name="synthetic")
    scopes = [
        {"client_id": "quick_clinic", "tenant_id": "same_tenant", "collection_id": "same_collection", "owner_subject_id": "synthetic-a"},
        {"client_id": "generic_test_client", "tenant_id": "same_tenant", "collection_id": "same_collection", "owner_subject_id": "synthetic-a"},
        {"client_id": "quick_clinic", "tenant_id": "other_tenant", "collection_id": "same_collection", "owner_subject_id": "synthetic-a"},
    ]
    try:
        for scope in scopes:
            await store.index_document("same_external_id", document(["old one", "old two"]), extra_payload=scope)
        assert (await client.count("synthetic", exact=True)).count == 6
        await store.index_document("same_external_id", document(["changed one"]), extra_payload=scopes[0])
        assert (await client.count("synthetic", exact=True)).count == 5
        # Retrying has no duplicate points; stale second chunk was removed.
        await store.index_document("same_external_id", document(["changed one"]), extra_payload=scopes[0])
        assert (await client.count("synthetic", exact=True)).count == 5
        result = await client.query_points("synthetic", query=[1.0, 0.0, 0.0], query_filter=models.Filter(must=[models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in scopes[0].items()]))
        assert len(result.points) == 1
        assert result.points[0].payload["content"] == "changed one"
        # A mismatched patient scope finds no chunks before reranking.
        foreign = await client.query_points("synthetic", query=[1.0, 0.0, 0.0], query_filter=models.Filter(must=[models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in {**scopes[0], "owner_subject_id": "synthetic-b"}.items()]))
        assert foreign.points == []
        await store.delete_document_points("same_external_id", filters=scopes[0])
        assert (await client.count("synthetic", exact=True)).count == 4
        assert all(p.payload["client_id"] != "quick_clinic" or p.payload["tenant_id"] != "same_tenant" for p in (await client.scroll("synthetic", limit=20))[0])
    finally:
        await client.close()


def test_api_and_worker_vector_store_copies_match():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert (root / "api/src/vector_store/qdrant_store.py").read_bytes() == (root / "worker/src/vector_store/qdrant_store.py").read_bytes()
