from io import BytesIO

import fitz
import structlog
from PIL import Image

from src.processing.extraction.image_captioner import (
    ImageCaptioner,
    ImageCaptioningError,
)
from src.processing.extraction.ocr_processor import (
    OcrProcessingError,
    OcrProcessor,
)
from src.processing.models.document import (
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)

logger = structlog.get_logger()


class DocumentExtractionError(Exception):
    """Raised when document extraction fails."""


class DocumentExtractor:
    """
    Extracts ordered multimodal content from a PDF.

    Responsibilities:
    - preserve page-level reading order;
    - extract native text blocks;
    - extract image blocks;
    - run OCR on image blocks;
    - generate semantic image descriptions;
    - combine OCR and image meaning in the same location.

    Does NOT:
    - clean text;
    - chunk text;
    - generate embeddings;
    - write to vector storage.
    """

    TEXT_BLOCK_TYPE = 0
    IMAGE_BLOCK_TYPE = 1

    def __init__(
        self,
        ocr_processor: OcrProcessor | None = None,
        image_captioner: ImageCaptioner | None = None,
    ) -> None:
        self.ocr_processor = ocr_processor or OcrProcessor()
        self.image_captioner = image_captioner or ImageCaptioner()

    def extract(self, pdf_bytes: bytes) -> ExtractedDocument:
        """
        Extract ordered content from PDF bytes.

        Example:

        Paragraph 1
            ↓
        Image
            ↓
        Paragraph 2

        becomes:

        Paragraph 1

        [Image text: ...]

        [Image description: ...]

        Paragraph 2
        """

        if not pdf_bytes:
            raise DocumentExtractionError("Cannot extract content from an empty PDF.")

        try:
            document = fitz.open(
                stream=pdf_bytes,
                filetype="pdf",
            )
        except Exception as exc:
            logger.exception(
                "Failed to open PDF",
                error=str(exc),
            )

            raise DocumentExtractionError("Failed to open PDF document.") from exc

        pages: list[ExtractedPage] = []

        try:
            for page_number, page in enumerate(
                document,
                start=1,
            ):
                extracted_page = self._extract_page(
                    page=page,
                    page_number=page_number,
                )

                pages.append(extracted_page)

            extracted_document = ExtractedDocument(pages=pages)

            logger.info(
                "Document extraction completed",
                page_count=len(pages),
                extracted_characters=len(extracted_document.combined_text()),
            )

            return extracted_document

        finally:
            document.close()

    def _extract_page(
        self,
        page: fitz.Page,
        page_number: int,
    ) -> ExtractedPage:
        """
        Extract a page while preserving block order.
        """

        try:
            page_dict = page.get_text("dict")
        except Exception as exc:
            logger.exception(
                "Failed to read PDF page layout",
                page_number=page_number,
                error=str(exc),
            )

            raise DocumentExtractionError(
                f"Failed to inspect PDF page {page_number}."
            ) from exc

        blocks = page_dict.get("blocks", [])

        ordered_blocks = sorted(
            blocks,
            key=self._reading_order_key,
        )

        extracted_blocks: list[ExtractedBlock] = []

        for block in ordered_blocks:
            block_type = block.get("type")

            if block_type == self.TEXT_BLOCK_TYPE:
                text = self._extract_text_block(block)

                if text:
                    extracted_blocks.append(
                        ExtractedBlock(
                            block_type="text",
                            content=text,
                        )
                    )

            elif block_type == self.IMAGE_BLOCK_TYPE:
                image_content = self._extract_image_block(
                    block=block,
                    page_number=page_number,
                )

                if image_content:
                    extracted_blocks.append(
                        ExtractedBlock(
                            block_type="image",
                            content=image_content,
                        )
                    )

        logger.info(
            "PDF page extracted",
            page_number=page_number,
            block_count=len(extracted_blocks),
        )

        return ExtractedPage(
            page_number=page_number,
            blocks=extracted_blocks,
        )

    def _reading_order_key(
        self,
        block: dict,
    ) -> tuple[float, float]:
        """
        Sort blocks approximately in visual reading order.

        Primary:
        top-to-bottom

        Secondary:
        left-to-right
        """

        bbox = block.get(
            "bbox",
            (0.0, 0.0, 0.0, 0.0),
        )

        x0 = float(bbox[0])
        y0 = float(bbox[1])

        return y0, x0

    def _extract_text_block(
        self,
        block: dict,
    ) -> str:
        """
        Reconstruct text from spans while preserving
        line-level structure.
        """

        lines: list[str] = []

        for line in block.get("lines", []):
            spans = line.get("spans", [])

            line_text = "".join(span.get("text", "") for span in spans).strip()

            if line_text:
                lines.append(line_text)

        return "\n".join(lines).strip()

    def _extract_image_block(
        self,
        block: dict,
        page_number: int,
    ) -> str:
        """
        Process one embedded image.

        Both OCR and semantic captioning are attempted.

        Example result:

        [Image text: Paracetamol 500mg]

        [Image description: A medicine bottle with tablets.]
        """

        image_bytes = block.get("image")

        if not image_bytes:
            return ""

        if not self._is_useful_image(image_bytes):
            return ""

        extracted_parts: list[str] = []

        ocr_text = self._run_ocr(
            image_bytes=image_bytes,
            page_number=page_number,
        )

        if ocr_text:
            extracted_parts.append(f"[Image text: {ocr_text}]")

        description = self._generate_description(
            image_bytes=image_bytes,
            page_number=page_number,
        )

        if description:
            extracted_parts.append(f"[Image description: {description}]")

        return "\n\n".join(extracted_parts)

    def _run_ocr(
        self,
        image_bytes: bytes,
        page_number: int,
    ) -> str:
        try:
            return self.ocr_processor.extract_text(image_bytes).strip()

        except OcrProcessingError as exc:
            logger.warning(
                "OCR failed for PDF image",
                page_number=page_number,
                error=str(exc),
            )

            return ""

        except Exception as exc:
            logger.warning(
                "Unexpected failure during image OCR",
                page_number=page_number,
                error=str(exc),
            )

            return ""

    def _generate_description(
        self,
        image_bytes: bytes,
        page_number: int,
    ) -> str:
        try:
            return self.image_captioner.describe(image_bytes).strip()

        except ImageCaptioningError as exc:
            logger.warning(
                "Image captioning failed",
                page_number=page_number,
                error=str(exc),
            )

            return ""

        except Exception as exc:
            logger.warning(
                "Unexpected failure during image captioning",
                page_number=page_number,
                error=str(exc),
            )

            return ""

    def _is_useful_image(
        self,
        image_bytes: bytes,
    ) -> bool:
        """
        Ignore tiny images such as:
        - bullets;
        - icons;
        - separators;
        - tiny logos.

        These usually add noise and waste OCR/vision compute.
        """

        try:
            with Image.open(BytesIO(image_bytes)) as image:
                width, height = image.size

                if width < 100 or height < 100:
                    return False

                return True

        except Exception as exc:
            logger.warning(
                "Could not inspect PDF image",
                error=str(exc),
            )

            return False
