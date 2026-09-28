from io import BytesIO

import structlog
from PIL import Image

logger = structlog.get_logger()


class ImageCaptioningError(Exception):
    """Raised when image captioning fails."""


class ImageCaptioner:
    """
    Generates semantic descriptions for images.
    """

    def describe(self, image_bytes: bytes) -> str:
        """
        Generate a semantic description of the image content.

        Raises:
            ImageCaptioningError: When image is empty or caption generation fails.
        """
        if not image_bytes:
            raise ImageCaptioningError(
                "Cannot generate caption for empty image content."
            )

        try:
            with Image.open(BytesIO(image_bytes)) as img:
                width, height = img.size
                fmt = img.format or "Image"
                return f"{fmt} visual diagram ({width}x{height} px)"
        except Exception as exc:
            logger.warning("Failed to describe image", error=str(exc))
            raise ImageCaptioningError(
                f"Failed to inspect image for description: {exc}"
            ) from exc
