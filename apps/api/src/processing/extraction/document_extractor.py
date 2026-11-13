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
from src.processing.extraction.layout_regions import render_region, visual_regions
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
        layout_recognizer=None,
    ) -> None:
        self.ocr_processor = ocr_processor or OcrProcessor()
        self.image_captioner = image_captioner or ImageCaptioner()
        self.layout_recognizer = layout_recognizer
        self._hosted_regions = 0
        self._crop_bytes = 0

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
            if self.layout_recognizer is not None:
                with Image.open(BytesIO(image_bytes)) as image:
                    width, height = image.size
                    if width * height > 16_000_000:
                        raise DocumentExtractionError("Image exceeds the pixel limit.")
                blocks = self.layout_recognizer.recognize(
                    image_bytes, page_number=1, bbox=[0, 0, width, height],
                    source_hash=hashlib.sha256(image_bytes).hexdigest(), original_order=0, coordinate_space="image_pixels",
                )
                return ExtractedDocument([ExtractedPage(1, blocks)])
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
            flags = fitz.TEXTFLAGS_DICT
            if self.layout_recognizer is not None:
                flags &= ~fitz.TEXT_PRESERVE_IMAGES
            page_dict = page.get_text("dict", flags=flags)
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
        hosted = []
        if self.layout_recognizer is not None:
            # Image metadata can be read without duplicating original image bytes.
            image_blocks = [{"type": 1, "bbox": info["bbox"]} for info in page.get_image_info()]
            for rect in visual_regions(page, blocks + image_blocks, tables):
                self._hosted_regions += 1
                if self._hosted_regions > 32:
                    raise DocumentExtractionError("Document exceeds the hosted layout region limit.")
                recognized = self.layout_recognizer.recognize(
                    render_region(page, rect), page_number=page_number, bbox=list(rect),
                    source_hash=source_sha256, original_order=len(blocks) + len(tables) + len(hosted),
                    page_size=[float(page.cropbox.width), float(page.cropbox.height)],
                )
                self._crop_bytes += sum(len(block.crop or b"") for block in recognized)
                if self._crop_bytes > 8 * 1024 * 1024:
                    raise DocumentExtractionError("Document exceeds the derived crop byte limit.")
                masks.append(rect)
                hosted.extend({"type": "hosted", "bbox": block.provenance["bbox"], "extracted": block,
                               "original_order": len(blocks) + len(tables) + index} for index, block in enumerate(recognized))
        for index, block in enumerate(blocks):
            if block.get("type") == self.IMAGE_BLOCK_TYPE and self.layout_recognizer is not None:
                continue
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
        blocks = filtered + [{**table, "original_order": len(blocks) + index} for index, table in enumerate(tables)] + hosted
        blocks = [part for block in blocks for part in split_text_columns(block)]
        if len(blocks) > 4096:
            raise DocumentExtractionError("PDF page exceeds the layout block limit.")
        ordered_blocks = reading_order(blocks, float(page.cropbox.width))
        sizes = sorted(float(span.get("size", 11)) for block in ordered_blocks
                       for line in block.get("lines", []) for span in line.get("spans", []))
        baseline = sizes[len(sizes) // 2] if sizes else 11

        extracted_blocks: list[ExtractedBlock] = []

        for block in ordered_blocks:
            if block.get("type") == "hosted":
                extracted_blocks.append(block["extracted"])
                continue
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
                "pdf_to_page": list(page.transformation_matrix),
                "cropbox": list(page.cropbox),
                "document_title": (page.parent.metadata or {}).get("title", ""),
            }
            if block_type == self.IMAGE_BLOCK_TYPE:
                provenance.update({"ocr_review_required": True, "caption_is_description": True})

            if block_type == "table":
                extracted_blocks.append(ExtractedBlock("table", table_text(block["structured"]), provenance, block["structured"]))
                continue

            if block_type == self.TEXT_BLOCK_TYPE:
                text = self._extract_text_block(block)
                size = max((float(span.get("size", baseline)) for line in block.get("lines", [])
                            for span in line.get("spans", [])), default=baseline)
                if len(text) < 180 and size >= baseline * 1.2:
                    provenance["heading_level"] = 1 if size >= baseline * 1.5 else 2
                    provenance["heading_evidence"] = "font_size_heuristic_requires_review"

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
