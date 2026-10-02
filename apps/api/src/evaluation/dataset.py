import json
from pathlib import Path

from pydantic import BaseModel, Field


class EvalItem(BaseModel):
    """A single evaluation question case with ground truth labels."""

    id: str | None = None
    question: str = Field(..., min_length=1, description="Evaluation query or question.")
    expected_answer: str | None = Field(default=None, description="Reference answer text.")
    expected_document_ids: list[str] = Field(
        default_factory=list,
        description="IDs of ground-truth documents that contain the answer.",
    )
    expected_chunk_indices: list[int] = Field(
        default_factory=list,
        description="Indices of ground-truth chunks.",
    )
    expected_keywords: list[str] = Field(
        default_factory=list,
        description="Key terms that must be mentioned in the answer.",
    )
    should_refuse: bool = Field(
        default=False,
        description="True if the system should refuse due to absent/insufficient evidence.",
    )


class EvalDataset(BaseModel):
    """Collection of evaluation test cases for retrieval and RAG evaluation."""

    items: list[EvalItem] = Field(default_factory=list)

    @classmethod
    def from_json(cls, file_path: str | Path) -> "EvalDataset":
        """Load evaluation dataset from a JSON file."""
        path = Path(file_path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            items = [EvalItem(**item) for item in data]
        elif isinstance(data, dict) and "items" in data:
            items = [EvalItem(**item) for item in data["items"]]
        else:
            raise ValueError("Unsupported JSON format for EvalDataset.")
        return cls(items=items)

    @classmethod
    def from_jsonl(cls, file_path: str | Path) -> "EvalDataset":
        """Load evaluation dataset from a JSON Lines (JSONL) file."""
        path = Path(file_path)
        items = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    items.append(EvalItem(**json.loads(line_str)))
        return cls(items=items)

    def save_json(self, file_path: str | Path) -> None:
        """Save dataset to a JSON file."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump([item.model_dump() for item in self.items], f, indent=2)

    def save_jsonl(self, file_path: str | Path) -> None:
        """Save dataset to a JSONL file."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for item in self.items:
                f.write(json.dumps(item.model_dump()) + "\n")
