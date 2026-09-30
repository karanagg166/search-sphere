import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client import AsyncQdrantClient, models

from src.processing.models.document import EmbeddedChunk, EmbeddedDocument
from src.processing.models.search import DenseSearchResult
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
    # Must NOT recreate collection or index
    mock_client.create_collection.assert_not_called()
    mock_client.create_payload_index.assert_not_called()


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
