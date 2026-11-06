from io import BytesIO

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from src.inference.sync_adapter import CohereSyncAdapter


class OcrProcessingError(Exception):
    """Raised when deterministic image validation or preprocessing fails."""


class OcrProcessor:
    """Preprocess images deterministically and extract visible text through Cohere."""

    def __init__(self, adapter=None):
        self.adapter = adapter or CohereSyncAdapter()

    def extract_text(self, image_bytes: bytes) -> str:
        self._validate_image(image_bytes)
        return self.adapter.inspect_image(
            image_bytes,
            "Transcribe only visible text verbatim, preserving numbers, units, identifiers and line order. "
            "Write [unreadable] for uncertain characters. Do not infer diagnoses or missing values.",
            preprocess=True,
        ).strip()

    def _validate_image(self, image_bytes: bytes) -> None:
        if not image_bytes:
            raise OcrProcessingError("Cannot perform OCR on empty image content.")
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise OcrProcessingError("OCR input is not a valid image.") from exc

    def _preprocess(self, image_bytes: bytes):
        self._validate_image(image_bytes)
        with Image.open(BytesIO(image_bytes)) as source:
            return ImageOps.autocontrast(ImageOps.grayscale(source)).filter(ImageFilter.MedianFilter(3))
