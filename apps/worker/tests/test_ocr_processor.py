from unittest.mock import patch

import numpy as np
import pytesseract
import pytest

from src.processing.ocr_processor import (
    OcrProcessingError,
    OcrProcessor,
)


def test_extract_text_empty_input() -> None:
    processor = OcrProcessor()
    with pytest.raises(
        OcrProcessingError,
        match="Cannot perform OCR on empty image content",
    ):
        processor.extract_text(b"")


def test_extract_text_invalid_image() -> None:
    processor = OcrProcessor()
    with pytest.raises(
        OcrProcessingError,
        match="OCR input is not a valid image",
    ):
        processor.extract_text(b"not an image at all")


def test_preprocess_valid_image(sample_image_bytes: bytes) -> None:
    processor = OcrProcessor()
    processed = processor._preprocess(sample_image_bytes)

    assert isinstance(processed, np.ndarray)
    assert len(processed.shape) == 2  # Grayscale thresholded 2D matrix
    assert processed.shape[0] == 150
    assert processed.shape[1] == 150


def test_preprocess_decode_failure() -> None:
    processor = OcrProcessor()
    with patch("cv2.imdecode", return_value=None):
        with pytest.raises(
            OcrProcessingError,
            match="Failed to decode image for OCR",
        ):
            processor._preprocess(b"fake_bytes")


def test_extract_text_success(sample_image_bytes: bytes) -> None:
    processor = OcrProcessor()
    with patch("pytesseract.image_to_string", return_value="  Detected OCR text  \n"):
        result = processor.extract_text(sample_image_bytes)

    assert result == "Detected OCR text"


def test_extract_text_tesseract_not_found(sample_image_bytes: bytes) -> None:
    processor = OcrProcessor()
    with patch(
        "pytesseract.image_to_string",
        side_effect=pytesseract.TesseractNotFoundError(),
    ):
        with pytest.raises(
            OcrProcessingError,
            match="Tesseract OCR is not installed or is not configured correctly",
        ):
            processor.extract_text(sample_image_bytes)


def test_extract_text_generic_failure(sample_image_bytes: bytes) -> None:
    processor = OcrProcessor()
    with patch(
        "pytesseract.image_to_string",
        side_effect=RuntimeError("Internal OCR engine crash"),
    ):
        with pytest.raises(
            OcrProcessingError,
            match="Failed to extract text from image",
        ):
            processor.extract_text(sample_image_bytes)


def test_custom_tesseract_cmd() -> None:
    with patch(
        "src.processing.ocr_processor.settings.TESSERACT_CMD",
        "/custom/bin/tesseract",
    ):
        processor = OcrProcessor()
        assert processor is not None
        assert pytesseract.pytesseract.tesseract_cmd == "/custom/bin/tesseract"

