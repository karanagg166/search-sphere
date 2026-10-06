import pytest
from fastapi import HTTPException

from src.routers.internal_medical_documents import detect_and_validate_file_type
from tests.binary_fixtures import pdf_bytes, image_bytes


@pytest.mark.parametrize("data,mime", [(pdf_bytes(), "application/pdf"), (image_bytes("JPEG"), "image/jpeg"), (image_bytes("PNG"), "image/png"), (image_bytes("WEBP"), "image/webp")])
def test_supported_real_files(data, mime):
    assert detect_and_validate_file_type(data, mime) == mime


@pytest.mark.parametrize("data,mime", [(b"%PDF-1.4 broken", "application/pdf"), (b"not a PDF", "application/pdf"), (pdf_bytes(), "image/png"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"", "application/pdf")])
def test_invalid_or_forged_files(data, mime):
    with pytest.raises(HTTPException) as caught:
        detect_and_validate_file_type(data, mime)
    assert caught.value.status_code == 400
