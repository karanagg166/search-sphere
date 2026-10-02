from dataclasses import dataclass

import structlog

from src.config import settings
from src.processing.chunking.semantic_splitter import (
    SemanticSplitter,
    split_sentences,
)
from src.processing.chunking.token_counter import TokenCounter
from src.processing.models.document import (
    ChunkedDocument,
    CleanedDocument,
    DocumentChunk,
)

logger = structlog.get_logger()


@dataclass(frozen=True)
class _TextUnit:
    """
    Internal representation of a logical text unit for chunking.
    """

    content: str
    page_number: int
    block_type: str
    token_count: int
    is_intro_header: bool = False


class DocumentChunker:
    """
    Structure-aware hybrid semantic chunker.

    Pipeline:
    1. Structure Analysis: Convert CleanedDocument into ordered logical units
       (paragraphs, lists, headings, multimodal image blocks).
    2. Atomic Unit Protection: Treat multimodal image blocks
       [Image text: ...] + [Image description: ...] as indivisible units.
       Keep list headers with list items.
    3. Oversized Fallback: Gracefully break units exceeding CHUNK_MAX_TOKENS
       (paragraphs -> sentences -> token-aware word boundaries).
    4. Semantic Boundary Detection: Generate unit embeddings and detect topic changes
       via adjacent cosine distances.
    5. Token Budget Enforcement: Respect target tokens preference, hard maximum limits,
       and boundary overlap context.
    6. Metadata Preservation: Retain page spans (start_page, end_page, page_numbers)
       and block_types for citations and downstream indexing.
    """

    def __init__(
        self,
        token_counter: TokenCounter | None = None,
        semantic_splitter: SemanticSplitter | None = None,
        target_tokens: int | None = None,
        max_tokens: int | None = None,
        overlap_tokens: int | None = None,
        min_chunk_tokens: int = 30,
    ) -> None:
        self.token_counter = token_counter or TokenCounter()
        self.semantic_splitter = semantic_splitter or SemanticSplitter()
        self.target_tokens = (
            target_tokens if target_tokens is not None else settings.CHUNK_TARGET_TOKENS
        )
        self.max_tokens = (
            max_tokens if max_tokens is not None else settings.CHUNK_MAX_TOKENS
        )
        self.overlap_tokens = (
            overlap_tokens
            if overlap_tokens is not None
            else settings.CHUNK_OVERLAP_TOKENS
        )
        self.min_chunk_tokens = min_chunk_tokens

    def chunk_document(self, document: CleanedDocument) -> ChunkedDocument:
        """
        Process a CleanedDocument into a structured ChunkedDocument.
        """
        if not document.pages:
            return ChunkedDocument(chunks=[])

        # Step 1: Extract ordered logical units across all pages
        logical_units = self._extract_logical_units(document)
        if not logical_units:
            return ChunkedDocument(chunks=[])

        # Step 2: Detect semantic topic boundaries between adjacent units
        unit_texts = [u.content for u in logical_units]
        semantic_boundaries = self.semantic_splitter.find_semantic_boundaries(
            unit_texts
        )

        # Step 3: Assemble chunks enforcing semantic boundaries and token budget
        raw_chunks = self._assemble_chunks(logical_units, semantic_boundaries)

        # Step 4: Convert to final immutable DocumentChunk objects
        document_chunks: list[DocumentChunk] = []
        for idx, (units, content) in enumerate(raw_chunks):
            pages = sorted(list(set(u.page_number for u in units)))
            block_types = sorted(list(set(u.block_type for u in units)))
            tokens = self.token_counter.count(content)

            document_chunks.append(
                DocumentChunk(
                    chunk_index=idx,
                    content=content,
                    token_count=tokens,
                    start_page=min(pages) if pages else 1,
                    end_page=max(pages) if pages else 1,
                    page_numbers=pages,
                    block_types=block_types,
                )
            )

        logger.info(
            "Document chunking completed",
            total_chunks=len(document_chunks),
            total_tokens=sum(c.token_count for c in document_chunks),
        )

        return ChunkedDocument(chunks=document_chunks)

    def _extract_logical_units(self, document: CleanedDocument) -> list[_TextUnit]:
        """
        Convert CleanedDocument into an ordered list of _TextUnit instances,
        handling image blocks, lists, headings, and oversized fallbacks.
        """
        units: list[_TextUnit] = []

        for page in document.pages:
            for block in page.blocks:
                content = block.content.strip()
                if not content:
                    continue

                if block.block_type == "image":
                    # Multimodal block is kept as a single atomic semantic unit
                    tokens = self.token_counter.count(content)
                    if tokens > self.max_tokens:
                        # Fallback for unusually large image captions
                        sub_texts = self.token_counter.split_by_tokens(
                            content, self.max_tokens
                        )
                        for st in sub_texts:
                            units.append(
                                _TextUnit(
                                    content=st,
                                    page_number=page.page_number,
                                    block_type="image",
                                    token_count=self.token_counter.count(st),
                                    is_intro_header=False,
                                )
                            )
                    else:
                        units.append(
                            _TextUnit(
                                content=content,
                                page_number=page.page_number,
                                block_type="image",
                                token_count=tokens,
                                is_intro_header=False,
                            )
                        )
                else:
                    # Text block: split into paragraphs
                    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                    for p in paragraphs:
                        self._process_paragraph(p, page.page_number, units)

        return units

    def _process_paragraph(
        self, paragraph: str, page_number: int, out_units: list[_TextUnit]
    ) -> None:
        """
        Process a paragraph into logical units, with fallback splitting if oversized.
        """
        tokens = self.token_counter.count(paragraph)
        is_intro = self._is_introductory_header(paragraph)

        # Case 1: Paragraph fits within max_tokens
        if tokens <= self.max_tokens:
            out_units.append(
                _TextUnit(
                    content=paragraph,
                    page_number=page_number,
                    block_type="text",
                    token_count=tokens,
                    is_intro_header=is_intro,
                )
            )
            return

        # Case 2: Oversized paragraph fallback -> Split by sentences
        sentences = split_sentences(paragraph)
        if len(sentences) > 1:
            for sentence in sentences:
                self._process_sentence(sentence, page_number, out_units)
            return

        # Case 3: Single sentence exceeds max_tokens -> Hard token fallback
        token_splits = self.token_counter.split_by_tokens(paragraph, self.max_tokens)
        for ts in token_splits:
            out_units.append(
                _TextUnit(
                    content=ts,
                    page_number=page_number,
                    block_type="text",
                    token_count=self.token_counter.count(ts),
                    is_intro_header=False,
                )
            )

    def _process_sentence(
        self, sentence: str, page_number: int, out_units: list[_TextUnit]
    ) -> None:
        """Process a single sentence, handling fallback if it exceeds max_tokens."""
        tokens = self.token_counter.count(sentence)
        if tokens <= self.max_tokens:
            out_units.append(
                _TextUnit(
                    content=sentence,
                    page_number=page_number,
                    block_type="text",
                    token_count=tokens,
                    is_intro_header=self._is_introductory_header(sentence),
                )
            )
        else:
            token_splits = self.token_counter.split_by_tokens(sentence, self.max_tokens)
            for ts in token_splits:
                out_units.append(
                    _TextUnit(
                        content=ts,
                        page_number=page_number,
                        block_type="text",
                        token_count=self.token_counter.count(ts),
                        is_intro_header=False,
                    )
                )

    def _assemble_chunks(
        self, units: list[_TextUnit], semantic_boundaries: set[int]
    ) -> list[tuple[list[_TextUnit], str]]:
        """
        Group logical units into chunks using semantic boundaries first,
        token budget second, with boundary overlap.
        """
        chunks: list[tuple[list[_TextUnit], str]] = []
        current_units: list[_TextUnit] = []
        current_tokens = 0

        i = 0
        total_units = len(units)

        while i < total_units:
            unit = units[i]

            # If current_units is empty, add unit unconditionally
            if not current_units:
                current_units.append(unit)
                current_tokens = unit.token_count
                i += 1
                continue

            # Check if previous unit had a semantic boundary before this unit
            prev_idx = i - 1
            has_semantic_boundary = prev_idx in semantic_boundaries
            prev_unit = current_units[-1]

            # Never split right after an introductory header or markdown heading
            cannot_split_here = prev_unit.is_intro_header

            # Condition A: Hard max token limit exceeded if unit is added
            exceeds_max = (current_tokens + unit.token_count) > self.max_tokens

            # Condition B: Semantic boundary reached and chunk has meaningful size
            # (preferring target tokens, but respecting natural topic shift)
            semantic_split = (
                has_semantic_boundary
                and not cannot_split_here
                and (
                    current_tokens >= self.min_chunk_tokens
                    or current_tokens >= (self.target_tokens * 0.6)
                )
            )

            # Condition C: Already reached target and next unit pushes even higher
            target_split = (
                not cannot_split_here
                and current_tokens >= self.target_tokens
                and (current_tokens + unit.token_count) > self.target_tokens
            )

            if (exceeds_max or semantic_split or target_split) and current_units:
                # Seal current chunk
                content = self._render_units_content(current_units)
                chunks.append((list(current_units), content))

                # Compute trailing overlap units from current_units
                overlap_units = self._compute_overlap(current_units)

                # Initialize next chunk with overlap
                current_units = list(overlap_units)
                current_tokens = sum(u.token_count for u in current_units)

                # Check if adding unit to overlap would still exceed max_tokens
                if (
                    current_units
                    and (current_tokens + unit.token_count) > self.max_tokens
                ):
                    # Drop overlap to prioritize primary unit fitting
                    current_units = []
                    current_tokens = 0

                current_units.append(unit)
                current_tokens += unit.token_count
                i += 1
            else:
                # Append unit to current chunk
                current_units.append(unit)
                current_tokens += unit.token_count
                i += 1

        if current_units:
            content = self._render_units_content(current_units)
            chunks.append((list(current_units), content))

        return chunks

    def _compute_overlap(self, current_units: list[_TextUnit]) -> list[_TextUnit]:
        """
        Extract complete trailing logical units to serve as context overlap.
        Preserves complete sentence/unit boundaries and respects overlap_tokens limit.
        Does not duplicate the entire chunk.
        """
        if self.overlap_tokens <= 0 or len(current_units) <= 1:
            return []

        overlap_units: list[_TextUnit] = []
        tokens_accumulated = 0

        # Traverse backwards, taking trailing units within the overlap budget
        for u in reversed(current_units):
            # Avoid taking all units of the current chunk
            if len(overlap_units) + 1 >= len(current_units):
                break

            if tokens_accumulated + u.token_count <= self.overlap_tokens:
                overlap_units.insert(0, u)
                tokens_accumulated += u.token_count
            else:
                break

        return overlap_units

    @staticmethod
    def _is_introductory_header(text: str) -> bool:
        """
        Check if text looks like a heading or introductory list marker
        (e.g., 'Benefits:', '## Section', 'Overview').
        """
        s = text.strip()
        if not s:
            return False
        if s.startswith("#"):
            return True
        if s.endswith(":"):
            return True
        # Very short line with title casing and without terminal sentence punctuation
        words = s.split()
        if len(words) <= 5 and not s.endswith((".", "!", "?")):
            return True
        return False

    @staticmethod
    def _render_units_content(units: list[_TextUnit]) -> str:
        """
        Render text units into coherent chunk content preserving natural spacing.
        """
        parts: list[str] = []
        for u in units:
            part = u.content.strip()
            if part:
                parts.append(part)

        return "\n\n".join(parts)
