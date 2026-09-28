import pytest

from src.processing.image_captioner import (
    ImageCaptioner,
    ImageCaptioningError,
)


def test_image_captioning_error_is_exception() -> None:
    assert issubclass(ImageCaptioningError, Exception)


def test_describe_empty_image() -> None:
    captioner = ImageCaptioner()
    with pytest.raises(
        ImageCaptioningError,
        match="Cannot generate caption for empty image content",
    ):
        captioner.describe(b"")


def test_describe_valid_image(sample_image_bytes: bytes) -> None:
    captioner = ImageCaptioner()
    description = captioner.describe(sample_image_bytes)
    assert "visual diagram" in description
    assert "150x150" in description


def test_describe_corrupted_image() -> None:
    captioner = ImageCaptioner()
    with pytest.raises(
        ImageCaptioningError,
        match="Failed to inspect image for description",
    ):
        captioner.describe(b"not an image")
