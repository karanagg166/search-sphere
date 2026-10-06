from unittest.mock import MagicMock, patch

import pytest

from src.processing.models.document import DocumentChunk
from src.processing.models.sparse_vector import SparseVector
from src.processing.sparse_embedding.bm25_embedder import (
    BM25Embedder,
    SparseEmbeddingError,
)


class FakeSparseEmbedding:
    """Simulates FastEmbed's SparseEmbedding output object."""

    def __init__(self, indices: list[int], values: list[float]) -> None:
        self.indices = indices
        self.values = values


def _create_mock_fastembed_model() -> MagicMock:
    """Create a mock model matching FastEmbed's SparseTextEmbedding interface."""
    model = MagicMock()
    model.passage_embed.return_value = [
        FakeSparseEmbedding(indices=[101, 202], values=[1.5, 2.5])
    ]
    model.query_embed.return_value = [FakeSparseEmbedding(indices=[101], values=[1.0])]
    return model


# ==============================================================================
# 0. SparseVector Model Unit Tests
# ==============================================================================


def test_sparse_vector_valid_instantiation() -> None:
    """Valid indices and values create a valid SparseVector."""
    sv = SparseVector(indices=[0, 5, 10], values=[1.0, 2.5, 0.8])
    assert sv.indices == [0, 5, 10]
    assert sv.values == [1.0, 2.5, 0.8]

    # Qdrant conversion
    qdrant_vec = sv.to_qdrant()
    assert qdrant_vec.indices == [0, 5, 10]
    assert qdrant_vec.values == [1.0, 2.5, 0.8]


def test_sparse_vector_empty_allowed() -> None:
    """Empty sparse vector is valid (e.g. for pure stopwords/punctuation)."""
    sv = SparseVector(indices=[], values=[])
    assert sv.indices == []
    assert sv.values == []
    qdrant_vec = sv.to_qdrant()
    assert qdrant_vec.indices == []
    assert qdrant_vec.values == []


def test_sparse_vector_length_mismatch_fails() -> None:
    """Mismatched indices and values lengths must raise ValueError."""
    with pytest.raises(ValueError, match="equal length"):
        SparseVector(indices=[1, 2], values=[1.0])


def test_sparse_vector_negative_index_fails() -> None:
    """Negative index must raise ValueError."""
    with pytest.raises(ValueError, match="Negative index"):
        SparseVector(indices=[-1, 2], values=[1.0, 2.0])


def test_sparse_vector_duplicate_index_fails() -> None:
    """Duplicate index must raise ValueError."""
    with pytest.raises(ValueError, match="Duplicate index"):
        SparseVector(indices=[5, 5], values=[1.0, 2.0])


def test_sparse_vector_non_integer_index_fails() -> None:
    """Non-integer (or bool) index must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid non-integer index"):
        SparseVector(indices=[True, 2], values=[1.0, 2.0])  # type: ignore[list-item]


def test_sparse_vector_non_finite_value_fails() -> None:
    """NaN and Infinity values must be rejected."""
    with pytest.raises(ValueError, match="NaN detected"):
        SparseVector(indices=[1], values=[float("nan")])

    with pytest.raises(ValueError, match="Infinity detected"):
        SparseVector(indices=[1], values=[float("inf")])


def test_sparse_vector_from_fastembed() -> None:
    """Correctly convert FastEmbed-like object to SparseVector."""
    fake = FakeSparseEmbedding(indices=[123, 456], values=[0.75, 1.25])
    sv = SparseVector.from_fastembed(fake)
    assert sv.indices == [123, 456]
    assert sv.values == [0.75, 1.25]


# ==============================================================================
# 1. Lazy Loading
# ==============================================================================


def test_lazy_loading() -> None:
    """Model is not loaded upon BM25Embedder instantiation."""
    with patch("fastembed.SparseTextEmbedding") as mock_cls:
        embedder = BM25Embedder()
        assert embedder._model is None
        mock_cls.assert_not_called()


# ==============================================================================
# 2. Model Reuse & Caching
# ==============================================================================


def test_model_reuse_across_calls() -> None:
    """Subsequent calls reuse the cached model instance."""
    mock_model = _create_mock_fastembed_model()
    with patch("fastembed.SparseTextEmbedding", return_value=mock_model) as mock_cls:
        # Reset class-level cache for isolated test
        BM25Embedder._cached_model = None
        BM25Embedder._cached_model_name = None

        embedder = BM25Embedder()
        res1 = embedder.embed_texts(["first text"])
        res2 = embedder.embed_texts(["second text"])

        assert len(res1) == 1
        assert len(res2) == 1
        mock_cls.assert_called_once()


# ==============================================================================
# 3. Document Embedding
# ==============================================================================


def test_document_embedding_success() -> None:
    """Known text produces mocked sparse vector with correct internal representation."""
    mock_model = MagicMock()
    mock_model.passage_embed.return_value = [
        FakeSparseEmbedding(indices=[11, 22], values=[0.9, 1.8])
    ]
    embedder = BM25Embedder(model=mock_model)

    results = embedder.embed_texts(["PostgreSQL database indexing."])

    assert len(results) == 1
    assert isinstance(results[0], SparseVector)
    assert results[0].indices == [11, 22]
    assert results[0].values == [0.9, 1.8]
    mock_model.passage_embed.assert_called_once_with(["PostgreSQL database indexing."])


def test_chunk_embedding_success() -> None:
    """embed_chunks extracts chunk content and calls embed_texts."""
    mock_model = MagicMock()
    mock_model.passage_embed.return_value = [
        FakeSparseEmbedding(indices=[1], values=[1.0])
    ]
    embedder = BM25Embedder(model=mock_model)

    chunk = DocumentChunk(
        chunk_index=0,
        content="Distributed vector search chunk.",
        token_count=5,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
    )

    results = embedder.embed_chunks([chunk])
    assert len(results) == 1
    assert results[0].indices == [1]
    mock_model.passage_embed.assert_called_once_with(
        ["Distributed vector search chunk."]
    )


# ==============================================================================
# 4. Query Embedding
# ==============================================================================


def test_query_embedding_uses_query_embed() -> None:
    """Query embedding invokes query_embed, separate from document passage_embed."""
    mock_model = MagicMock()
    mock_model.query_embed.return_value = [
        FakeSparseEmbedding(indices=[42, 84], values=[1.0, 1.0])
    ]
    embedder = BM25Embedder(model=mock_model)

    result = embedder.embed_query("search query")

    assert isinstance(result, SparseVector)
    assert result.indices == [42, 84]
    assert result.values == [1.0, 1.0]
    mock_model.query_embed.assert_called_once_with("search query")
    mock_model.passage_embed.assert_not_called()


# ==============================================================================
# 5. Empty Input Handling
# ==============================================================================


def test_empty_input_handling() -> None:
    """Empty list returns empty list immediately without loading or calling model."""
    mock_model = MagicMock()
    embedder = BM25Embedder(model=mock_model)

    assert embedder.embed_texts([]) == []
    assert embedder.embed_chunks([]) == []

    mock_model.passage_embed.assert_not_called()
    mock_model.query_embed.assert_not_called()


# ==============================================================================
# 6. Batch Input Handling
# ==============================================================================


def test_batch_input_handling() -> None:
    """Multiple texts are passed in batch and mapped to SparseVector objects."""
    mock_model = MagicMock()
    mock_model.passage_embed.return_value = [
        FakeSparseEmbedding(indices=[1], values=[1.0]),
        FakeSparseEmbedding(indices=[2], values=[2.0]),
        FakeSparseEmbedding(indices=[3], values=[3.0]),
    ]
    embedder = BM25Embedder(model=mock_model)

    texts = ["doc 1", "doc 2", "doc 3"]
    results = embedder.embed_texts(texts)

    assert len(results) == 3
    assert results[0].indices == [1]
    assert results[1].indices == [2]
    assert results[2].indices == [3]
    mock_model.passage_embed.assert_called_once_with(texts)


# ==============================================================================
# 7. Invalid Sparse Vector Validation
# ==============================================================================


def test_invalid_sparse_vector_raises_sparse_embedding_error() -> None:
    """Mismatched indices and values from model raise SparseEmbeddingError."""
    mock_model = MagicMock()
    mock_model.passage_embed.return_value = [
        FakeSparseEmbedding(indices=[1, 2], values=[1.0])  # length mismatch
    ]
    embedder = BM25Embedder(model=mock_model)

    with pytest.raises(SparseEmbeddingError, match="Invalid sparse vector"):
        embedder.embed_texts(["test text"])


# ==============================================================================
# 8. Non-Finite Values Validation
# ==============================================================================


def test_non_finite_values_raise_sparse_embedding_error() -> None:
    """NaN or Inf values from model raise SparseEmbeddingError."""
    mock_model = MagicMock()
    mock_model.passage_embed.return_value = [
        FakeSparseEmbedding(indices=[1], values=[float("nan")])
    ]
    embedder = BM25Embedder(model=mock_model)

    with pytest.raises(SparseEmbeddingError, match="Invalid sparse vector"):
        embedder.embed_texts(["test text"])


# ==============================================================================
# 9. Model Load Error Wrapping
# ==============================================================================


def test_model_load_error_wrapped() -> None:
    """Model initialization errors are wrapped into SparseEmbeddingError."""
    BM25Embedder._cached_model = None
    BM25Embedder._cached_model_name = None

    with patch(
        "fastembed.SparseTextEmbedding", side_effect=RuntimeError("Download failed")
    ):
        embedder = BM25Embedder()
        with pytest.raises(SparseEmbeddingError, match="Failed to load sparse model"):
            embedder.embed_texts(["text"])


# ==============================================================================
# 10. Inference Error Wrapping
# ==============================================================================


def test_inference_error_wrapped() -> None:
    """Inference exceptions are wrapped into SparseEmbeddingError."""
    mock_model = MagicMock()
    mock_model.passage_embed.side_effect = RuntimeError("Inference kernel crash")
    embedder = BM25Embedder(model=mock_model)

    with pytest.raises(SparseEmbeddingError, match="inference failed"):
        embedder.embed_texts(["valid text"])

    mock_model.query_embed.side_effect = RuntimeError("Query inference crash")
    with pytest.raises(SparseEmbeddingError, match="inference failed"):
        embedder.embed_query("valid query")


@pytest.fixture(autouse=True)
def isolate_sparse_model_cache():
    BM25Embedder._cached_model = None
    BM25Embedder._cached_model_name = None
    yield
    BM25Embedder._cached_model = None
    BM25Embedder._cached_model_name = None
