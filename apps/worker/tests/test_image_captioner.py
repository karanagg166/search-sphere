from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from src.processing.extraction.image_captioner import (
    ImageCaptioner,
    ImageCaptioningError,
)


@pytest.fixture(autouse=True)
def clear_captioner_cache() -> Generator[None, None, None]:
    """Ensure cached pipeline state does not leak between unit tests."""
    ImageCaptioner._clear_cache()
    yield
    ImageCaptioner._clear_cache()


def test_image_captioning_error_is_exception() -> None:
    assert issubclass(ImageCaptioningError, Exception)


def test_describe_empty_image() -> None:
    captioner = ImageCaptioner()
    with pytest.raises(
        ImageCaptioningError,
        match="Cannot generate caption for empty image content",
    ):
        captioner.describe(b"")


def test_describe_invalid_image() -> None:
    captioner = ImageCaptioner()
    with pytest.raises(
        ImageCaptioningError,
        match="Provided bytes do not represent a valid image",
    ):
        captioner.describe(b"not an image file")


def test_describe_success_with_injected_pipeline(
    sample_image_bytes: bytes,
) -> None:
    mock_pipeline = MagicMock()
    mock_pipeline.return_value = [
        {"generated_text": "A dog running through a grassy field."}
    ]

    captioner = ImageCaptioner(pipeline=mock_pipeline)
    result = captioner.describe(sample_image_bytes)

    assert result == "A dog running through a grassy field."
    mock_pipeline.assert_called_once()
    args, kwargs = mock_pipeline.call_args
    assert kwargs.get("max_new_tokens") == 50


def test_describe_lazy_loading_and_pipeline_reuse(
    sample_image_bytes: bytes,
) -> None:
    mock_pipeline = MagicMock()
    mock_pipeline.return_value = [
        {"generated_text": "A medicine bottle with tablets beside it."}
    ]

    with patch(
        "src.processing.extraction.image_captioner.pipeline",
        return_value=mock_pipeline,
    ) as mock_factory:
        captioner1 = ImageCaptioner(model_name="mock/blip-captioner")
        assert ImageCaptioner._cached_pipeline is None  # Not loaded at init

        res1 = captioner1.describe(sample_image_bytes)
        assert res1 == "A medicine bottle with tablets beside it."
        assert mock_factory.call_count == 1

        # Second instance reusing the same cached model in memory
        captioner2 = ImageCaptioner(model_name="mock/blip-captioner")
        res2 = captioner2.describe(sample_image_bytes)
        assert res2 == "A medicine bottle with tablets beside it."

        # Verify pipeline factory was NOT called a second time
        assert mock_factory.call_count == 1


def test_describe_pipeline_load_failure(sample_image_bytes: bytes) -> None:
    with patch(
        "src.processing.extraction.image_captioner.pipeline",
        side_effect=RuntimeError("Weights failed to download"),
    ):
        captioner = ImageCaptioner(model_name="failing/model")
        with pytest.raises(
            ImageCaptioningError,
            match="Failed to load image captioning model",
        ):
            captioner.describe(sample_image_bytes)


def test_describe_inference_failure(sample_image_bytes: bytes) -> None:
    mock_pipeline = MagicMock(side_effect=RuntimeError("GPU/CPU runtime failure"))
    captioner = ImageCaptioner(pipeline=mock_pipeline)

    with pytest.raises(
        ImageCaptioningError,
        match="Failed to generate semantic caption",
    ):
        captioner.describe(sample_image_bytes)


def test_describe_empty_model_output(sample_image_bytes: bytes) -> None:
    mock_pipeline = MagicMock(return_value=[])
    captioner = ImageCaptioner(pipeline=mock_pipeline)

    with pytest.raises(
        ImageCaptioningError,
        match="Image captioning model returned empty or unexpected output",
    ):
        captioner.describe(sample_image_bytes)


def test_describe_device_cuda_fallback(sample_image_bytes: bytes) -> None:
    mock_pipeline = MagicMock()
    mock_pipeline.return_value = [{"generated_text": "A technical diagram."}]

    with (
        patch(
            "src.processing.extraction.image_captioner.torch.cuda.is_available",
            return_value=True,
        ),
        patch(
            "src.processing.extraction.image_captioner.pipeline",
            return_value=mock_pipeline,
        ) as mock_factory,
    ):
        captioner = ImageCaptioner(model_name="test/cuda-blip")
        captioner.describe(sample_image_bytes)

        mock_factory.assert_called_once_with(
            "image-to-text",
            model="test/cuda-blip",
            device="cuda",
        )
