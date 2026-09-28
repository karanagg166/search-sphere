from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedBlock:
    """
    One extracted block from a PDF page.

    block_type:
    - "text"
    - "image"
    """

    block_type: str
    content: str


@dataclass(frozen=True)
class ExtractedPage:
    """Ordered extracted content for a single PDF page."""

    page_number: int
    blocks: list[ExtractedBlock]

    def combined_text(self) -> str:
        return "\n\n".join(
            block.content.strip() for block in self.blocks if block.content.strip()
        )


@dataclass(frozen=True)
class ExtractedDocument:
    """Complete ordered extracted document."""

    pages: list[ExtractedPage]

    def combined_text(self) -> str:
        return "\n\n".join(
            page.combined_text() for page in self.pages if page.combined_text().strip()
        )


@dataclass(frozen=True)
class CleanedBlock:
    """
    A single cleaned block from a document page, preserving block type and order.

    block_type:
    - "text"
    - "image"
    """

    block_type: str
    content: str


@dataclass(frozen=True)
class CleanedPage:
    """Ordered cleaned blocks for a single page."""

    page_number: int
    blocks: list[CleanedBlock]

    def combined_text(self) -> str:
        return "\n\n".join(
            block.content.strip() for block in self.blocks if block.content.strip()
        )


@dataclass(frozen=True)
class CleanedDocument:
    """Complete ordered cleaned document."""

    pages: list[CleanedPage]

    def combined_text(self) -> str:
        return "\n\n".join(
            page.combined_text() for page in self.pages if page.combined_text().strip()
        )
