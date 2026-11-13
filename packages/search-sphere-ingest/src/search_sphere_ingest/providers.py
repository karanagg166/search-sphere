"""Injected boundaries do not import application, database or heavyweight models."""

from collections.abc import Callable
from typing import Protocol

from .models import NormalizedDocument, OCRRegion, Page, SourceRef, Visual


class Tokenizer(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class CallableTokenizer:
    """Wrap the embedding provider's actual tokenizer, including remote tokenization."""

    def __init__(self, name: str, count: Callable[[str], int]) -> None:
        self.name, self._count = name, count

    def count(self, text: str) -> int:
        return self._count(text)


class OCRProvider(Protocol):
    name: str

    def recognize(self, image: bytes, source: SourceRef) -> list[OCRRegion]: ...


class VisionProvider(Protocol):
    name: str

    def understand(self, image: bytes, source: SourceRef) -> Visual: ...


class LayoutProvider(Protocol):
    name: str

    def parse(self, content: bytes, pages: list[Page], checksum: str) -> NormalizedDocument: ...


class AssetStore(Protocol):
    """The caller must enforce ownership when resolving these opaque references."""

    def put(self, asset_id: str, content: bytes, media_type: str) -> None: ...


class ProgressSink(Protocol):
    def __call__(self, stage: str, page_number: int | None, region_id: str | None) -> None: ...


def merge_image_evidence(visual: Visual, regions: list[OCRRegion]) -> Visual:
    """Verified OCR stays literal; descriptions remain separate, with no semantic rewrite."""
    seen: set[tuple[str, tuple[float, float, float, float]]] = set()
    unique = []
    for item in regions:
        key = (item.text, item.bbox.tuple())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    literal = "\n".join(item.text for item in unique)
    return visual.model_copy(
        update={
            "text_regions": unique,
            "ocr_text": literal,
            "combined_text": "\n".join(
                part for part in (visual.visual_description, literal) if part
            ),
        }
    )
