import structlog

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

        raise ImageCaptioningError(
            "Image captioning model is not yet configured."
        )
