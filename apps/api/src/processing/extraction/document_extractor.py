import hashlib
import math
from io import BytesIO

import pymupdf as fitz
import structlog
from PIL import Image

from src.inference.policy import ProviderError
from src.processing.extraction.image_captioner import (
    ImageCaptioner,
    ImageCaptioningError,
)
from src.processing.extraction.native_tables import (
    continue_tables,
    native_tables,
    table_text,
)
from src.processing.extraction.ocr_processor import (
    OcrProcessingError,
    OcrProcessor,
)
from src.processing.extraction.reading_order import reading_order, split_text_columns
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

    def extract_image(self, image_bytes: bytes) -> ExtractedDocument:
        """
        Extract text from an image document (JPEG, PNG, WebP) using OCR.

        Per medical document processing guidelines, OCR is the primary mechanism
        for report images and prescriptions. We intentionally do not use BLIP image
        captioning here to avoid generating invented or hallucinatory medical diagnoses.
        """
        if not image_bytes:
            raise DocumentExtractionError("Cannot extract content from empty image data.")

        try:
            text = self.ocr_processor.extract_text(image_bytes)
            clean_text = text.strip() if text else ""
            blocks = (
                [ExtractedBlock(block_type="image", content=f"[Image text: {clean_text}]", provenance={
                    "block_id": hashlib.sha256(image_bytes).hexdigest() + ":1:0", "page_number": 1,
                    "original_order": 0, "source_kind": "hosted_ocr", "ocr_review_required": True,
                    "document_sha256": hashlib.sha256(image_bytes).hexdigest(),
                })]
                if clean_text
                else []
            )
            return ExtractedDocument(
                pages=[ExtractedPage(page_number=1, blocks=blocks)]
            )
        except ProviderError:
            raise
        except Exception as exc:
            logger.exception("Image OCR extraction failed", error=str(exc))
            raise DocumentExtractionError(f"Image extraction failed: {exc}") from exc

    def extract(
        self,
        content_bytes: bytes,
        mime_type: str = "application/pdf",
    ) -> ExtractedDocument:
        """
        Extract ordered content from document bytes (PDF or Image).
        """
        if not content_bytes:
            raise DocumentExtractionError("Cannot extract content from an empty document.")

        if mime_type.lower() in ("image/jpeg", "image/png", "image/webp", "image/jpg"):
            return self.extract_image(content_bytes)

        try:
            document = fitz.open(
                stream=content_bytes,
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
            if document.needs_pass or document.page_count > 100:
                raise DocumentExtractionError("PDF is encrypted or exceeds the 100-page processing limit.")
            source_sha256 = hashlib.sha256(content_bytes).hexdigest()
            for page_number, page in enumerate(
                document,
                start=1,
            ):
                extracted_page = self._extract_page(
                    page=page,
                    page_number=page_number,
                    source_sha256=source_sha256,
                )

                pages.append(extracted_page)

            continue_tables(pages)
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
        source_sha256: str = "",
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
        if len(blocks) > 4096:
            raise DocumentExtractionError("PDF page exceeds the layout block limit.")
        for block in blocks:
            for item in [block, *block.get("lines", [])]:
                bbox = item.get("bbox", ())
                if len(bbox) != 4 or not all(math.isfinite(float(value)) for value in bbox) or bbox[2] < bbox[0] or bbox[3] < bbox[1]:
                    raise DocumentExtractionError("PDF page contains invalid layout coordinates.")
        try:
            tables = native_tables(page, page_number, source_sha256)
        except Exception as exc:
            raise DocumentExtractionError("Native table inspection failed.") from exc
        filtered = []
        masks = [fitz.Rect(bbox) for table in tables for bbox in table["masks"]]
        for index, block in enumerate(blocks):
            if block.get("type") != self.TEXT_BLOCK_TYPE:
                filtered.append({**block, "original_order": index})
                continue
            lines = []
            for line_index, line in enumerate(block.get("lines", [])):
                rect = fitz.Rect(line["bbox"])
                if any((rect & mask).get_area() >= rect.get_area() * 0.6 for mask in masks):
                    continue
                lines.append({**line, "source_line_index": line_index})
            if lines:
                rect = fitz.Rect(lines[0]["bbox"])
                for line in lines[1:]:
                    rect |= fitz.Rect(line["bbox"])
                filtered.append({**block, "lines": lines, "bbox": tuple(rect), "original_order": index})
        blocks = filtered + [{**table, "original_order": len(blocks) + index} for index, table in enumerate(tables)]
        blocks = [part for block in blocks for part in split_text_columns(block)]
        if len(blocks) > 4096:
            raise DocumentExtractionError("PDF page exceeds the layout block limit.")
        ordered_blocks = reading_order(blocks, float(page.cropbox.width))

        extracted_blocks: list[ExtractedBlock] = []

        for block in ordered_blocks:
            block_type = block.get("type")
            provenance = {
                "block_id": f"{source_sha256}:{page_number}:{block['original_order']}:{block.get('source_part', 0)}",
                "document_sha256": source_sha256, "page_number": page_number,
                "bbox": [float(value) for value in block.get("bbox", (0, 0, 0, 0))],
                "original_order": block["original_order"],
                "source_line_indices": block.get("source_line_indices", list(range(len(block.get("lines", []))))),
                "source_kind": "native_pdf" if block_type in (self.TEXT_BLOCK_TYPE, "table") else "hosted_ocr_vision",
                "reading_order_method": "whitespace_partitions",
                "coordinate_space": "unrotated_pdf_points",
                "page_size": [float(page.cropbox.width), float(page.cropbox.height)],
                "page_rotation": int(page.rotation),
            }
            if block_type == self.IMAGE_BLOCK_TYPE:
                provenance.update({"ocr_review_required": True, "caption_is_description": True})

            if block_type == "table":
                extracted_blocks.append(ExtractedBlock("table", table_text(block["structured"]), provenance, block["structured"]))
                continue

            if block_type == self.TEXT_BLOCK_TYPE:
                text = self._extract_text_block(block)

                if text:
                    extracted_blocks.append(
                        ExtractedBlock(
                            block_type="text",
                            content=text,
                            provenance=provenance,
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
                            provenance=provenance,
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

        except ProviderError:
            raise
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

        except ProviderError:
            raise
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
