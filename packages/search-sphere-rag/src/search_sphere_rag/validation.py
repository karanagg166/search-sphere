"""Strict vector validation and content hashing extracted from Search Sphere."""

import hashlib
import math
from collections.abc import Sequence

from .contracts import EmbeddingValidationError


def compute_content_sha256(text: str) -> str:
    """Return deterministic SHA-256 hex digest for input text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def l2_norm(vector: Sequence[float]) -> float:
    """Compute Euclidean L2 norm of a numeric vector."""
    return math.sqrt(sum(float(x) * float(x) for x in vector))


def validate_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    expected_dimension: int | None = None,
    require_normalized: bool = False,
    norm_tolerance: float = 1e-2,
) -> list[list[float]]:
    """Validate embedding vectors without ever padding or truncating dimensions.

    Rules enforced:
    1. Vector count must equal expected_count.
    2. Vectors must be non-empty and uniform in dimension.
    3. Dimension must match expected_dimension when specified.
    4. Every element must be numeric (int/float, never bool) and finite (no NaN/Inf).
    5. When require_normalized=True, each vector's L2 norm must be within norm_tolerance of 1.0.
    """
    if len(vectors) != expected_count:
        raise EmbeddingValidationError(
            f"Embedding count mismatch: expected {expected_count}, got {len(vectors)}."
        )

    if expected_count == 0:
        return []

    first = vectors[0]
    if not isinstance(first, (list, tuple)) or len(first) == 0:
        raise EmbeddingValidationError("Generated vector at index 0 is empty.")

    target_dim = expected_dimension if expected_dimension is not None else len(first)
    validated: list[list[float]] = []

    for idx, vec in enumerate(vectors):
        if not isinstance(vec, (list, tuple)) or len(vec) == 0:
            raise EmbeddingValidationError(f"Generated vector at index {idx} is empty.")
        if len(vec) != target_dim:
            raise EmbeddingValidationError(
                f"Embedding dimension mismatch at index {idx}: "
                f"expected {target_dim}, got {len(vec)}."
            )

        clean_vec: list[float] = []
        for elem_idx, val in enumerate(vec):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise EmbeddingValidationError(
                    f"Non-numeric embedding value at index {idx}, element {elem_idx}: {val!r}"
                )
            fval = float(val)
            if math.isnan(fval) or math.isinf(fval):
                raise EmbeddingValidationError(
                    f"Non-finite embedding value at index {idx}, element {elem_idx}."
                )
            clean_vec.append(fval)

        if require_normalized:
            norm = l2_norm(clean_vec)
            if abs(norm - 1.0) > norm_tolerance:
                raise EmbeddingValidationError(
                    f"Vector at index {idx} is not L2-normalized (norm={norm:.6f})."
                )

        validated.append(clean_vec)

    return validated
