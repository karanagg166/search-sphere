from io import BytesIO
from typing import Any

from PIL import Image

from src.config import settings
from src.inference.sync_adapter import CohereSyncAdapter


class ImageCaptioningError(Exception):
    """Raised when image caption input or output is invalid."""


class ImageCaptioner:
    """Hosted image descriptions, explicitly distinct from verified source facts."""

    _cached_pipeline: Any = None
    _cached_model_name: str | None = None

    def __init__(self, model_name=None, pipeline=None, adapter=None):
        self.model_name = model_name or settings.IMAGE_CAPTION_MODEL
        self._pipeline = pipeline  # Explicit test injection only; never loads weights.
        self.adapter = adapter or CohereSyncAdapter()

    def describe(self, image_bytes: bytes) -> str:
        self._validate_image(image_bytes)
        if self._pipeline is not None:
            with Image.open(BytesIO(image_bytes)) as image:
                results = self._pipeline(image.convert("RGB"), max_new_tokens=50)
            if results and isinstance(results, list) and isinstance(results[0], dict):
                caption = results[0].get("generated_text", "").strip()
                if caption:
                    return caption
            raise ImageCaptioningError("Image captioning returned no description.")
        if not settings.ENABLE_IMAGE_CAPTIONING or self.model_name.lower() in {"none", "disabled", "false", ""}:
            return ""
        return self.adapter.inspect_image(
            image_bytes,
            "Provide a short description of visible non-text content. Label this a generated description. "
            "Do not infer medical diagnoses, identify people or invent unreadable values.",
        ).strip()

    def _validate_image(self, image_bytes):
        if not image_bytes:
            raise ImageCaptioningError("Cannot generate caption for empty image content.")
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.verify()
        except Exception:
            raise ImageCaptioningError("Provided bytes do not represent a valid image.") from None

    @classmethod
    def _clear_cache(cls):
        cls._cached_pipeline = cls._cached_model_name = None
