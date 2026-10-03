from io import BytesIO
from typing import Any

import structlog
from PIL import Image

from src.config import settings

logger = structlog.get_logger()


class ImageCaptioningError(Exception):
    """Raised when image captioning fails."""


class ImageCaptioner:
    """
    Generates semantic descriptions for images using a vision/image-captioning model.

    Responsibilities:
    - Validate image bytes safely using Pillow;
    - Lazily load image-captioning pipeline on first request;
    - Cache loaded model/pipeline in memory to avoid reloading per image;
    - Automatically select GPU (CUDA) if available, falling back to CPU;
    - Return clean semantic descriptions (e.g. "A dog running across a grassy field").
    """

    _cached_pipeline: Any = None
    _cached_model_name: str | None = None

    def __init__(
        self,
        model_name: str | None = None,
        pipeline: Any = None,
    ) -> None:
        self.model_name = model_name or settings.IMAGE_CAPTION_MODEL
        self._pipeline = pipeline

    def describe(self, image_bytes: bytes) -> str:
        """
        Generate a semantic description of the image content.

        Raises:
            ImageCaptioningError: When image is empty, invalid, model fails to load,
                                  or caption generation encounters an error.
        """
        self._validate_image(image_bytes)

        pipe = self._get_pipeline()

        try:
            with Image.open(BytesIO(image_bytes)) as raw_img:
                rgb_image = raw_img.convert("RGB")
                results = pipe(rgb_image, max_new_tokens=50)

        except Exception as exc:
            logger.exception(
                "Image caption inference failed",
                model_name=self.model_name,
                image_size_bytes=len(image_bytes),
                error=str(exc),
            )
            raise ImageCaptioningError(
                f"Failed to generate semantic caption: {exc}"
            ) from exc

        if results and isinstance(results, list):
            first_result = results[0]
            if isinstance(first_result, dict):
                caption = first_result.get("generated_text", "").strip()
                if caption:
                    logger.info(
                        "Image caption generated successfully",
                        model_name=self.model_name,
                        caption_characters=len(caption),
                    )
                    return caption

        raise ImageCaptioningError(
            "Image captioning model returned empty or unexpected output."
        )

    def _validate_image(self, image_bytes: bytes) -> None:
        """Verify that the supplied bytes contain a readable image."""
        if not image_bytes:
            raise ImageCaptioningError(
                "Cannot generate caption for empty image content."
            )

        try:
            with Image.open(BytesIO(image_bytes)) as img:
                img.verify()
        except Exception as exc:
            logger.warning(
                "Invalid image bytes provided for captioning",
                image_size_bytes=len(image_bytes),
                error=str(exc),
            )
            raise ImageCaptioningError(
                "Provided bytes do not represent a valid image."
            ) from exc

    def _get_pipeline(self) -> Any:
        """Lazily load and cache the Transformers image-to-text pipeline."""
        if self._pipeline is not None:
            return self._pipeline

        if (
            ImageCaptioner._cached_pipeline is not None
            and ImageCaptioner._cached_model_name == self.model_name
        ):
            self._pipeline = ImageCaptioner._cached_pipeline
            return self._pipeline

        logger.info(
            "Loading image captioning model",
            model_name=self.model_name,
        )

        try:
            import torch
            from transformers import pipeline

            device = "cuda" if torch.cuda.is_available() else "cpu"

            pipe = pipeline(
                "image-to-text",
                model=self.model_name,
                device=device,
            )  # type: ignore[call-overload]

            ImageCaptioner._cached_pipeline = pipe
            ImageCaptioner._cached_model_name = self.model_name
            self._pipeline = pipe

            logger.info(
                "Image captioning model loaded and cached",
                model_name=self.model_name,
                device=device,
            )
            return self._pipeline

        except Exception as exc:
            logger.exception(
                "Failed to load image captioning model",
                model_name=self.model_name,
                error=str(exc),
            )
            raise ImageCaptioningError(
                f"Failed to load image captioning model '{self.model_name}': {exc}"
            ) from exc

    @classmethod
    def _clear_cache(cls) -> None:
        """Clear cached model and pipeline in memory."""
        cls._cached_pipeline = None
        cls._cached_model_name = None
