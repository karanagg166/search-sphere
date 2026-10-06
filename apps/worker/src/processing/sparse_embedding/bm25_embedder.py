from collections.abc import Iterable
from typing import Any

import structlog

from src.config import settings
from src.processing.models.document import DocumentChunk
from src.processing.models.sparse_vector import SparseVector

logger = structlog.get_logger()


class SparseEmbeddingError(Exception):
    """Raised when BM25 sparse representation generation or vector validation fails."""


# Domain-consistent alias
BM25EmbeddingError = SparseEmbeddingError


class BM25Embedder:
    """
    Produces sparse lexical BM25 representations for document chunks and user queries.

    Uses FastEmbed's supported `Qdrant/bm25` sparse model.

    BM25 Parameters Note:
        The `Qdrant/bm25` model comes calibrated with standard document chunk parameters
        (avg_len=256.0, k=1.2, b=0.75) based on the target corpus statistics of text
        chunks. Term frequency calculation is performed during document embedding
        (passage_embed), while query embedding (query_embed) assigns uniform
        weights (1.0) to query token hashes, allowing Qdrant's server-side
        `Modifier.IDF` to compute exact BM25 scores.

    Responsibilities:
    - Lazy loading of FastEmbed SparseTextEmbedding model (default: Qdrant/bm25);
    - In-memory caching and reuse across invocations;
    - Distinct handling for document chunk (passage_embed) vs query (query_embed);
    - Batch encoding of chunk text contents;
    - Boundary isolation: converts third-party sparse objects to SparseVector;
    - Dependency injection support for unit testing without downloading model weights;
    - Structured error wrapping with SparseEmbeddingError.
    """

    _cached_model: Any = None
    _cached_model_name: str | None = None

    def __init__(
        self,
        model_name: str | None = None,
        batch_size: int | None = None,
        model: Any = None,
    ) -> None:
        self.model_name: str = str(
            model_name or getattr(settings, "SPARSE_MODEL", "Qdrant/bm25")
        )
        self.batch_size = (
            batch_size
            if batch_size is not None
            else getattr(settings, "SPARSE_BATCH_SIZE", 64)
        )
        self._model = model

    def _get_model(self) -> Any:
        """Lazily load and cache the SparseTextEmbedding model instance."""
        if self._model is not None:
            return self._model

        if (
            BM25Embedder._cached_model is not None
            and BM25Embedder._cached_model_name == self.model_name
        ):
            return BM25Embedder._cached_model

        try:
            logger.info(
                "Loading BM25 sparse embedding model", model_name=self.model_name
            )
            from fastembed import SparseTextEmbedding

            loaded_model = SparseTextEmbedding(model_name=self.model_name, cache_dir=settings.FASTEMBED_CACHE_PATH)
            BM25Embedder._cached_model = loaded_model
            BM25Embedder._cached_model_name = self.model_name
            return loaded_model
        except Exception as exc:
            logger.exception(
                "Failed to initialize BM25 sparse embedding model",
                model_name=self.model_name,
                error=str(exc),
            )
            raise SparseEmbeddingError(
                f"Failed to load sparse model '{self.model_name}': {exc}"
            ) from exc

    def embed_chunks(self, chunks: list[DocumentChunk]) -> list[SparseVector]:
        """
        Compute BM25 sparse representations for a list of document chunks.

        If the list is empty, returns an empty list without loading the model.
        """
        if not chunks:
            return []

        texts = [chunk.content for chunk in chunks]
        return self.embed_texts(texts)

    def embed_texts(self, texts: list[str]) -> list[SparseVector]:
        """
        Compute BM25 document representations for an arbitrary list of text strings.

        Uses passage_embed (or embed fallback) to produce document representations.
        """
        if not texts:
            return []

        model = self._get_model()

        try:
            if hasattr(model, "passage_embed"):
                raw_results: Iterable[Any] = model.passage_embed(texts)
            elif hasattr(model, "embed"):
                raw_results = model.embed(texts)
            elif callable(model):
                raw_results = model(texts)
            else:
                raise SparseEmbeddingError(
                    f"Sparse model object has no passage_embed/embed method: "
                    f"{type(model)}"
                )

            sparse_vectors: list[SparseVector] = []
            for item in raw_results:
                if isinstance(item, SparseVector):
                    sparse_vectors.append(item)
                else:
                    sparse_vectors.append(SparseVector.from_fastembed(item))

            if len(sparse_vectors) != len(texts):
                raise SparseEmbeddingError(
                    f"Expected {len(texts)} sparse vectors for {len(texts)} texts, "
                    f"but got {len(sparse_vectors)}."
                )

            return sparse_vectors

        except SparseEmbeddingError:
            raise
        except ValueError as exc:
            # Vector validation failure (e.g. mismatched lengths, NaN, negative index)
            logger.exception("Sparse vector validation failed", error=str(exc))
            raise SparseEmbeddingError(
                f"Invalid sparse vector produced: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Failed to compute sparse document representations",
                text_count=len(texts),
                error=str(exc),
            )
            raise SparseEmbeddingError(
                f"Sparse document embedding inference failed: {exc}"
            ) from exc

    def embed_query(self, query: str) -> SparseVector:
        """
        Compute BM25 sparse representation for a single user query string.

        Uses query_embed to produce query-side token hashes with uniform weights (1.0).
        """
        if not isinstance(query, str) or not query.strip():
            raise SparseEmbeddingError("Query must be a non-empty string.")

        clean_query = query.strip()
        model = self._get_model()

        try:
            if hasattr(model, "query_embed"):
                raw_results = list(model.query_embed(clean_query))
            elif hasattr(model, "embed"):
                raw_results = list(model.embed([clean_query]))
            elif callable(model):
                raw_results = list(model([clean_query]))
            else:
                raise SparseEmbeddingError(
                    f"Sparse model object has no query_embed/embed method: "
                    f"{type(model)}"
                )

            if not raw_results:
                # E.g. query consists entirely of non-alphanumeric / ignored characters
                return SparseVector(indices=[], values=[])

            raw_item = raw_results[0]
            if isinstance(raw_item, SparseVector):
                return raw_item

            return SparseVector.from_fastembed(raw_item)

        except SparseEmbeddingError:
            raise
        except ValueError as exc:
            logger.exception("Sparse query vector validation failed", error=str(exc))
            raise SparseEmbeddingError(f"Invalid sparse query vector: {exc}") from exc
        except Exception as exc:
            logger.exception(
                "Failed to compute sparse query representation",
                query=clean_query,
                error=str(exc),
            )
            raise SparseEmbeddingError(
                f"Sparse query embedding inference failed: {exc}"
            ) from exc
