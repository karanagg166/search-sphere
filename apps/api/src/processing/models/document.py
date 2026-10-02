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


@dataclass(frozen=True)
class DocumentChunk:
    """
    A single structure-aware semantic chunk of a document.

    Fields:
    - chunk_index: 0-indexed sequential position within the document
    - content: clean text content of the chunk
    - token_count: number of tokens in this chunk
    - start_page: 1-indexed first page number spanned by this chunk
    - end_page: 1-indexed last page number spanned by this chunk
    - page_numbers: sorted unique list of all pages spanned
    - block_types: list of source block types contained ("text", "image")
    """

    chunk_index: int
    content: str
    token_count: int
    start_page: int
    end_page: int
    page_numbers: list[int]
    block_types: list[str]


@dataclass(frozen=True)
class ChunkedDocument:
    """Complete ordered collection of chunks for a document."""

    chunks: list[DocumentChunk]

    def total_chunks(self) -> int:
        return len(self.chunks)

    def total_tokens(self) -> int:
        return sum(chunk.token_count for chunk in self.chunks)


@dataclass(frozen=True)
class EmbeddedChunk:
    """
    A single embedded chunk preserving all source chunk metadata plus dense vector.

    Fields:
    - chunk_index: 0-indexed sequential position within the document
    - content: clean text content of the chunk
    - token_count: number of tokens in this chunk
    - start_page: 1-indexed first page number spanned by this chunk
    - end_page: 1-indexed last page number spanned by this chunk
    - page_numbers: sorted unique list of all pages spanned
    - block_types: list of source block types contained ("text", "image")
    - embedding: normalized dense vector representing the chunk text
    """

    chunk_index: int
    content: str
    token_count: int
    start_page: int
    end_page: int
    page_numbers: list[int]
    block_types: list[str]
    embedding: list[float]


@dataclass(frozen=True)
class EmbeddedDocument:
    """Complete ordered collection of embedded chunks for a document."""

    chunks: list[EmbeddedChunk]

    def total_chunks(self) -> int:
        return len(self.chunks)

    def total_tokens(self) -> int:
        return sum(chunk.token_count for chunk in self.chunks)

    def embedding_dimension(self) -> int | None:
        if not self.chunks:
            return None
        return len(self.chunks[0].embedding)
