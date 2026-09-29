from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from src.processing.embedding import (
    DEFAULT_EMBEDDING_DIMENSION,
    DenseEmbedder,
    DenseEmbeddingError,
    EmbeddingError,
    validate_vectors,
)
from src.processing.models.document import (
    ChunkedDocument,
    DocumentChunk,
    EmbeddedChunk,
    EmbeddedDocument,
)


def _create_sample_chunk(
    index: int = 0,
    content: str = "Sample chunk content",
    tokens: int = 15,
    start_page: int = 1,
    end_page: int = 1,
    page_numbers: list[int] | None = None,
    block_types: list[str] | None = None,
) -> DocumentChunk:
    return DocumentChunk(
        chunk_index=index,
        content=content,
        token_count=tokens,
        start_page=start_page,
        end_page=end_page,
        page_numbers=page_numbers or [1],
        block_types=block_types or ["text"],
    )


@pytest.fixture(autouse=True)
def clean_embedder_cache() -> Iterator[None]:
    DenseEmbedder._clear_cache()
    yield
    DenseEmbedder._clear_cache()



def test_exception_hierarchy() -> None:
    assert issubclass(DenseEmbeddingError, Exception)
    assert issubclass(EmbeddingError, Exception)
    assert EmbeddingError is DenseEmbeddingError


def test_embed_empty_document() -> None:
    """Empty document must return an empty EmbeddedDocument without loading model."""
    mock_model = MagicMock()
    embedder = DenseEmbedder(model=mock_model)

    chunked_doc = ChunkedDocument(chunks=[])
    result = embedder.embed_document(chunked_doc)

    assert isinstance(result, EmbeddedDocument)
    assert result.total_chunks() == 0
    assert result.chunks == []
    assert result.embedding_dimension() is None
    mock_model.encode.assert_not_called()


def test_embed_empty_document_lazy_loading() -> None:
    """Calling embed_document on empty doc never attempts to load model."""

    embedder = DenseEmbedder()
    assert embedder._model is None
    assert DenseEmbedder._cached_model is None

    result = embedder.embed_document(ChunkedDocument(chunks=[]))
    assert result.total_chunks() == 0
    # Model should still not be loaded
    assert embedder._model is None
    assert DenseEmbedder._cached_model is None


def test_embed_one_chunk() -> None:
    """Mock model returns one known vector, correctly attached to chunk."""
    mock_model = MagicMock()
    known_vector = [0.123] * DEFAULT_EMBEDDING_DIMENSION
    mock_model.encode.return_value = [known_vector]

    embedder = DenseEmbedder(model=mock_model)
    chunk = _create_sample_chunk(
        index=0,
        content="Single test chunk content",
        tokens=25,
        start_page=2,
        end_page=2,
        page_numbers=[2],
        block_types=["text"],
    )
    chunked_doc = ChunkedDocument(chunks=[chunk])

    result = embedder.embed_document(chunked_doc)

    assert isinstance(result, EmbeddedDocument)
    assert result.total_chunks() == 1
    assert result.embedding_dimension() == DEFAULT_EMBEDDING_DIMENSION

    embedded_chunk = result.chunks[0]
    assert isinstance(embedded_chunk, EmbeddedChunk)
    assert embedded_chunk.chunk_index == 0
    assert embedded_chunk.content == "Single test chunk content"
    assert embedded_chunk.token_count == 25
    assert embedded_chunk.start_page == 2
    assert embedded_chunk.end_page == 2
    assert embedded_chunk.page_numbers == [2]
    assert embedded_chunk.block_types == ["text"]
    assert embedded_chunk.embedding == known_vector


def test_embed_multiple_chunks_batching() -> None:
    """Multiple chunks are sent in a single batched encode call and preserve order."""
    mock_model = MagicMock()
    vec1 = [0.1] * DEFAULT_EMBEDDING_DIMENSION
    vec2 = [0.2] * DEFAULT_EMBEDDING_DIMENSION
    vec3 = [0.3] * DEFAULT_EMBEDDING_DIMENSION
    mock_model.encode.return_value = [vec1, vec2, vec3]

    embedder = DenseEmbedder(model=mock_model)
    chunks = [
        _create_sample_chunk(index=0, content="First chunk"),
        _create_sample_chunk(index=1, content="Second chunk"),
        _create_sample_chunk(index=2, content="Third chunk"),
    ]
    chunked_doc = ChunkedDocument(chunks=chunks)

    result = embedder.embed_document(chunked_doc)

    # Verify single batched call
    mock_model.encode.assert_called_once_with(
        ["First chunk", "Second chunk", "Third chunk"],
        batch_size=embedder.batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    assert result.total_chunks() == 3
    assert result.chunks[0].content == "First chunk"
    assert result.chunks[0].embedding == vec1
    assert result.chunks[1].content == "Second chunk"
    assert result.chunks[1].embedding == vec2
    assert result.chunks[2].content == "Third chunk"
    assert result.chunks[2].embedding == vec3


def test_batch_size_configuration() -> None:
    """Configured batch size is passed to model.encode."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [[0.05] * DEFAULT_EMBEDDING_DIMENSION]

    custom_batch_size = 16
    embedder = DenseEmbedder(model=mock_model, batch_size=custom_batch_size)
    chunk = _create_sample_chunk()

    embedder.embed_document(ChunkedDocument(chunks=[chunk]))

    mock_model.encode.assert_called_once()
    _, kwargs = mock_model.encode.call_args
    assert kwargs.get("batch_size") == custom_batch_size


def test_normalization_flag_passed() -> None:
    """model.encode is called with normalize_embeddings=True."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [[0.05] * DEFAULT_EMBEDDING_DIMENSION]

    embedder = DenseEmbedder(model=mock_model)
    chunk = _create_sample_chunk()

    embedder.embed_document(ChunkedDocument(chunks=[chunk]))

    mock_model.encode.assert_called_once()
    _, kwargs = mock_model.encode.call_args
    assert kwargs.get("normalize_embeddings") is True


def test_lazy_loading() -> None:
    """Model is not instantiated until embedding is requested."""
    with patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st_cls:
        mock_instance = MagicMock()
        mock_instance.encode.return_value = [[0.1] * DEFAULT_EMBEDDING_DIMENSION]
        mock_st_cls.return_value = mock_instance

        embedder = DenseEmbedder()
        assert embedder._model is None
        mock_st_cls.assert_not_called()

        chunk = _create_sample_chunk()
        result = embedder.embed_document(ChunkedDocument(chunks=[chunk]))

        mock_st_cls.assert_called_once_with(
            "sentence-transformers/all-MiniLM-L6-v2", device="cpu"
        )
        assert result.total_chunks() == 1


def test_model_reuse_across_invocations() -> None:
    """Calling embed_document multiple times reuses the same loaded model instance."""
    with patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st_cls:
        mock_instance = MagicMock()
        mock_instance.encode.return_value = [[0.1] * DEFAULT_EMBEDDING_DIMENSION]
        mock_st_cls.return_value = mock_instance

        embedder1 = DenseEmbedder()
        embedder2 = DenseEmbedder()

        doc1 = ChunkedDocument(chunks=[_create_sample_chunk(content="Doc 1")])
        doc2 = ChunkedDocument(chunks=[_create_sample_chunk(content="Doc 2")])

        embedder1.embed_document(doc1)
        embedder2.embed_document(doc2)

        # Model class instantiated exactly once
        mock_st_cls.assert_called_once()


def test_mismatched_embedding_count_raises_error() -> None:
    """If model returns wrong vector count, raise DenseEmbeddingError."""

    mock_model = MagicMock()
    # 3 chunks but only 2 vectors returned
    mock_model.encode.return_value = [
        [0.1] * DEFAULT_EMBEDDING_DIMENSION,
        [0.2] * DEFAULT_EMBEDDING_DIMENSION,
    ]

    embedder = DenseEmbedder(model=mock_model)
    chunks = [
        _create_sample_chunk(index=0),
        _create_sample_chunk(index=1),
        _create_sample_chunk(index=2),
    ]

    with pytest.raises(DenseEmbeddingError, match="Embedding count mismatch"):
        embedder.embed_document(ChunkedDocument(chunks=chunks))


def test_dimension_mismatch_raises_error() -> None:
    """Vectors of inconsistent dimension must fail validation."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [
        [0.1] * DEFAULT_EMBEDDING_DIMENSION,
        [0.2] * DEFAULT_EMBEDDING_DIMENSION,
        [0.3] * 256,  # Mismatched dimension
    ]

    embedder = DenseEmbedder(model=mock_model)
    chunks = [
        _create_sample_chunk(index=0),
        _create_sample_chunk(index=1),
        _create_sample_chunk(index=2),
    ]

    with pytest.raises(DenseEmbeddingError, match="dimension mismatch"):
        embedder.embed_document(ChunkedDocument(chunks=chunks))


def test_unexpected_dimension_raises_error() -> None:
    """When expected dimension is 384, all vectors having dimension 256 must fail."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [
        [0.1] * 256,
        [0.2] * 256,
    ]

    embedder = DenseEmbedder(model=mock_model, expected_dimension=384)
    chunks = [_create_sample_chunk(index=0), _create_sample_chunk(index=1)]

    with pytest.raises(DenseEmbeddingError, match="dimension mismatch at index 0"):
        embedder.embed_document(ChunkedDocument(chunks=chunks))


def test_empty_vector_raises_error() -> None:
    """An empty vector in model output must fail validation."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [[]]

    embedder = DenseEmbedder(model=mock_model)
    chunk = _create_sample_chunk()

    with pytest.raises(DenseEmbeddingError, match="vector at index 0 is empty"):
        embedder.embed_document(ChunkedDocument(chunks=[chunk]))


def test_nan_vector_raises_error() -> None:
    """Vector containing NaN must fail validation."""
    mock_model = MagicMock()
    nan_vector = [0.1] * DEFAULT_EMBEDDING_DIMENSION
    nan_vector[10] = float("nan")
    mock_model.encode.return_value = [nan_vector]

    embedder = DenseEmbedder(model=mock_model)
    chunk = _create_sample_chunk()

    with pytest.raises(DenseEmbeddingError, match="NaN detected"):
        embedder.embed_document(ChunkedDocument(chunks=[chunk]))


def test_infinity_vector_raises_error() -> None:
    """Vector containing positive or negative Infinity must fail validation."""
    mock_model = MagicMock()
    inf_vector = [0.1] * DEFAULT_EMBEDDING_DIMENSION
    inf_vector[5] = float("inf")
    mock_model.encode.return_value = [inf_vector]

    embedder = DenseEmbedder(model=mock_model)
    chunk = _create_sample_chunk()

    with pytest.raises(DenseEmbeddingError, match="Infinity detected"):
        embedder.embed_document(ChunkedDocument(chunks=[chunk]))

    # Test negative infinity
    neg_inf_vector = [0.1] * DEFAULT_EMBEDDING_DIMENSION
    neg_inf_vector[5] = float("-inf")
    mock_model.encode.return_value = [neg_inf_vector]

    with pytest.raises(DenseEmbeddingError, match="Infinity detected"):
        embedder.embed_document(ChunkedDocument(chunks=[chunk]))


def test_non_numeric_vector_raises_error() -> None:
    """Non-numeric values (like bool or string) must fail validation."""
    mock_model = MagicMock()
    bool_vector = [0.1] * DEFAULT_EMBEDDING_DIMENSION
    bool_vector[2] = True  # bool
    mock_model.encode.return_value = [bool_vector]

    embedder = DenseEmbedder(model=mock_model)
    chunk = _create_sample_chunk()

    with pytest.raises(DenseEmbeddingError, match="Non-numeric embedding value"):
        embedder.embed_document(ChunkedDocument(chunks=[chunk]))


def test_model_loading_failure_raises_error() -> None:
    """Failure during model loading must raise DenseEmbeddingError with context."""
    with patch(
        "sentence_transformers.SentenceTransformer",
        side_effect=RuntimeError("Cannot download weights"),
    ):
        embedder = DenseEmbedder()
        chunk = _create_sample_chunk()

        with pytest.raises(
            DenseEmbeddingError, match="Failed to load dense embedding model"
        ):
            embedder.embed_document(ChunkedDocument(chunks=[chunk]))


def test_model_encode_failure_raises_error() -> None:
    """Inference failures must raise DenseEmbeddingError."""
    mock_model = MagicMock()
    mock_model.encode.side_effect = RuntimeError("GPU out of memory")

    embedder = DenseEmbedder(model=mock_model)
    chunk = _create_sample_chunk()

    with pytest.raises(
        DenseEmbeddingError, match="Failed to generate dense embeddings"
    ):
        embedder.embed_document(ChunkedDocument(chunks=[chunk]))


def test_metadata_preservation() -> None:
    """All metadata fields of DocumentChunk are strictly preserved in EmbeddedChunk."""
    mock_model = MagicMock()
    vector = [0.05] * DEFAULT_EMBEDDING_DIMENSION
    mock_model.encode.return_value = [vector]

    embedder = DenseEmbedder(model=mock_model)
    chunk = DocumentChunk(
        chunk_index=7,
        content="Deep learning architectures in multimodal systems.",
        token_count=142,
        start_page=3,
        end_page=5,
        page_numbers=[3, 4, 5],
        block_types=["text", "image"],
    )

    result = embedder.embed_document(ChunkedDocument(chunks=[chunk]))

    assert result.total_chunks() == 1
    ec = result.chunks[0]
    assert ec.chunk_index == 7
    assert ec.content == "Deep learning architectures in multimodal systems."
    assert ec.token_count == 142
    assert ec.start_page == 3
    assert ec.end_page == 5
    assert ec.page_numbers == [3, 4, 5]
    assert ec.block_types == ["text", "image"]
    assert ec.embedding == vector


def test_embed_texts_lower_level_api() -> None:
    """Lower-level embed_texts method encodes and validates raw strings."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [
        [0.1] * DEFAULT_EMBEDDING_DIMENSION,
        [0.2] * DEFAULT_EMBEDDING_DIMENSION,
    ]

    embedder = DenseEmbedder(model=mock_model)
    assert embedder.embed_texts([]) == []

    vectors = embedder.embed_texts(["hello", "world"])
    assert len(vectors) == 2
    assert len(vectors[0]) == DEFAULT_EMBEDDING_DIMENSION
    assert len(vectors[1]) == DEFAULT_EMBEDDING_DIMENSION
    mock_model.encode.assert_called_once_with(
        ["hello", "world"],
        batch_size=embedder.batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )


def test_validate_vectors_directly() -> None:
    """Direct unit tests for standalone validate_vectors function."""
    valid = [[0.1, 0.2], [0.3, 0.4]]
    # Should pass without error
    validate_vectors(valid, expected_count=2, expected_dimension=2)

    with pytest.raises(DenseEmbeddingError, match="count mismatch"):
        validate_vectors(valid, expected_count=3)
