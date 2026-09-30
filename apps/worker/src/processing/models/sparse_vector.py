import math
from dataclasses import dataclass
from typing import Any

from qdrant_client import models


@dataclass(frozen=True)
class SparseVector:
    """
    Application-level sparse vector representation.

    Decouples the domain and retrieval layers from third-party embedding libraries
    (e.g., FastEmbed) and provides strict data validation.

    Attributes:
        indices: 0-indexed integer feature positions (token hashes).
        values: Corresponding floating-point weights (e.g., BM25 term frequencies).
    """

    indices: list[int]
    values: list[float]

    def __post_init__(self) -> None:
        if not isinstance(self.indices, (list, tuple)):
            raise ValueError(
                f"indices must be a list or tuple, got {type(self.indices)}."
            )

        if not isinstance(self.values, (list, tuple)):
            raise ValueError(
                f"values must be a list or tuple, got {type(self.values)}."
            )

        if len(self.indices) != len(self.values):
            raise ValueError(
                f"indices and values must have equal length: "
                f"got {len(self.indices)} indices and {len(self.values)} values."
            )

        # Empty sparse vectors are deliberately supported (e.g., stopword-only text)
        if not self.indices:
            return

        seen_indices: set[int] = set()
        for idx_pos, idx in enumerate(self.indices):
            if isinstance(idx, bool) or not isinstance(idx, int):
                raise ValueError(
                    f"Invalid non-integer index at position {idx_pos}: {idx!r}"
                )
            if idx < 0:
                raise ValueError(
                    f"Negative index detected at position {idx_pos}: {idx}"
                )
            if idx in seen_indices:
                raise ValueError(
                    f"Duplicate index detected at position {idx_pos}: {idx}"
                )
            seen_indices.add(idx)

        for val_pos, val in enumerate(self.values):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(
                    f"Invalid non-numeric value at position {val_pos}: {val!r}"
                )
            if math.isnan(val):
                raise ValueError(
                    f"NaN detected in sparse vector value at position {val_pos}."
                )
            if math.isinf(val):
                raise ValueError(
                    f"Infinity detected in sparse vector value at position {val_pos}."
                )

    def to_qdrant(self) -> models.SparseVector:
        """Convert to Qdrant native SparseVector model."""
        return models.SparseVector(
            indices=list(self.indices),
            values=[float(v) for v in self.values],
        )

    @classmethod
    def from_fastembed(cls, raw: Any) -> "SparseVector":
        """Convert a FastEmbed SparseEmbedding object into an internal SparseVector."""
        raw_indices = getattr(raw, "indices", None)
        raw_values = getattr(raw, "values", None)

        if raw_indices is None or raw_values is None:
            raise ValueError(
                f"Expected object with 'indices' and 'values', got {type(raw)}"
            )

        indices = (
            raw_indices.tolist()
            if hasattr(raw_indices, "tolist")
            else list(raw_indices)
        )
        values = (
            raw_values.tolist() if hasattr(raw_values, "tolist") else list(raw_values)
        )

        return cls(
            indices=[int(i) for i in indices],
            values=[float(v) for v in values],
        )
