import math
import os
from typing import Any

import structlog

from src.config import settings
from src.processing.models.document import (
    ChunkedDocument,
    EmbeddedChunk,
    EmbeddedDocument,
)

logger = structlog.get_logger()

DEFAULT_EMBEDDING_DIMENSION = 384

KNOWN_MODEL_DIMENSIONS: dict[str, int] = {
    "sentence-transformers/all-MiniLM-L6-v2": DEFAULT_EMBEDDING_DIMENSION,
    "all-MiniLM-L6-v2": DEFAULT_EMBEDDING_DIMENSION,
}

_UNSET = object()


class DenseEmbeddingError(Exception):
    """Raised when dense embedding generation or vector validation fails."""


# Alias for consistency with domain naming
EmbeddingError = DenseEmbeddingError


def validate_vectors(
    vectors: list[list[float]],
    expected_count: int,
    expected_dimension: int | None = None,
) -> None:
    """
    Validate generated dense embedding vectors against domain integrity rules.

    Rules:
    1. Total number of vectors must equal expected_count (number of chunks).
    2. Vectors must be non-empty.
    3. All vectors must have identical dimensions.
    4. Vector dimension must match expected_dimension when specified.
    5. All values must be numeric (int/float, not bool).
    6. No value can be NaN.
    7. No value can be positive or negative Infinity.

    Raises:
        DenseEmbeddingError: If any validation rule is violated.
    """
    if len(vectors) != expected_count:
        raise DenseEmbeddingError(
            f"Embedding count mismatch: expected {expected_count} vectors for "
            f"{expected_count} chunks, but received {len(vectors)}."
        )

    if expected_count == 0:
        return

    first_vec = vectors[0]
    if not isinstance(first_vec, (list, tuple)) or len(first_vec) == 0:
        raise DenseEmbeddingError("Generated vector at index 0 is empty.")

    target_dim = (
        expected_dimension if expected_dimension is not None else len(first_vec)
    )

    for idx, vec in enumerate(vectors):
        if not isinstance(vec, (list, tuple)) or len(vec) == 0:
            raise DenseEmbeddingError(f"Generated vector at index {idx} is empty.")

        if len(vec) != target_dim:
            raise DenseEmbeddingError(
                f"Embedding dimension mismatch at index {idx}: expected dimension "
                f"{target_dim}, but got {len(vec)}."
            )

        for v_idx, val in enumerate(vec):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise DenseEmbeddingError(
                    f"Non-numeric embedding value at chunk {idx}, "
                    f"element {v_idx}: {val!r}"
                )
            if math.isnan(val):
                raise DenseEmbeddingError(
                    f"NaN detected in embedding vector at chunk {idx}, element {v_idx}."
                )
            if math.isinf(val):
                raise DenseEmbeddingError(
                    f"Infinity detected in embedding vector at chunk {idx}, "
                    f"element {v_idx}."
                )


class DenseEmbedder:
    """
    Produces normalized dense embeddings for ChunkedDocuments.

    Responsibilities:
    - Lazy loading of SentenceTransformer model (default: all-MiniLM-L6-v2);
    - In-memory caching across document tasks within the same worker process;
    - Batch encoding of all document chunk contents in a single model call;
    - Preserving chunk order and full source metadata in EmbeddedChunk;
    - Strict vector validation (count, dimension, NaN, Infinity, non-emptiness);
    - Support dependency injection for unit tests without downloading models.
    """

    _cached_model: Any = None
    _cached_model_name: str | None = None

    def __init__(
        self,
        model_name: str | None = None,
        batch_size: int | None = None,
        model: Any = None,
        device: str | None = None,
        expected_dimension: Any = _UNSET,
    ) -> None:
        self.model_name = model_name or settings.EMBEDDING_MODEL
        self.batch_size = (
            batch_size if batch_size is not None else settings.EMBEDDING_BATCH_SIZE
        )
        self.device = device or getattr(settings, "EMBEDDING_DEVICE", "cpu")
        self._model = model

        if expected_dimension is not _UNSET:
            self._expected_dimension: int | None = expected_dimension
        else:
            self._expected_dimension = KNOWN_MODEL_DIMENSIONS.get(self.model_name)

    @property
    def dimension(self) -> int | None:
        """Return the expected or detected embedding dimension."""
        if self._expected_dimension is not None:
            return self._expected_dimension

        if self._model is not None:
            get_dim = getattr(self._model, "get_sentence_embedding_dimension", None)
            if callable(get_dim):
                try:
                    dim = get_dim()
                    if isinstance(dim, int) and dim > 0:
                        return dim
                except Exception:
                    pass

        return KNOWN_MODEL_DIMENSIONS.get(self.model_name)

    def embed_document(self, chunked_document: ChunkedDocument) -> EmbeddedDocument:
        """
        Embed all chunks of a ChunkedDocument in a single batch.

        If the document contains 0 chunks, returns an empty EmbeddedDocument
        without loading the model.
        """
        if not chunked_document.chunks:
            return EmbeddedDocument(chunks=[])

        texts = [chunk.content for chunk in chunked_document.chunks]
        raw_vectors = self._encode_texts(texts)

        # Validate vectors
        validate_vectors(
            raw_vectors,
            expected_count=len(chunked_document.chunks),
            expected_dimension=self.dimension,
        )

        embedded_chunks = [
            EmbeddedChunk(
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                token_count=chunk.token_count,
                start_page=chunk.start_page,
                end_page=chunk.end_page,
                page_numbers=list(chunk.page_numbers),
                block_types=list(chunk.block_types),
                embedding=raw_vectors[idx],
            )
            for idx, chunk in enumerate(chunked_document.chunks)
        ]

        return EmbeddedDocument(chunks=embedded_chunks)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Lower-level API to compute normalized dense embeddings for raw text strings.
        Validates output vectors before returning.
        """
        if not texts:
            return []

        raw_vectors = self._encode_texts(texts)
        validate_vectors(
            raw_vectors,
            expected_count=len(texts),
            expected_dimension=self.dimension,
        )
        return raw_vectors

    def _encode_texts(self, texts: list[str]) -> list[list[float]]:
        """Run batched model inference and convert outputs to float lists."""
        model = self._get_model()

        try:
            if hasattr(model, "embed"):
                # FastEmbed returns a generator yielding numpy arrays
                batch_sz = self.batch_size if self.batch_size else 32
                embeddings = list(model.embed(texts, batch_size=batch_sz))
            elif hasattr(model, "encode"):
                embeddings = model.encode(
                    texts,
                    batch_size=self.batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
            elif callable(model):
                embeddings = model(texts)
            else:
                raise DenseEmbeddingError(
                    f"Model of type {type(model)} does not provide an "
                    f"'embed' or 'encode' method and is not callable."
                )
        except DenseEmbeddingError:
            raise
        except Exception as exc:
            logger.exception(
                "Dense embedding inference failed",
                model_name=self.model_name,
                num_texts=len(texts),
                error=str(exc),
            )
            raise DenseEmbeddingError(
                f"Failed to generate dense embeddings with model "
                f"'{self.model_name}': {exc}"
            ) from exc

        if hasattr(embeddings, "tolist"):
            raw_vectors = embeddings.tolist()
        elif isinstance(embeddings, (list, tuple)):
            raw_vectors = [
                vec.tolist() if hasattr(vec, "tolist")
                else list(vec) if hasattr(vec, "__iter__") and not isinstance(vec, (str, bytes))
                else vec
                for vec in embeddings
            ]
        else:
            raise DenseEmbeddingError(
                f"Unexpected embedding output type: {type(embeddings)}"
            )

        return raw_vectors

    def _get_model(self) -> Any:
        """Lazily load and cache embedding model (prefers FastEmbed ONNX Runtime for low memory)."""
        if self._model is not None:
            return self._model

        if (
            DenseEmbedder._cached_model is not None
            and DenseEmbedder._cached_model_name == self.model_name
        ):
            self._model = DenseEmbedder._cached_model
            return self._model

        # 1. Prefer FastEmbed (ONNX Runtime) to avoid PyTorch OOM on memory-constrained servers (e.g. Render free tier 512MB)
        try:
            from fastembed import TextEmbedding

            fastembed_model_name = self.model_name
            if not fastembed_model_name.startswith("sentence-transformers/") and fastembed_model_name == "all-MiniLM-L6-v2":
                fastembed_model_name = "sentence-transformers/all-MiniLM-L6-v2"

            cache_path = getattr(settings, "FASTEMBED_CACHE_PATH", None) or os.getenv("FASTEMBED_CACHE_PATH")
            logger.info(
                "Loading FastEmbed TextEmbedding model (ONNX Runtime)",
                model_name=fastembed_model_name,
                cache_path=cache_path,
            )
            loaded_model = TextEmbedding(
                model_name=fastembed_model_name,
                cache_dir=cache_path,
            )
            DenseEmbedder._cached_model = loaded_model
            DenseEmbedder._cached_model_name = self.model_name
            self._model = loaded_model
            logger.info(
                "FastEmbed model loaded and cached successfully",
                model_name=fastembed_model_name,
            )
            return self._model
        except Exception as fastembed_exc:
            logger.warning(
                "FastEmbed unavailable or failed to initialize",
                model_name=self.model_name,
                error=str(fastembed_exc),
            )
            if getattr(settings, "ENVIRONMENT", "").lower() == "production":
                raise DenseEmbeddingError(
                    f"FastEmbed failed to initialize in production: {fastembed_exc}"
                ) from fastembed_exc

        # 2. Fallback to SentenceTransformer
        logger.info(
            "Loading sentence-transformer embedding model",
            model_name=self.model_name,
            device=self.device,
        )

        try:
            from sentence_transformers import SentenceTransformer

            loaded_model = SentenceTransformer(self.model_name, device=self.device)
            DenseEmbedder._cached_model = loaded_model
            DenseEmbedder._cached_model_name = self.model_name
            self._model = loaded_model

            logger.info(
                "Dense embedding model loaded and cached",
                model_name=self.model_name,
                device=self.device,
            )
            return self._model

        except Exception as exc:
            logger.exception(
                "Failed to load sentence-transformer model",
                model_name=self.model_name,
                error=str(exc),
            )
            raise DenseEmbeddingError(
                f"Failed to load dense embedding model '{self.model_name}': {exc}"
            ) from exc

    @classmethod
    def _clear_cache(cls) -> None:
        """Clear cached model in memory (useful for testing)."""
        cls._cached_model = None
        cls._cached_model_name = None
