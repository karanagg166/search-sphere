import os
import math
import re
from typing import Any

import structlog

from src.config import settings

logger = structlog.get_logger()

# Common English abbreviations that shouldn't trigger sentence splits
_ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "vs",
    "eg",
    "ie",
    "etc",
    "al",
    "inc",
    "corp",
    "co",
    "ltd",
    "dept",
    "fig",
    "no",
    "vol",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
}

# Regex to detect list items
_LIST_ITEM_RE = re.compile(r"^\s*([-*•+]\s+|\d+[\.\)]\s+|\([a-zA-Z0-9]+\)\s+)")


def split_sentences(text: str) -> list[str]:
    """
    Deterministic rule-based sentence segmentation.

    Responsibilities:
    - Never split multimodal markers ([Image text: ...], [Image description: ...]);
    - Preserve list items line-by-line;
    - Handle abbreviations (Dr., e.g., i.e., vs., etc.);
    - Handle numbers and decimals ($1.5M, 2.8%);
    - Handle quotes and terminal punctuation (. ! ?);
    - Fast, deterministic, and free of heavy NLP framework downloads.
    """
    if not text or not text.strip():
        return []

    stripped = text.strip()

    # Multimodal blocks are atomic semantic units and must never be split
    if stripped.startswith("[Image text:") or stripped.startswith(
        "[Image description:"
    ):
        return [stripped]

    # If the text is a list, preserve bullet points as individual units
    lines = [line.strip() for line in stripped.split("\n") if line.strip()]
    if len(lines) > 1 and all(_LIST_ITEM_RE.match(line) for line in lines):
        return lines

    sentences: list[str] = []
    # Tokenize text while inspecting sentence boundaries
    # Match candidate terminal punctuation: . ! ? followed by whitespace or quotes
    pattern = re.compile(r"([.!?]+)([\"'\)\]]*\s+|[\"'\)\]]*$)")
    last_end = 0

    for match in pattern.finditer(stripped):
        punct = match.group(1)
        end_idx = match.end()

        # Potential sentence end is at match.start() + len(punct) + len(quotes)
        candidate = stripped[last_end:end_idx].strip()
        if not candidate:
            continue

        # If punctuation is '.', check for abbreviation or single initial
        if "." in punct:
            # Check preceding word
            preceding_text = stripped[last_end : match.start()]
            words = preceding_text.strip().split()
            if words:
                last_word = words[-1].lower().rstrip(".,;:()")
                # 1. Known abbreviation
                if last_word in _ABBREVIATIONS:
                    continue
                # 2. Single letter initial (e.g. John F. Kennedy or initials A. B.)
                if len(last_word) == 1 and last_word.isalpha():
                    continue

        # Valid sentence boundary
        sentences.append(candidate)
        last_end = end_idx

    # Append any trailing text that did not end with punctuation
    trailing = stripped[last_end:].strip()
    if trailing:
        if sentences:
            # If the trailing snippet has no punctuation, attach to last or add as unit
            if len(trailing.split()) <= 2 and not trailing.endswith((".", "!", "?")):
                sentences[-1] = f"{sentences[-1]} {trailing}"
            else:
                sentences.append(trailing)
        else:
            sentences.append(trailing)

    return [s for s in sentences if s.strip()]


class SemanticEmbedder:
    """
    Generates sentence embeddings for semantic boundary detection.

    Responsibilities:
    - Lazily load sentence-transformers model (default: all-MiniLM-L6-v2) on CPU;
    - Cache loaded model in memory across chunking operations;
    - Support dependency injection for mocking in unit tests to prevent model downloads.
    """

    _cached_model: Any = None
    _cached_model_name: str | None = None

    def __init__(
        self,
        model_name: str | None = None,
        model: Any = None,
    ) -> None:
        self.model_name = model_name or settings.SEMANTIC_CHUNKING_MODEL
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Compute normalized embeddings for a list of texts.
        """
        if not texts:
            return []

        model = self._get_model()

        # Check if model has embed method (FastEmbed)
        if hasattr(model, "embed"):
            embeddings = list(model.embed(texts))
            return [
                vec.tolist()
                if hasattr(vec, "tolist")
                else list(vec)
                if hasattr(vec, "__iter__") and not isinstance(vec, (str, bytes))
                else vec
                for vec in embeddings
            ]

        # Check if model has encode method (SentenceTransformer or Mock)
        if hasattr(model, "encode"):
            embeddings = model.encode(
                texts,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            if hasattr(embeddings, "tolist"):
                return embeddings.tolist()
            return [list(vec) for vec in embeddings]

        # Callable mock fallback
        results = model(texts)
        if hasattr(results, "tolist"):
            return results.tolist()
        return list(results)

    def _get_model(self) -> Any:
        """Lazily load and cache the embedding model on CPU."""
        if self._model is not None:
            return self._model

        if (
            SemanticEmbedder._cached_model is not None
            and SemanticEmbedder._cached_model_name == self.model_name
        ):
            self._model = SemanticEmbedder._cached_model
            return self._model

        # 1. Prefer FastEmbed (ONNX Runtime) for lightweight CPU execution
        try:
            from fastembed import TextEmbedding

            fastembed_model_name = self.model_name
            if (
                not fastembed_model_name.startswith("sentence-transformers/")
                and fastembed_model_name == "all-MiniLM-L6-v2"
            ):
                fastembed_model_name = "sentence-transformers/all-MiniLM-L6-v2"

            logger.info(
                "Loading FastEmbed model for semantic chunking",
                model_name=fastembed_model_name,
            )
            loaded_model = TextEmbedding(model_name=fastembed_model_name, cache_dir=getattr(settings, "FASTEMBED_CACHE_PATH", None) or os.getenv("FASTEMBED_CACHE_PATH"))
            SemanticEmbedder._cached_model = loaded_model
            SemanticEmbedder._cached_model_name = self.model_name
            self._model = loaded_model
            return self._model
        except Exception as fe_exc:
            logger.warning(
                "FastEmbed not available for semantic chunking",
                error=str(fe_exc),
            )

        # 2. Fallback to SentenceTransformer
        logger.info(
            "Loading semantic chunking sentence-transformer model",
            model_name=self.model_name,
        )

        try:
            from sentence_transformers import SentenceTransformer

            loaded_model = SentenceTransformer(self.model_name, device="cpu")
            SemanticEmbedder._cached_model = loaded_model
            SemanticEmbedder._cached_model_name = self.model_name
            self._model = loaded_model

            logger.info(
                "Semantic chunking model loaded and cached",
                model_name=self.model_name,
            )
            return self._model

        except Exception as exc:
            logger.warning(
                "Failed to load sentence-transformer model for semantic chunking",
                model_name=self.model_name,
                error=str(exc),
            )
            raise RuntimeError(
                f"Failed to load semantic chunking model '{self.model_name}': {exc}"
            ) from exc

    @classmethod
    def _clear_cache(cls) -> None:
        """Clear cached model in memory (useful for testing)."""
        cls._cached_model = None
        cls._cached_model_name = None


def cosine_distance(vec_a: list[float], vec_b: list[float]) -> float:
    """
    Compute cosine distance (1.0 - cosine_similarity) between two vectors.
    Returns float in range [0.0, 2.0].
    """
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0

    similarity = dot_product / (norm_a * norm_b)
    # Clamp to avoid floating point precision overflow
    similarity = max(-1.0, min(1.0, similarity))
    return 1.0 - similarity


class SemanticSplitter:
    """
    Detects semantic topic boundaries between adjacent text units.

    Algorithm:
    1. Embed adjacent text units using SemanticEmbedder.
    2. Compute cosine distances between unit[i] and unit[i+1].
    3. Identify significant drops in similarity (high cosine distance).
    4. Thresholding:
       - Configurable absolute distance threshold (e.g. 0.5);
       - Optional statistical percentile threshold (e.g. 80th percentile);
       - Topic boundary is declared if the distance satisfies the configured criteria.
    """

    def __init__(
        self,
        embedder: SemanticEmbedder | None = None,
        distance_threshold: float | None = None,
        percentile_threshold: float | None = None,
    ) -> None:
        self.embedder = embedder or SemanticEmbedder()
        self.distance_threshold = (
            distance_threshold
            if distance_threshold is not None
            else settings.SEMANTIC_DISTANCE_THRESHOLD
        )
        self.percentile_threshold = (
            percentile_threshold
            if percentile_threshold is not None
            else settings.SEMANTIC_SIMILARITY_PERCENTILE
        )

    def calculate_adjacent_distances(self, texts: list[str]) -> list[float]:
        """
        Calculate cosine distances between adjacent elements in `texts`.
        Returns a list of length max(0, len(texts) - 1).
        """
        if len(texts) < 2:
            return []

        embeddings = self.embedder.embed(texts)
        distances: list[float] = []

        for i in range(len(embeddings) - 1):
            dist = cosine_distance(embeddings[i], embeddings[i + 1])
            distances.append(dist)

        return distances

    def find_semantic_boundaries(self, texts: list[str]) -> set[int]:
        """
        Identify indices `i` where a semantic boundary occurs between
        texts[i] and texts[i+1].

        Returns set of boundary indices.
        """
        if len(texts) < 2:
            return set()

        distances = self.calculate_adjacent_distances(texts)
        if not distances:
            return set()

        # Compute percentile threshold if configured and enough samples exist
        cutoff = self.distance_threshold
        if self.percentile_threshold is not None and len(distances) >= 3:
            sorted_dist = sorted(distances)
            k = (len(sorted_dist) - 1) * (self.percentile_threshold / 100.0)
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                percentile_val = sorted_dist[int(k)]
            else:
                d0 = sorted_dist[int(f)] * (c - k)
                d1 = sorted_dist[int(c)] * (k - f)
                percentile_val = d0 + d1

            # Require both significant absolute distance and relative prominence
            cutoff = max(self.distance_threshold, percentile_val)

        boundaries: set[int] = set()
        for idx, dist in enumerate(distances):
            if dist >= cutoff:
                boundaries.add(idx)

        return boundaries
