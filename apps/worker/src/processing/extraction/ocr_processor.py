from io import BytesIO
from typing import Any

import structlog
from PIL import Image, UnidentifiedImageError

from src.config import settings

logger = structlog.get_logger()


class OcrProcessingError(Exception):
    """Raised when OCR processing fails."""


class OcrProcessor:
    """
    Extracts written text from images using Tesseract OCR.

    Typical use cases:
    - scanned PDF pages;
    - screenshots;
    - images containing labels or written text;
    - photographed documents.

    This class does NOT describe semantic image content such as
    "a dog running in a park". That responsibility belongs to the
    image-captioning/vision component.
    """

    def __init__(self) -> None:
        if settings.TESSERACT_CMD:
            try:
                import pytesseract

                pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
            except Exception:
                pass

    def extract_text(self, image_bytes: bytes) -> str:
        """
        Extract text from an image.

        Flow:
        image bytes
            ↓
        validate image
            ↓
        preprocess image
            ↓
        Tesseract OCR
            ↓
        extracted text
        """

        if not image_bytes:
            raise OcrProcessingError("Cannot perform OCR on empty image content.")

        self._validate_image(image_bytes)

        processed_image = self._preprocess(image_bytes)

        try:
            import pytesseract

            text = pytesseract.image_to_string(
                processed_image,
                config="--oem 3 --psm 6",
            )
        except Exception as exc:
            err_name = type(exc).__name__
            if "TesseractNotFoundError" in err_name:
                logger.exception("Tesseract executable was not found.")
                raise OcrProcessingError(
                    "Tesseract OCR is not installed or is not configured correctly."
                ) from exc

            logger.exception(
                "OCR extraction failed",
                error=str(exc),
            )

            raise OcrProcessingError("Failed to extract text from image.") from exc

        cleaned_text = text.strip()

        logger.info(
            "OCR processing completed",
            extracted_characters=len(cleaned_text),
        )

        return cleaned_text

    def _validate_image(self, image_bytes: bytes) -> None:
        """Verify that the supplied bytes contain a readable image."""

        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.verify()

        except (UnidentifiedImageError, OSError) as exc:
            logger.error(
                "Invalid image supplied for OCR",
                error=str(exc),
            )

            raise OcrProcessingError("OCR input is not a valid image.") from exc

    def _preprocess(self, image_bytes: bytes) -> Any:
        """
        Prepare an image for OCR.

        Processing:
        1. Decode bytes.
        2. Convert to grayscale.
        3. Reduce noise.
        4. Apply adaptive thresholding.

        The result generally gives Tesseract cleaner text than the
        original image.
        """
        import cv2
        import numpy as np

        image_array = np.frombuffer(
            image_bytes,
            dtype=np.uint8,
        )

        image = cv2.imdecode(
            image_array,
            cv2.IMREAD_COLOR,
        )

        if image is None:
            raise OcrProcessingError("Failed to decode image for OCR.")

        grayscale = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2GRAY,
        )

        denoised = cv2.GaussianBlur(
            grayscale,
            (3, 3),
            0,
        )

        thresholded = cv2.adaptiveThreshold(
            denoised,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )

        return thresholded
