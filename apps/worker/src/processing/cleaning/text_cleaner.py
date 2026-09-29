import re

import structlog

from src.processing.cleaning.hyphenation_cleaner import HyphenationCleaner
from src.processing.cleaning.line_cleaner import LineCleaner
from src.processing.cleaning.noise_cleaner import NoiseCleaner
from src.processing.cleaning.whitespace_cleaner import WhitespaceCleaner
from src.processing.models.document import (
    CleanedBlock,
    CleanedDocument,
    CleanedPage,
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)

logger = structlog.get_logger()


class TextCleaner:
    """
    Coordinates modular, deterministic cleaning and normalization of documents.

    Pipeline:
    1. WhitespaceCleaner: Unicode normalization (NFKC), invisible chars removal.
    2. HyphenationCleaner: Repair words broken across lines by margin hyphens.
    3. LineCleaner: Conservative sentence unwrap preserving structure & markers.
    4. NoiseCleaner: Obvious extraction artifact cleanup (repeated symbols).
    5. Final Spacing Normalization: Ensure clean paragraph and word boundaries.

    Does NOT:
    - Call external LLM or ML models (deterministic, CPU-cheap, fully testable);
    - Chunk or split text (reserved for chunking phase);
    - Alter intentional document semantics.
    """

    def __init__(
        self,
        whitespace_cleaner: WhitespaceCleaner | None = None,
        hyphenation_cleaner: HyphenationCleaner | None = None,
        line_cleaner: LineCleaner | None = None,
        noise_cleaner: NoiseCleaner | None = None,
    ) -> None:
        self.whitespace_cleaner = whitespace_cleaner or WhitespaceCleaner()
        self.hyphenation_cleaner = hyphenation_cleaner or HyphenationCleaner()
        self.line_cleaner = line_cleaner or LineCleaner()
        self.noise_cleaner = noise_cleaner or NoiseCleaner()

    def clean(self, text: str) -> str:
        """
        Run the complete cleaning pipeline on a raw text string.
        """
        if not text:
            return ""

        # Phase 1 & 2: Unicode, invisible characters, and whitespace normalization
        step1 = self.whitespace_cleaner.clean(text)

        # Phase 4: Hyphenation repair across line breaks
        step2 = self.hyphenation_cleaner.clean(step1)

        # Phase 3, 6, 7: Conservative line unwrap protecting structure & markers
        step3 = self.line_cleaner.clean(step2)

        # Phase 5: Removal of zero-information divider artifacts and symbol noise
        step4 = self.noise_cleaner.clean(step3)

        # Final pass: normalize spacing after line transformations
        return self.whitespace_cleaner.normalize_spacing(step4)

    _IMAGE_TEXT_RE = re.compile(r"\[Image text:\s*(.*?)\s*\]", re.DOTALL)
    _IMAGE_DESC_RE = re.compile(r"\[Image description:\s*(.*?)\s*\]", re.DOTALL)

    def clean_block(self, block: ExtractedBlock) -> CleanedBlock | None:
        """
        Clean an individual extracted block, preserving its domain block type.
        Returns None if the content is completely empty after cleaning.
        """
        if block.block_type == "image":
            cleaned_content = self._clean_image_block(block.content)
        else:
            cleaned_content = self.clean(block.content)

        if not cleaned_content:
            return None

        return CleanedBlock(
            block_type=block.block_type,
            content=cleaned_content,
        )

    def _clean_image_block(self, content: str) -> str:
        """
        Normalize internal text and descriptions within multimodal tags while strictly
        preserving marker boundaries and structural formatting.
        """
        parts: list[str] = []

        for match in self._IMAGE_TEXT_RE.finditer(content):
            cleaned_text = self.clean(match.group(1))
            if cleaned_text:
                parts.append(f"[Image text: {cleaned_text}]")

        for match in self._IMAGE_DESC_RE.finditer(content):
            cleaned_desc = self.clean(match.group(1))
            if cleaned_desc:
                parts.append(f"[Image description: {cleaned_desc}]")

        if parts:
            return "\n\n".join(parts)

        return self.clean(content)

    def clean_page(self, page: ExtractedPage) -> CleanedPage:
        """
        Clean all blocks within a page, dropping empty blocks.
        """
        cleaned_blocks: list[CleanedBlock] = []

        for block in page.blocks:
            cleaned_block = self.clean_block(block)
            if cleaned_block is not None:
                cleaned_blocks.append(cleaned_block)

        return CleanedPage(
            page_number=page.page_number,
            blocks=cleaned_blocks,
        )

    def clean_document(self, document: ExtractedDocument) -> CleanedDocument:
        """
        Clean an entire extracted document page-by-page and block-by-block.
        """
        cleaned_pages = [self.clean_page(page) for page in document.pages]

        logger.info(
            "Document text cleaning completed",
            pages=len(cleaned_pages),
            total_cleaned_characters=sum(len(p.combined_text()) for p in cleaned_pages),
        )

        return CleanedDocument(pages=cleaned_pages)
