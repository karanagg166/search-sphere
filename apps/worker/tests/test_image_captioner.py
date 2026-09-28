import pytest

from src.processing.image_captioner import (
    ImageCaptioner,
    ImageCaptioningError,
)


def test_image_captioning_error_is_exception() -> None:
    assert issubclass(ImageCaptioningError, Exception)


def test_describe_empty_image(sample_image_bytes: bytes) -> None:
    captioner = ImageCaptioner()
    with pytest.raises(
        ImageCaptioningError,
        match="Cannot generate caption for empty image content",
    ):
        captioner.describe(b"")


def test_describe_unconfigured_model(sample_image_bytes: bytes) -> None:
    captioner = ImageCaptioner()
    with pytest.raises(
        ImageCaptioningError,
        match="Image captioning model is not yet configured",
    ):
        captioner.describe(sample_image_bytes)
