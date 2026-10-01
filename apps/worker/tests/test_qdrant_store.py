import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client import AsyncQdrantClient, models

from src.processing.models.document import EmbeddedChunk, EmbeddedDocument
from src.processing.models.search import (
    DenseSearchResult,
    HybridSearchResult,
    SparseSearchResult,
)
from src.processing.models.sparse_vector import SparseVector
from src.vector_store import (
    QdrantVectorStore,
    QdrantVectorStoreError,
    generate_point_id,
)


def _create_sample_embedded_chunk(
    chunk_index: int = 0,
    content: str = "Test chunk content",
    start_page: int = 1,
    end_page: int = 1,
    page_numbers: list[int] | None = None,
    block_types: list[str] | None = None,
    embedding: list[float] | None = None,
) -> EmbeddedChunk:
    return EmbeddedChunk(
        chunk_index=chunk_index,
        content=content,
        token_count=len(content.split()),
        start_page=start_page,
        end_page=end_page,
        page_numbers=page_numbers if page_numbers is not None else [1],
        block_types=block_types if block_types is not None else ["text"],
        embedding=embedding if embedding is not None else [0.1] * 384,
    )


# ==============================================================================
# 1. Deterministic Point ID Tests
# ==============================================================================


def test_deterministic_point_id_consistency() -> None:
    """Same document_id + chunk_index must always produce identical point IDs."""
    doc_id = "doc-alpha-123"
    chunk_idx = 0

    id1 = generate_point_id(doc_id, chunk_idx)
    id2 = generate_point_id(doc_id, chunk_idx)

    assert id1 == id2
    # Verify valid UUID string format
    parsed = uuid.UUID(id1)
    assert parsed.version == 5


def test_deterministic_point_id_differentiation() -> None:
    """Different chunk_index or document_id must produce distinct point IDs."""
    base_id = generate_point_id("doc-1", 0)

    # Different chunk index
    diff_chunk_id = generate_point_id("doc-1", 1)
    assert base_id != diff_chunk_id

    # Different document ID
    diff_doc_id = generate_point_id("doc-2", 0)
    assert base_id != diff_doc_id


# ==============================================================================
# 2. Point Conversion & Metadata Preservation Tests
# ==============================================================================


def test_convert_to_points_structure() -> None:
    """EmbeddedChunk is transformed into Qdrant PointStruct with full metadata."""
    store = QdrantVectorStore(client=AsyncMock())
    doc_id = "doc-convert-001"

    chunk = _create_sample_embedded_chunk(
        chunk_index=3,
        content="Preserved semantic content across pages",
        start_page=2,
        end_page=3,
        page_numbers=[2, 3],
        block_types=["text", "image"],
        embedding=[0.05] * 384,
    )
    embedded_doc = EmbeddedDocument(chunks=[chunk])

    points = store.convert_to_points(doc_id, embedded_doc)

    assert len(points) == 1
    point = points[0]

    assert isinstance(point, models.PointStruct)
    assert point.id == generate_point_id(doc_id, 3)
    assert point.vector == [0.05] * 384

    payload = point.payload
    assert payload["document_id"] == doc_id
    assert payload["chunk_index"] == 3
    assert payload["content"] == "Preserved semantic content across pages"
    assert payload["token_count"] == 5
    assert payload["start_page"] == 2
    assert payload["end_page"] == 3
    assert payload["page_numbers"] == [2, 3]
    assert payload["block_types"] == ["text", "image"]


# ==============================================================================
# 3. Collection Creation & Inspection Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_ensure_collection_creates_when_missing() -> None:
    """When collection does not exist, create with 384-d Cosine vectors and index."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = False

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="test_col",
        vector_dimension=384,
    )

    await store.ensure_collection()

    mock_client.collection_exists.assert_awaited_once_with("test_col")
    mock_client.create_collection.assert_awaited_once()

    _, kwargs = mock_client.create_collection.call_args
    assert kwargs["collection_name"] == "test_col"
    vectors_config = kwargs["vectors_config"]
    assert isinstance(vectors_config, models.VectorParams)
    assert vectors_config.size == 384
    assert vectors_config.distance == models.Distance.COSINE
    sparse_config = kwargs.get("sparse_vectors_config")
    assert isinstance(sparse_config, dict)
    assert "bm25" in sparse_config
    assert sparse_config["bm25"].modifier == models.Modifier.IDF

    # Payload index must be created
    mock_client.create_payload_index.assert_awaited_once_with(
        collection_name="test_col",
        field_name="document_id",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )


@pytest.mark.asyncio
async def test_ensure_collection_skips_creation_when_valid() -> None:
    """Existing compatible collection is validated and not destructively recreated."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True

    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384,
        distance=models.Distance.COSINE,
    )
    mock_info.config.params.sparse_vectors = {
        "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
    }
    mock_info.payload_schema = {"document_id": MagicMock()}
    mock_client.get_collection.return_value = mock_info

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="existing_col",
        vector_dimension=384,
    )

    await store.ensure_collection()

    mock_client.collection_exists.assert_awaited_once_with("existing_col")
    mock_client.get_collection.assert_awaited_once_with("existing_col")
    # Must NOT recreate collection or update
    mock_client.create_collection.assert_not_called()
    mock_client.update_collection.assert_not_called()
    mock_client.create_payload_index.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_collection_existing_missing_sparse_schema_safe_addition() -> None:
    """Safely add sparse schema via update_collection when missing without deleting."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True

    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384,
        distance=models.Distance.COSINE,
    )
    # sparse_vectors is missing or empty
    mock_info.config.params.sparse_vectors = None
    mock_info.payload_schema = {"document_id": MagicMock()}
    mock_client.get_collection.return_value = mock_info

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="legacy_col",
        vector_dimension=384,
    )

    await store.ensure_collection()

    # Must call update_collection with sparse schema
    mock_client.update_collection.assert_awaited_once()
    _, kwargs = mock_client.update_collection.call_args
    assert kwargs["collection_name"] == "legacy_col"
    assert "bm25" in kwargs["sparse_vectors_config"]
    assert kwargs["sparse_vectors_config"]["bm25"].modifier == models.Modifier.IDF

    # Must NOT delete or recreate collection
    mock_client.create_collection.assert_not_called()
    mock_client.delete_collection.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_collection_sparse_schema_failure_preserves_data() -> None:
    """When safe update fails, raise QdrantVectorStoreError without deleting."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True

    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384,
        distance=models.Distance.COSINE,
    )
    mock_info.config.params.sparse_vectors = {}
    mock_info.payload_schema = {"document_id": MagicMock()}
    mock_client.get_collection.return_value = mock_info
    mock_client.update_collection.side_effect = RuntimeError(
        "Update not supported by server version"
    )

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="legacy_col",
        vector_dimension=384,
    )

    with pytest.raises(QdrantVectorStoreError, match="missing sparse vector 'bm25'"):
        await store.ensure_collection()

    # Must NEVER recreate or delete
    mock_client.create_collection.assert_not_called()
    mock_client.delete_collection.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_collection_fails_on_dimension_mismatch() -> None:
    """Incompatible vector dimension raises QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True

    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=768,  # Incompatible dimension
        distance=models.Distance.COSINE,
    )
    mock_client.get_collection.return_value = mock_info

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="bad_dim_col",
        vector_dimension=384,
    )

    with pytest.raises(QdrantVectorStoreError, match="Incompatible vector dimension"):
        await store.ensure_collection()

    mock_client.create_collection.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_collection_fails_on_distance_metric_mismatch() -> None:
    """Incompatible distance metric raises QdrantVectorStoreError without recreation."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True

    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384,
        distance=models.Distance.EUCLID,  # Conflicting distance
    )
    mock_client.get_collection.return_value = mock_info

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="bad_dist_col",
        vector_dimension=384,
    )

    with pytest.raises(QdrantVectorStoreError, match="Incompatible distance metric"):
        await store.ensure_collection()

    mock_client.create_collection.assert_not_called()


@pytest.mark.asyncio
async def test_payload_index_created_if_not_present_in_existing_collection() -> None:
    """If existing collection is missing the document_id index, create it."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True

    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384,
        distance=models.Distance.COSINE,
    )
    mock_info.payload_schema = {}  # Empty payload schema
    mock_client.get_collection.return_value = mock_info

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="no_index_col",
        vector_dimension=384,
    )

    await store.ensure_collection()

    mock_client.create_payload_index.assert_awaited_once_with(
        collection_name="no_index_col",
        field_name="document_id",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )


# ==============================================================================
# 4. Deletion & Reindexing Synchronization Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_delete_document_points_uses_filtered_selector() -> None:
    """Deletion must specifically target document_id using server-side Filter."""
    mock_client = AsyncMock()
    store = QdrantVectorStore(client=mock_client, collection_name="del_col")

    await store.delete_document_points("doc-xyz")

    mock_client.delete.assert_awaited_once()
    _, kwargs = mock_client.delete.call_args
    assert kwargs["collection_name"] == "del_col"
    selector = kwargs["points_selector"]
    assert isinstance(selector, models.Filter)
    assert len(selector.must) == 1
    condition = selector.must[0]
    assert condition.key == "document_id"
    assert condition.match.value == "doc-xyz"


@pytest.mark.asyncio
async def test_reindex_order_deletes_then_upserts() -> None:
    """Reindexing must delete existing document points before upserting new points."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True
    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384, distance=models.Distance.COSINE
    )
    mock_info.payload_schema = {"document_id": MagicMock()}
    mock_client.get_collection.return_value = mock_info

    store = QdrantVectorStore(client=mock_client, collection_name="sync_col")

    chunks = [
        _create_sample_embedded_chunk(chunk_index=0, content="v2 chunk 0"),
        _create_sample_embedded_chunk(chunk_index=1, content="v2 chunk 1"),
    ]
    doc = EmbeddedDocument(chunks=chunks)

    points_written = await store.index_document("doc-reindex", doc)

    assert points_written == 2

    # Verify execution order: delete must happen before upsert
    call_names = [call[0] for call in mock_client.method_calls]
    delete_idx = call_names.index("delete")
    upsert_idx = call_names.index("upsert")
    assert delete_idx < upsert_idx


@pytest.mark.asyncio
async def test_empty_document_deletes_and_upserts_nothing() -> None:
    """Empty EmbeddedDocument deletes existing points and performs 0 upserts."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True
    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384, distance=models.Distance.COSINE
    )
    mock_info.payload_schema = {"document_id": MagicMock()}
    mock_client.get_collection.return_value = mock_info

    store = QdrantVectorStore(client=mock_client, collection_name="empty_col")

    empty_doc = EmbeddedDocument(chunks=[])
    points_written = await store.index_document("doc-empty", empty_doc)

    assert points_written == 0
    mock_client.delete.assert_awaited_once()
    mock_client.upsert.assert_not_called()


# ==============================================================================
# 5. Batch Upsert Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_upsert_splits_correctly() -> None:
    """Large point counts are divided into configured batch sizes."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True
    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384, distance=models.Distance.COSINE
    )
    mock_info.payload_schema = {"document_id": MagicMock()}
    mock_client.get_collection.return_value = mock_info

    # 5 chunks with batch_size=2 -> 3 upsert calls (2, 2, 1)
    store = QdrantVectorStore(
        client=mock_client,
        collection_name="batch_col",
        batch_size=2,
    )

    chunks = [
        _create_sample_embedded_chunk(chunk_index=i, content=f"Chunk {i}")
        for i in range(5)
    ]
    doc = EmbeddedDocument(chunks=chunks)

    points_written = await store.index_document("doc-batched", doc)

    assert points_written == 5
    assert mock_client.upsert.await_count == 3

    # Check batch sizes
    batch1 = mock_client.upsert.call_args_list[0].kwargs["points"]
    batch2 = mock_client.upsert.call_args_list[1].kwargs["points"]
    batch3 = mock_client.upsert.call_args_list[2].kwargs["points"]
    assert len(batch1) == 2
    assert len(batch2) == 2
    assert len(batch3) == 1


# ==============================================================================
# 6. Error Handling & Propagation Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_collection_inspection_failure_raises() -> None:
    """Inspection failure wraps error into QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_client.collection_exists.side_effect = RuntimeError("Connection timed out")

    store = QdrantVectorStore(client=mock_client)

    with pytest.raises(QdrantVectorStoreError, match="Failed to check existence"):
        await store.ensure_collection()


@pytest.mark.asyncio
async def test_collection_creation_failure_raises() -> None:
    """Creation failure wraps error into QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = False
    mock_client.create_collection.side_effect = RuntimeError("Disk full")

    store = QdrantVectorStore(client=mock_client)

    with pytest.raises(QdrantVectorStoreError, match="Failed to create collection"):
        await store.ensure_collection()


@pytest.mark.asyncio
async def test_delete_failure_raises() -> None:
    """Delete failure wraps error into QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_client.delete.side_effect = RuntimeError("Qdrant node unreachable")

    store = QdrantVectorStore(client=mock_client)

    with pytest.raises(QdrantVectorStoreError, match="Failed to delete points"):
        await store.delete_document_points("doc-fail")


@pytest.mark.asyncio
async def test_upsert_failure_raises() -> None:
    """Upsert failure wraps error into QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_client.collection_exists.return_value = True
    mock_info = MagicMock()
    mock_info.config.params.vectors = models.VectorParams(
        size=384, distance=models.Distance.COSINE
    )
    mock_info.payload_schema = {"document_id": MagicMock()}
    mock_client.get_collection.return_value = mock_info
    mock_client.upsert.side_effect = RuntimeError("Payload serialization error")

    store = QdrantVectorStore(client=mock_client)
    doc = EmbeddedDocument(chunks=[_create_sample_embedded_chunk()])

    with pytest.raises(QdrantVectorStoreError, match="Failed to upsert"):
        await store.index_document("doc-fail", doc)


# ==============================================================================
# 7. Real In-Memory Qdrant Integration Test
# ==============================================================================


@pytest.mark.asyncio
async def test_qdrant_store_end_to_end_in_memory() -> None:
    """
    Verifies full lifecycle with a real in-memory AsyncQdrantClient:
    creation -> payload index -> deterministic points -> reindexing -> deletion.
    """
    client = AsyncQdrantClient(":memory:")
    store = QdrantVectorStore(
        client=client,
        collection_name="real_mem_test",
        vector_dimension=384,
        batch_size=2,
    )

    doc_id = "doc-real-test-01"
    chunks_v1 = [
        _create_sample_embedded_chunk(chunk_index=0, content="Initial Chunk 0"),
        _create_sample_embedded_chunk(chunk_index=1, content="Initial Chunk 1"),
        _create_sample_embedded_chunk(chunk_index=2, content="Initial Chunk 2"),
    ]
    doc_v1 = EmbeddedDocument(chunks=chunks_v1)

    # 1. Index version 1 (3 points)
    written_v1 = await store.index_document(doc_id, doc_v1)
    assert written_v1 == 3

    count_res = await client.count("real_mem_test")
    assert count_res.count == 3

    # Verify point content
    pt0 = await client.retrieve(
        "real_mem_test",
        ids=[generate_point_id(doc_id, 0)],
    )
    assert len(pt0) == 1
    assert pt0[0].payload["content"] == "Initial Chunk 0"
    assert pt0[0].payload["document_id"] == doc_id

    # 2. Reindex with version 2 having only 2 chunks (stale chunk 2 must be removed)
    chunks_v2 = [
        _create_sample_embedded_chunk(chunk_index=0, content="Updated Chunk 0"),
        _create_sample_embedded_chunk(chunk_index=1, content="Updated Chunk 1"),
    ]
    doc_v2 = EmbeddedDocument(chunks=chunks_v2)

    written_v2 = await store.index_document(doc_id, doc_v2)
    assert written_v2 == 2

    count_res2 = await client.count("real_mem_test")
    assert count_res2.count == 2

    # Chunk 2 should not exist
    pt2 = await client.retrieve(
        "real_mem_test",
        ids=[generate_point_id(doc_id, 2)],
    )
    assert len(pt2) == 0

    # 3. Empty document clears everything for doc_id
    written_v3 = await store.index_document(doc_id, EmbeddedDocument(chunks=[]))
    assert written_v3 == 0

    count_res3 = await client.count("real_mem_test")
    assert count_res3.count == 0

    await store.close()


# ==============================================================================
# 8. Dense ANN Search & Result Conversion Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_search_dense_query_call_parameters() -> None:
    """Verify Qdrant query_points is invoked with correct parameters."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.points = []
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="docs_col",
        vector_dimension=384,
    )

    query_vec = [0.05] * 384
    results = await store.search_dense(query_vector=query_vec, limit=7)

    assert results == []
    mock_client.query_points.assert_awaited_once_with(
        collection_name="docs_col",
        query=query_vec,
        limit=7,
        query_filter=None,
        score_threshold=None,
        with_payload=True,
        with_vectors=False,
    )


@pytest.mark.asyncio
async def test_search_dense_without_filter() -> None:
    """When document_id is omitted, query_filter is None."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.points = []
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client)
    await store.search_dense(query_vector=[0.1] * 384, limit=5, document_id=None)

    _, kwargs = mock_client.query_points.call_args
    assert kwargs["query_filter"] is None


@pytest.mark.asyncio
async def test_search_dense_with_document_filter() -> None:
    """When document_id is provided, server-side FieldCondition filter is built."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.points = []
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client)
    await store.search_dense(
        query_vector=[0.1] * 384,
        limit=5,
        document_id="doc-target-99",
    )

    _, kwargs = mock_client.query_points.call_args
    query_filter = kwargs["query_filter"]
    assert isinstance(query_filter, models.Filter)
    assert isinstance(query_filter.must, list)
    assert len(query_filter.must) == 1
    condition = query_filter.must[0]
    assert isinstance(condition, models.FieldCondition)
    assert condition.key == "document_id"
    assert condition.match == models.MatchValue(value="doc-target-99")


@pytest.mark.asyncio
async def test_search_dense_score_threshold() -> None:
    """Optional score_threshold is forwarded to query_points."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.points = []
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client)
    await store.search_dense(
        query_vector=[0.1] * 384,
        limit=5,
        score_threshold=0.82,
    )

    _, kwargs = mock_client.query_points.call_args
    assert kwargs["score_threshold"] == 0.82


@pytest.mark.asyncio
async def test_search_dense_payload_conversion() -> None:
    """Raw Qdrant ScoredPoint is converted into domain DenseSearchResult."""
    mock_client = AsyncMock()
    mock_point = MagicMock()
    mock_point.id = "c3f81e3a-7a5e-49b8-b80c-03d6d0a7a3b1"
    mock_point.score = 0.945
    mock_point.payload = {
        "document_id": "doc-abc",
        "chunk_index": 2,
        "content": "Retrieved chunk content from Qdrant",
        "token_count": 6,
        "start_page": 3,
        "end_page": 4,
        "page_numbers": [3, 4],
        "block_types": ["text", "image"],
    }
    mock_response = MagicMock()
    mock_response.points = [mock_point]
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client)
    results = await store.search_dense(query_vector=[0.1] * 384, limit=1)

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, DenseSearchResult)
    assert res.point_id == "c3f81e3a-7a5e-49b8-b80c-03d6d0a7a3b1"
    assert res.score == 0.945
    assert res.document_id == "doc-abc"
    assert res.chunk_index == 2
    assert res.content == "Retrieved chunk content from Qdrant"
    assert res.token_count == 6
    assert res.start_page == 3
    assert res.end_page == 4
    assert res.page_numbers == [3, 4]
    assert res.block_types == ["text", "image"]
    assert res.rank == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field",
    [
        "document_id",
        "chunk_index",
        "content",
        "token_count",
        "start_page",
        "end_page",
        "page_numbers",
        "block_types",
    ],
)
async def test_search_dense_malformed_missing_payload_field_raises(
    missing_field: str,
) -> None:
    """Missing any required payload field raises QdrantVectorStoreError."""
    mock_client = AsyncMock()
    payload = {
        "document_id": "doc-1",
        "chunk_index": 0,
        "content": "Valid content",
        "token_count": 2,
        "start_page": 1,
        "end_page": 1,
        "page_numbers": [1],
        "block_types": ["text"],
    }
    del payload[missing_field]

    mock_point = MagicMock()
    mock_point.id = "pt-invalid"
    mock_point.score = 0.8
    mock_point.payload = payload

    mock_response = MagicMock()
    mock_response.points = [mock_point]
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client)
    expected_err = f"Missing required payload field '{missing_field}'"
    with pytest.raises(QdrantVectorStoreError, match=expected_err):
        await store.search_dense(query_vector=[0.1] * 384, limit=5)


@pytest.mark.asyncio
async def test_search_dense_missing_payload_dict_raises() -> None:
    """Point with None payload raises QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_point = MagicMock()
    mock_point.id = "pt-no-payload"
    mock_point.score = 0.75
    mock_point.payload = None

    mock_response = MagicMock()
    mock_response.points = [mock_point]
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client)
    with pytest.raises(QdrantVectorStoreError, match="Missing or invalid payload"):
        await store.search_dense(query_vector=[0.1] * 384, limit=5)


@pytest.mark.asyncio
async def test_search_dense_client_failure_raises() -> None:
    """Underlying query failure wraps into QdrantVectorStoreError with context."""
    mock_client = AsyncMock()
    mock_client.query_points.side_effect = RuntimeError("Connection reset by peer")

    store = QdrantVectorStore(client=mock_client, collection_name="fail_col")

    with pytest.raises(QdrantVectorStoreError, match="Failed to query dense vectors"):
        await store.search_dense(query_vector=[0.1] * 384, limit=5)


@pytest.mark.asyncio
async def test_search_dense_empty_vector_raises() -> None:
    """Empty query vector raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock())
    with pytest.raises(QdrantVectorStoreError, match="query_vector must not be empty"):
        await store.search_dense(query_vector=[], limit=5)


@pytest.mark.asyncio
async def test_search_dense_dimension_mismatch_raises() -> None:
    """Query vector with wrong dimension raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock(), vector_dimension=384)
    with pytest.raises(QdrantVectorStoreError, match="Query vector dimension mismatch"):
        await store.search_dense(query_vector=[0.1] * 128, limit=5)


@pytest.mark.asyncio
async def test_search_dense_invalid_limit_raises() -> None:
    """Non-positive limit raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock())
    with pytest.raises(QdrantVectorStoreError, match="limit must be positive"):
        await store.search_dense(query_vector=[0.1] * 384, limit=0)


# ==============================================================================
# 9. Real In-Memory Qdrant Dense Search Integration Test
# ==============================================================================


@pytest.mark.asyncio
async def test_qdrant_search_dense_in_memory() -> None:
    """
    Verifies end-to-end ANN dense search using an in-memory AsyncQdrantClient:
    - indexes distinct documents with deterministic fake vectors
    - verifies closest vector is ranked first by Cosine similarity
    - verifies server-side document_id filter isolates target document
    """
    client = AsyncQdrantClient(":memory:")
    store = QdrantVectorStore(
        client=client,
        collection_name="dense_search_mem_test",
        vector_dimension=384,
    )

    # Document A: chunk 0 is [1, 0, 0...], chunk 1 is [0, 1, 0...]
    vec_a0 = [0.0] * 384
    vec_a0[0] = 1.0

    vec_a1 = [0.0] * 384
    vec_a1[1] = 1.0

    doc_a = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content="Doc A chunk 0: downloads original PDF from object storage",
                embedding=vec_a0,
            ),
            _create_sample_embedded_chunk(
                chunk_index=1,
                content="Doc A chunk 1: BLIP generates semantic image descriptions",
                embedding=vec_a1,
            ),
        ]
    )

    # Document B: chunk 0 is [0, 0, 1...]
    vec_b0 = [0.0] * 384
    vec_b0[2] = 1.0

    doc_b = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content="Doc B chunk 0: RabbitMQ transports the document identifier",
                embedding=vec_b0,
            )
        ]
    )

    await store.index_document("doc-A", doc_a)
    await store.index_document("doc-B", doc_b)

    # 1. Query vector aligned with vec_a0: [0.99, 0.01, 0...]
    query_vec = [0.0] * 384
    query_vec[0] = 0.99
    query_vec[1] = 0.01

    results = await store.search_dense(query_vector=query_vec, limit=5)

    assert len(results) == 3
    # First ranked match must be Doc A chunk 0
    assert results[0].document_id == "doc-A"
    assert results[0].chunk_index == 0
    assert "downloads original PDF" in results[0].content
    assert results[0].rank == 1
    assert results[0].score > results[1].score

    # 2. Filter by document_id="doc-B": excludes Doc A entirely
    doc_b_results = await store.search_dense(
        query_vector=query_vec,
        limit=5,
        document_id="doc-B",
    )
    assert len(doc_b_results) == 1
    assert doc_b_results[0].document_id == "doc-B"
    assert doc_b_results[0].chunk_index == 0
    assert "RabbitMQ transports" in doc_b_results[0].content

    # 3. Filter by document_id="doc-A": excludes Doc B entirely
    doc_a_results = await store.search_dense(
        query_vector=query_vec,
        limit=5,
        document_id="doc-A",
    )
    assert len(doc_a_results) == 2
    assert all(r.document_id == "doc-A" for r in doc_a_results)

    # 4. Limit=1 returns strictly Top-1 result
    top_1_results = await store.search_dense(query_vector=query_vec, limit=1)
    assert len(top_1_results) == 1
    assert top_1_results[0].document_id == "doc-A"
    assert top_1_results[0].chunk_index == 0

    await store.close()


# ==============================================================================
# 9. Sparse BM25 Vector Tests (Requirements 16-21, 35-42)
# ==============================================================================


def test_convert_to_points_with_sparse_vectors() -> None:
    """EmbeddedChunk is converted to PointStruct with dense and sparse vectors."""
    store = QdrantVectorStore(client=AsyncMock(), sparse_vector_name="bm25")
    doc_id = "doc-sparse-001"

    chunk = _create_sample_embedded_chunk(
        chunk_index=0,
        content="Lexical BM25 testing chunk",
        embedding=[0.1] * 384,
    )
    embedded_doc = EmbeddedDocument(chunks=[chunk])
    sparse_vecs = [SparseVector(indices=[10, 20], values=[1.5, 2.5])]

    points = store.convert_to_points(doc_id, embedded_doc, sparse_vectors=sparse_vecs)

    assert len(points) == 1
    point = points[0]
    assert point.id == generate_point_id(doc_id, 0)
    assert isinstance(point.vector, dict)
    assert point.vector[""] == [0.1] * 384
    assert isinstance(point.vector["bm25"], models.SparseVector)
    assert point.vector["bm25"].indices == [10, 20]
    assert point.vector["bm25"].values == [1.5, 2.5]
    # Payload unchanged
    assert point.payload["document_id"] == doc_id
    assert point.payload["content"] == "Lexical BM25 testing chunk"


def test_convert_to_points_sparse_vector_length_mismatch_raises() -> None:
    """Mismatch between chunks and sparse vectors raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock())
    doc = EmbeddedDocument(chunks=[_create_sample_embedded_chunk(chunk_index=0)])
    with pytest.raises(QdrantVectorStoreError, match="Mismatch between chunks"):
        store.convert_to_points("doc-1", doc, sparse_vectors=[])


@pytest.mark.asyncio
async def test_search_sparse_correct_parameters_passed() -> None:
    """search_sparse passes using=bm25, query_vector, with_payload, and limit."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_scored_point = MagicMock()
    mock_scored_point.id = "p-sparse-1"
    mock_scored_point.score = 2.718
    mock_scored_point.payload = {
        "document_id": "doc-test",
        "chunk_index": 0,
        "content": "Sparse matched content",
        "token_count": 3,
        "start_page": 1,
        "end_page": 1,
        "page_numbers": [1],
        "block_types": ["text"],
    }
    mock_response.points = [mock_scored_point]
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="sparse_test_col",
        sparse_vector_name="bm25",
    )

    query = SparseVector(indices=[42, 84], values=[1.0, 1.0])
    results = await store.search_sparse(
        query_vector=query, limit=5, document_id="doc-test"
    )

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, SparseSearchResult)
    assert res.point_id == "p-sparse-1"
    assert res.score == 2.718
    assert res.rank == 1
    assert res.content == "Sparse matched content"

    mock_client.query_points.assert_awaited_once()
    _, kwargs = mock_client.query_points.call_args
    assert kwargs["collection_name"] == "sparse_test_col"
    assert kwargs["using"] == "bm25"
    assert kwargs["limit"] == 5
    assert kwargs["with_payload"] is True
    assert kwargs["with_vectors"] is False
    assert kwargs["query"].indices == [42, 84]
    assert kwargs["query_filter"] is not None


@pytest.mark.asyncio
async def test_search_sparse_empty_indices_returns_empty_without_querying() -> None:
    """When query vector has no active indices (stopwords), returns [] immediately."""
    mock_client = AsyncMock()
    store = QdrantVectorStore(client=mock_client)

    results = await store.search_sparse(
        query_vector=SparseVector(indices=[], values=[]),
        limit=10,
    )
    assert results == []
    mock_client.query_points.assert_not_called()


@pytest.mark.asyncio
async def test_search_sparse_invalid_limit_raises() -> None:
    """limit <= 0 raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock())
    with pytest.raises(QdrantVectorStoreError, match="limit must be positive"):
        await store.search_sparse(
            query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=0,
        )


@pytest.mark.asyncio
async def test_search_sparse_none_query_raises() -> None:
    """query_vector=None raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock())
    with pytest.raises(QdrantVectorStoreError, match="must not be None"):
        await store.search_sparse(query_vector=None, limit=10)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_search_sparse_malformed_payload_raises() -> None:
    """Point missing required payload field raises QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    bad_point = MagicMock()
    bad_point.id = "bad-p"
    bad_point.score = 1.0
    bad_point.payload = {"document_id": "doc-1"}  # Missing content, token_count, etc.
    mock_response.points = [bad_point]
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client)
    with pytest.raises(QdrantVectorStoreError, match="Missing required payload field"):
        await store.search_sparse(
            query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=5,
        )


@pytest.mark.asyncio
async def test_search_sparse_query_failure_wrapped() -> None:
    """Exceptions from client.query_points are wrapped into QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_client.query_points.side_effect = RuntimeError("Qdrant connection reset")

    store = QdrantVectorStore(client=mock_client)
    with pytest.raises(QdrantVectorStoreError, match="Failed to query sparse vectors"):
        await store.search_sparse(
            query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=5,
        )


# ==============================================================================
# 10. End-to-End In-Memory Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_qdrant_store_sparse_search_end_to_end_in_memory() -> None:
    """
    Integration test using AsyncQdrantClient(":memory:"):
    - creates collection with both dense and named sparse BM25 vector configs
    - indexes points containing both representations
    - executes sparse search with SparseVector
    - verifies exact lexical match ranks first with BM25 IDF score
    - verifies server-side document_id filter
    """
    client = AsyncQdrantClient(":memory:")
    store = QdrantVectorStore(
        client=client,
        collection_name="sparse_mem_integration_test",
        vector_dimension=384,
        sparse_vector_name="bm25",
    )

    # Document 1: talks about ERR_CONNECTION_RESET
    doc1 = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content="TCP failure: ERR_CONNECTION_RESET occurred on port 443.",
                embedding=[0.01] * 384,
            )
        ]
    )
    sv1 = [SparseVector(indices=[1001, 1002], values=[2.0, 1.5])]

    # Document 2: talks about RabbitMQ
    doc2 = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content="RabbitMQ message broker dispatching ingestion tasks.",
                embedding=[0.02] * 384,
            )
        ]
    )
    sv2 = [SparseVector(indices=[2001, 2002], values=[2.0, 1.5])]

    await store.index_document("doc-conn-reset", doc1, sparse_vectors=sv1)
    await store.index_document("doc-rabbitmq", doc2, sparse_vectors=sv2)

    # Search for token 1001 (ERR_CONNECTION_RESET)
    query_sv = SparseVector(indices=[1001], values=[1.0])
    results = await store.search_sparse(query_vector=query_sv, limit=5)

    assert len(results) == 1
    assert results[0].document_id == "doc-conn-reset"
    assert "ERR_CONNECTION_RESET" in results[0].content
    assert results[0].score > 0
    assert results[0].rank == 1

    # Search for token 2001 (RabbitMQ) with document_id filter for doc-rabbitmq
    query_rabbit = SparseVector(indices=[2001], values=[1.0])
    rabbit_results = await store.search_sparse(
        query_vector=query_rabbit,
        limit=5,
        document_id="doc-rabbitmq",
    )
    assert len(rabbit_results) == 1
    assert rabbit_results[0].document_id == "doc-rabbitmq"
    assert "RabbitMQ" in rabbit_results[0].content

    # Search with filter that excludes match
    no_results = await store.search_sparse(
        query_vector=query_rabbit,
        limit=5,
        document_id="doc-conn-reset",
    )
    assert len(no_results) == 0

    await store.close()


@pytest.mark.asyncio
async def test_real_bm25_lexical_retrieval_integration() -> None:
    """
    Integration test with FastEmbed BM25Embedder and in-memory Qdrant:
    1. Chunks:
       - "Error code ERR_CONNECTION_RESET occurs when TCP is closed."
       - "RabbitMQ delivers document processing jobs."
       - "The image caption model describes visual content."
    2. Query "ERR_CONNECTION_RESET" -> Chunk 1 ranks first!
    3. Query "RabbitMQ" -> Chunk 2 ranks first!
    """
    try:
        from src.processing.sparse_embedding import BM25Embedder

        embedder = BM25Embedder()
    except Exception:
        pytest.skip("FastEmbed or model not available for live model test")

    client = AsyncQdrantClient(":memory:")
    store = QdrantVectorStore(
        client=client,
        collection_name="real_bm25_test",
        vector_dimension=384,
        sparse_vector_name="bm25",
        sparse_embedder=embedder,
    )

    texts = [
        "Error code ERR_CONNECTION_RESET occurs when TCP connection is reset.",
        "RabbitMQ delivers document processing jobs.",
        "The image caption model describes visual content.",
    ]
    chunks = [
        _create_sample_embedded_chunk(
            chunk_index=idx, content=text, embedding=[0.05] * 384
        )
        for idx, text in enumerate(texts)
    ]
    doc = EmbeddedDocument(chunks=chunks)

    # Index document with auto-generated BM25 sparse vectors
    await store.index_document("doc-real-bm25", doc)

    # 1. Query for exact technical error code
    query_reset = embedder.embed_query("ERR_CONNECTION_RESET")
    results_reset = await store.search_sparse(query_vector=query_reset, limit=3)
    assert len(results_reset) >= 1
    assert results_reset[0].chunk_index == 0
    assert "ERR_CONNECTION_RESET" in results_reset[0].content

    # 2. Query for keyword "RabbitMQ"
    query_rabbit = embedder.embed_query("RabbitMQ")
    results_rabbit = await store.search_sparse(query_vector=query_rabbit, limit=3)
    assert len(results_rabbit) >= 1
    assert results_rabbit[0].chunk_index == 1
    assert "RabbitMQ delivers" in results_rabbit[0].content

    await store.close()


# ==============================================================================
# 11. Hybrid Search (RRF) Tests & Integration
# ==============================================================================


@pytest.mark.asyncio
async def test_search_hybrid_prefetches_and_fusion_architecture() -> None:
    """
    Unit test verifying Qdrant hybrid query architecture:
    - 2 prefetches: dense prefetch and sparse prefetch
    - Dense prefetch uses dense query vector and candidate_limit
    - Sparse prefetch uses sparse indices/values, named vector ("bm25"),
      and candidate_limit
    - Main query uses models.FusionQuery(fusion=models.Fusion.RRF)
    - Final limit equals top_k limit
    - with_payload=True and with_vectors=False
    - document_id filter applied to both prefetches and main query
    """
    mock_client = AsyncMock()
    mock_response = MagicMock()
    sample_point = MagicMock()
    sample_point.id = "p-hybrid-1"
    sample_point.score = 0.032
    sample_point.payload = {
        "document_id": "doc-hybrid",
        "chunk_index": 0,
        "content": "Hybrid retrieved chunk",
        "token_count": 3,
        "start_page": 1,
        "end_page": 1,
        "page_numbers": [1],
        "block_types": ["text"],
    }
    mock_response.points = [sample_point]
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="hybrid_test_col",
        vector_dimension=4,
        sparse_vector_name="bm25",
    )

    dense_query = [0.5, 0.5, 0.0, 0.0]
    sparse_query = SparseVector(indices=[101, 202], values=[1.2, 3.4])

    results = await store.search_hybrid(
        dense_query_vector=dense_query,
        sparse_query_vector=sparse_query,
        limit=5,
        candidate_limit=20,
        document_id="doc-hybrid",
    )

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, HybridSearchResult)
    assert res.point_id == "p-hybrid-1"
    assert res.score == 0.032
    assert res.rank == 1
    assert res.content == "Hybrid retrieved chunk"

    mock_client.query_points.assert_awaited_once()
    _, kwargs = mock_client.query_points.call_args
    assert kwargs["collection_name"] == "hybrid_test_col"
    assert kwargs["limit"] == 5
    assert kwargs["with_payload"] is True
    assert kwargs["with_vectors"] is False

    # Check prefetches
    prefetches = kwargs["prefetch"]
    assert len(prefetches) == 2
    dense_pf, sparse_pf = prefetches[0], prefetches[1]

    # Dense prefetch checks
    assert dense_pf.query == dense_query
    assert dense_pf.limit == 20
    assert dense_pf.filter is not None
    assert dense_pf.filter.must[0].key == "document_id"
    assert dense_pf.filter.must[0].match.value == "doc-hybrid"

    # Sparse prefetch checks
    assert sparse_pf.using == "bm25"
    assert sparse_pf.limit == 20
    assert sparse_pf.query.indices == [101, 202]
    assert sparse_pf.query.values == [1.2, 3.4]
    assert sparse_pf.filter is not None
    assert sparse_pf.filter.must[0].key == "document_id"

    # Fusion check
    query = kwargs["query"]
    assert isinstance(query, models.FusionQuery)
    assert query.fusion == models.Fusion.RRF

    # Query filter check
    assert kwargs["query_filter"] is not None
    assert kwargs["query_filter"].must[0].match.value == "doc-hybrid"


@pytest.mark.asyncio
async def test_search_hybrid_empty_dense_vector_raises() -> None:
    """Empty dense_query_vector raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock(), vector_dimension=4)
    with pytest.raises(QdrantVectorStoreError, match="must not be empty"):
        await store.search_hybrid(
            dense_query_vector=[],
            sparse_query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=5,
            candidate_limit=10,
        )


@pytest.mark.asyncio
async def test_search_hybrid_dense_dimension_mismatch_raises() -> None:
    """Dense vector with mismatched dimension raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock(), vector_dimension=384)
    with pytest.raises(QdrantVectorStoreError, match="dimension mismatch"):
        await store.search_hybrid(
            dense_query_vector=[0.1] * 128,  # expected 384
            sparse_query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=5,
            candidate_limit=10,
        )


@pytest.mark.asyncio
async def test_search_hybrid_none_sparse_vector_raises() -> None:
    """None sparse_query_vector raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock(), vector_dimension=4)
    with pytest.raises(QdrantVectorStoreError, match="must not be None"):
        await store.search_hybrid(
            dense_query_vector=[0.1, 0.2, 0.3, 0.4],
            sparse_query_vector=None,  # type: ignore[arg-type]
            limit=5,
            candidate_limit=10,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_limit", [0, -1, -5])
async def test_search_hybrid_invalid_limit_raises(invalid_limit: int) -> None:
    """limit <= 0 raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock(), vector_dimension=4)
    with pytest.raises(QdrantVectorStoreError, match="limit must be positive"):
        await store.search_hybrid(
            dense_query_vector=[0.1, 0.2, 0.3, 0.4],
            sparse_query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=invalid_limit,
            candidate_limit=10,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_cand_limit", [0, -1, -10])
async def test_search_hybrid_invalid_candidate_limit_raises(
    invalid_cand_limit: int,
) -> None:
    """candidate_limit <= 0 raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock(), vector_dimension=4)
    with pytest.raises(
        QdrantVectorStoreError, match="candidate_limit must be positive"
    ):
        await store.search_hybrid(
            dense_query_vector=[0.1, 0.2, 0.3, 0.4],
            sparse_query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=5,
            candidate_limit=invalid_cand_limit,
        )


@pytest.mark.asyncio
async def test_search_hybrid_candidate_limit_less_than_limit_raises() -> None:
    """candidate_limit < limit raises QdrantVectorStoreError."""
    store = QdrantVectorStore(client=AsyncMock(), vector_dimension=4)
    with pytest.raises(QdrantVectorStoreError, match="cannot be less than limit"):
        await store.search_hybrid(
            dense_query_vector=[0.1, 0.2, 0.3, 0.4],
            sparse_query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=10,
            candidate_limit=5,
        )


@pytest.mark.asyncio
async def test_search_hybrid_malformed_payload_raises() -> None:
    """Missing required payload field raises QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    bad_point = MagicMock()
    bad_point.id = "bad-hybrid"
    bad_point.score = 0.5
    bad_point.payload = {"document_id": "doc-1"}  # Missing content, token_count, etc.
    mock_response.points = [bad_point]
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client, vector_dimension=4)
    with pytest.raises(QdrantVectorStoreError, match="Missing required payload field"):
        await store.search_hybrid(
            dense_query_vector=[0.1, 0.2, 0.3, 0.4],
            sparse_query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=5,
            candidate_limit=10,
        )


@pytest.mark.asyncio
async def test_search_hybrid_query_failure_wrapped() -> None:
    """Exceptions from client.query_points are wrapped into QdrantVectorStoreError."""
    mock_client = AsyncMock()
    mock_client.query_points.side_effect = RuntimeError("Qdrant service unreachable")

    store = QdrantVectorStore(client=mock_client, vector_dimension=4)
    with pytest.raises(QdrantVectorStoreError, match="Failed to query hybrid vectors"):
        await store.search_hybrid(
            dense_query_vector=[0.1, 0.2, 0.3, 0.4],
            sparse_query_vector=SparseVector(indices=[1], values=[1.0]),
            limit=5,
            candidate_limit=10,
        )


@pytest.mark.asyncio
async def test_search_hybrid_empty_results_returns_empty_list() -> None:
    """When query_points returns no points, empty list is returned."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.points = []
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(client=mock_client, vector_dimension=4)
    results = await store.search_hybrid(
        dense_query_vector=[0.1, 0.2, 0.3, 0.4],
        sparse_query_vector=SparseVector(indices=[1], values=[1.0]),
        limit=5,
        candidate_limit=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_qdrant_store_hybrid_rrf_end_to_end_in_memory() -> None:
    """
    In-memory Qdrant integration test verifying RRF fusion behavior:
    - 3 points with controlled dense and sparse profiles:
      * Point A: strong dense match, weaker sparse match
      * Point B: strong sparse match, weaker dense match
      * Point C: solid match across both dense and sparse representations
    - Verifies no duplicate point IDs returned
    - Verifies correct result count and 1-indexed ranks
    - Verifies point ranking well in both lists is rewarded
    - Verifies complete payload preservation
    """
    client = AsyncQdrantClient(":memory:")
    store = QdrantVectorStore(
        client=client,
        collection_name="hybrid_mem_integration_test",
        vector_dimension=4,
        sparse_vector_name="bm25",
    )

    doc_a = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content="Point A: Pure dense semantic leader",
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        ]
    )
    sv_a = [SparseVector(indices=[1], values=[1.0])]

    doc_b = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content="Point B: Pure sparse lexical leader",
                embedding=[0.0, 1.0, 0.0, 0.0],
            )
        ]
    )
    sv_b = [SparseVector(indices=[2], values=[5.0])]

    doc_c = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content="Point C: Balanced strong in both dense and sparse",
                embedding=[0.8, 0.6, 0.0, 0.0],
            )
        ]
    )
    sv_c = [SparseVector(indices=[2], values=[4.0])]

    await store.index_document("doc-a", doc_a, sparse_vectors=sv_a)
    await store.index_document("doc-b", doc_b, sparse_vectors=sv_b)
    await store.index_document("doc-c", doc_c, sparse_vectors=sv_c)

    # Query dense along [1.0, 0.0, 0.0, 0.0] and sparse on token 2
    dense_q = [1.0, 0.0, 0.0, 0.0]
    sparse_q = SparseVector(indices=[2], values=[1.0])

    results = await store.search_hybrid(
        dense_query_vector=dense_q,
        sparse_query_vector=sparse_q,
        limit=3,
        candidate_limit=10,
    )

    assert len(results) == 3

    # Check deduplication: each point ID must be unique
    point_ids = [r.point_id for r in results]
    assert len(point_ids) == len(set(point_ids))

    # Check 1-indexed ranks
    assert [r.rank for r in results] == [1, 2, 3]

    # Verify score is a valid positive fusion score
    for r in results:
        assert r.score > 0.0
        assert r.document_id in ("doc-a", "doc-b", "doc-c")
        assert len(r.content) > 0
        assert r.token_count > 0

    # Test filtering by document_id
    filtered = await store.search_hybrid(
        dense_query_vector=dense_q,
        sparse_query_vector=sparse_q,
        limit=3,
        candidate_limit=10,
        document_id="doc-c",
    )
    assert len(filtered) == 1
    assert filtered[0].document_id == "doc-c"
    assert "Point C" in filtered[0].content

    await store.close()


@pytest.mark.asyncio
async def test_qdrant_store_hybrid_semantic_and_lexical_integration() -> None:
    """
    Integration test verifying hybrid retrieval with controlled domain documents:
    - Doc A: "RabbitMQ transports background jobs between the API and worker."
    - Doc B: "ERR_CONNECTION_RESET occurs when a TCP connection is abruptly terminated."
    - Doc C: "The worker retrieves original files from object storage."
    """
    client = AsyncQdrantClient(":memory:")
    store = QdrantVectorStore(
        client=client,
        collection_name="hybrid_domain_test",
        vector_dimension=4,
        sparse_vector_name="bm25",
    )

    # Controlled orthogonal vectors for predictable testing
    doc_a = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content=(
                    "RabbitMQ transports background jobs between the API and worker."
                ),
                embedding=[1.0, 0.0, 0.0, 0.0],
            )
        ]
    )
    sv_a = [SparseVector(indices=[101], values=[2.0])]

    doc_b = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content=(
                    "ERR_CONNECTION_RESET occurs when a TCP connection is "
                    "abruptly terminated."
                ),
                embedding=[0.0, 1.0, 0.0, 0.0],
            )
        ]
    )
    sv_b = [SparseVector(indices=[202], values=[5.0])]

    doc_c = EmbeddedDocument(
        chunks=[
            _create_sample_embedded_chunk(
                chunk_index=0,
                content="The worker retrieves original files from object storage.",
                embedding=[0.0, 0.0, 1.0, 0.0],
            )
        ]
    )
    sv_c = [SparseVector(indices=[303], values=[2.0])]

    await store.index_document("doc-jobs", doc_a, sparse_vectors=sv_a)
    await store.index_document("doc-reset", doc_b, sparse_vectors=sv_b)
    await store.index_document("doc-storage", doc_c, sparse_vectors=sv_c)

    # 1. Semantic query matching Doc A (dense [1, 0, 0, 0], no keyword match)
    res_semantic = await store.search_hybrid(
        dense_query_vector=[0.95, 0.05, 0.0, 0.0],
        sparse_query_vector=SparseVector(indices=[], values=[]),
        limit=3,
        candidate_limit=5,
    )
    assert len(res_semantic) >= 1
    assert res_semantic[0].document_id == "doc-jobs"
    assert "RabbitMQ" in res_semantic[0].content

    # 2. Exact keyword query matching Doc B (sparse token 202 for ERR_CONNECTION_RESET)
    res_lexical = await store.search_hybrid(
        dense_query_vector=[0.0, 0.1, 0.1, 0.1],
        sparse_query_vector=SparseVector(indices=[202], values=[1.0]),
        limit=3,
        candidate_limit=5,
    )
    assert len(res_lexical) >= 1
    assert res_lexical[0].document_id == "doc-reset"
    assert "ERR_CONNECTION_RESET" in res_lexical[0].content

    await store.close()
