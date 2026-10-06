import pytest
from qdrant_client import AsyncQdrantClient
from src.processing.models.document import EmbeddedChunk, EmbeddedDocument
from src.vector_store.qdrant_store import QdrantVectorStore


def document(contents):
    return EmbeddedDocument(chunks=[EmbeddedChunk(chunk_index=i, content=text, token_count=2, start_page=1, end_page=1, page_numbers=[1], block_types=["text"], embedding=[1.0, 0.0, 0.0]) for i, text in enumerate(contents)])


@pytest.mark.asyncio
async def test_hybrid_result_preserves_scope_through_reranking():
    from unittest.mock import MagicMock
    from src.processing.models.sparse_vector import SparseVector
    from src.retrieval.reranker import CrossEncoderReranker

    client = AsyncQdrantClient(":memory:")
    store = QdrantVectorStore(client=client, collection_name="hybrid_scope", vector_dimension=3)
    scope = {
        "client_id": "quick_clinic", "tenant_id": "quick_clinic_default",
        "collection_id": "synthetic_patient_records", "owner_subject_id": "synthetic-patient-a",
        "source_system": "quick_clinic", "patient_id": "synthetic-patient-a",
    }
    try:
        await store.index_document("synthetic-document-a", document(["synthetic record"]), extra_payload=scope)
        results = await store.search_hybrid(
            dense_query_vector=[1.0, 0.0, 0.0],
            sparse_query_vector=SparseVector(indices=[], values=[]),
            limit=5, candidate_limit=10, filters=scope,
        )
        assert len(results) == 1
        assert {key: getattr(results[0], key) for key in scope} == scope
        model = MagicMock(spec=["predict"])
        model.predict.return_value = [0.9]
        reranked = CrossEncoderReranker(model=model).rerank("synthetic query", results)
        assert {key: getattr(reranked[0], key) for key in scope} == scope
        assert reranked[0].document_id == "synthetic-document-a"
    finally:
        await client.close()
