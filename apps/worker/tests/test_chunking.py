from unittest.mock import MagicMock

import pytest

from src.processing.chunking import (
    DocumentChunker,
    SemanticEmbedder,
    SemanticSplitter,
    TokenCounter,
    split_sentences,
)
from src.processing.chunking.semantic_splitter import cosine_distance
from src.processing.models.document import (
    ChunkedDocument,
    CleanedBlock,
    CleanedDocument,
    CleanedPage,
)

# ======================================================================
# 1. TokenCounter Tests
# ======================================================================


def test_token_counter_empty_and_whitespace() -> None:
    counter = TokenCounter()
    assert counter.count("") == 0
    assert counter.count("   \n\t  ") == 0


def test_token_counter_count_accuracy() -> None:
    counter = TokenCounter()
    text = "Hello world! This is a test of token counting."
    tokens = counter.count(text)
    assert tokens > 0
    assert tokens == len(counter.encoding.encode(text))


def test_token_counter_truncate() -> None:
    counter = TokenCounter()
    text = "The quick brown fox jumps over the lazy dog repeatedly and quickly."
    total_tokens = counter.count(text)
    assert total_tokens > 5

    truncated = counter.truncate(text, max_tokens=5)
    assert counter.count(truncated) <= 5
    assert text.startswith(truncated)


def test_token_counter_split_by_tokens_word_aligned() -> None:
    counter = TokenCounter()
    text = "Sentence one here. Sentence two here. Sentence three here."
    # Request max 4 tokens per slice
    segments = counter.split_by_tokens(text, max_tokens=4)
    assert len(segments) > 1
    for seg in segments:
        assert counter.count(seg) <= 4


# ======================================================================
# 2. Sentence Segmentation Tests
# ======================================================================


def test_split_sentences_empty() -> None:
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_split_sentences_abbreviations_and_decimals() -> None:
    text = "Dr. Smith met with Mr. Jones at 2.8% interest rate. They agreed to terms."
    sentences = split_sentences(text)
    assert len(sentences) == 2
    assert sentences[0] == "Dr. Smith met with Mr. Jones at 2.8% interest rate."
    assert sentences[1] == "They agreed to terms."


def test_split_sentences_multimodal_marker_atomic() -> None:
    text = (
        "[Image text: Revenue Q3 28% increase]\n\n"
        "[Image description: A financial bar chart]"
    )
    # Multimodal blocks must not be fragmented into multiple sentences
    sentences = split_sentences(text)
    assert len(sentences) == 1
    assert "[Image text: Revenue Q3 28% increase]" in sentences[0]
    assert "[Image description: A financial bar chart]" in sentences[0]


def test_split_sentences_list_items_preserved() -> None:
    list_text = "- Item one\n- Item two\n- Item three"
    items = split_sentences(list_text)
    assert len(items) == 3
    assert items[0] == "- Item one"
    assert items[1] == "- Item two"
    assert items[2] == "- Item three"


# ======================================================================
# 3. Semantic Similarity & Boundary Detection Tests
# ======================================================================


def test_cosine_distance_identical_and_orthogonal() -> None:
    # Identical vectors: distance should be 0.0
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [1.0, 0.0, 0.0]
    assert pytest.approx(cosine_distance(vec_a, vec_b), abs=1e-5) == 0.0

    # Orthogonal vectors: distance should be 1.0
    vec_c = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_distance(vec_a, vec_c), abs=1e-5) == 1.0


def test_semantic_splitter_detects_topic_shift(
    mock_semantic_embedder: MagicMock,
) -> None:
    # Simulate: Unit 0 & 1 on Topic A, Unit 2 & 3 on Topic B
    topic_a = [1.0, 0.0, 0.0]
    topic_b = [0.0, 1.0, 0.0]

    mock_semantic_embedder.embed.side_effect = None
    mock_semantic_embedder.embed.return_value = [topic_a, topic_a, topic_b, topic_b]

    splitter = SemanticSplitter(
        embedder=mock_semantic_embedder,
        distance_threshold=0.5,
        percentile_threshold=None,
    )

    units = ["Topic A line 1", "Topic A line 2", "Topic B line 1", "Topic B line 2"]
    boundaries = splitter.find_semantic_boundaries(units)

    # Topic shift occurs between index 1 and index 2
    assert 1 in boundaries
    assert 0 not in boundaries
    assert 2 not in boundaries


# ======================================================================
# 4. DocumentChunker Pipeline Tests
# ======================================================================


def test_empty_cleaned_document(mock_semantic_embedder: MagicMock) -> None:
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder)
    )
    empty_doc = CleanedDocument(pages=[])
    chunked = chunker.chunk_document(empty_doc)
    assert isinstance(chunked, ChunkedDocument)
    assert chunked.total_chunks() == 0
    assert chunked.chunks == []


def test_one_short_paragraph(mock_semantic_embedder: MagicMock) -> None:
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder)
    )
    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text",
                        content=(
                            "This is a single short paragraph discussing vector"
                            " databases."
                        ),
                    )
                ],
            )
        ]
    )
    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() == 1
    c = chunked.chunks[0]
    assert c.chunk_index == 0
    assert "discussing vector databases" in c.content
    assert c.start_page == 1
    assert c.end_page == 1
    assert c.page_numbers == [1]
    assert c.block_types == ["text"]
    assert c.token_count > 0


def test_multiple_paragraphs_under_target_size(
    mock_semantic_embedder: MagicMock,
) -> None:
    # All sentences share the same topic (no semantic boundary)
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(
            embedder=mock_semantic_embedder,
            distance_threshold=0.5,
        ),
        target_tokens=500,
        max_tokens=700,
    )

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text",
                        content=(
                            "Paragraph one discusses architecture.\n\n"
                            "Paragraph two continues the overview.\n\n"
                            "Paragraph three provides technical details."
                        ),
                    )
                ],
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    # Total tokens is small (~25 tokens), well under target_tokens (500)
    assert chunked.total_chunks() == 1
    assert "Paragraph one discusses architecture." in chunked.chunks[0].content
    assert "Paragraph three provides technical details." in chunked.chunks[0].content


def test_semantically_different_sections(mock_semantic_embedder: MagicMock) -> None:
    # 2 sections with orthogonal embedding vectors (distance = 1.0 > 0.5)
    def mock_embed(texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for t in texts:
            if "Quantum computing" in t:
                results.append([1.0, 0.0])
            else:
                results.append([0.0, 1.0])
        return results

    mock_semantic_embedder.embed.side_effect = mock_embed

    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(
            embedder=mock_semantic_embedder,
            distance_threshold=0.5,
            percentile_threshold=None,
        ),
        target_tokens=500,
        max_tokens=700,
        overlap_tokens=0,
        min_chunk_tokens=10,
    )

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text",
                        content=(
                            "Quantum computing relies on qubits, superposition, "
                            "and quantum entanglement principles.\n\n"
                            "Italian pasta recipes traditionally require semolina "
                            "flour, fresh eggs, salt, and tomato sauce."
                        ),
                    )
                ],
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() == 2
    assert "Quantum computing" in chunked.chunks[0].content
    assert "Italian pasta recipes" in chunked.chunks[1].content
    assert "Italian pasta recipes" not in chunked.chunks[0].content


def test_semantically_similar_neighboring_sentences(
    mock_semantic_embedder: MagicMock,
) -> None:
    # Neighboring sentences have near-identical vectors (distance = 0.0)
    mock_semantic_embedder.embed.side_effect = lambda texts: [[0.5, 0.5] for _ in texts]

    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(
            embedder=mock_semantic_embedder,
            distance_threshold=0.5,
        ),
        target_tokens=500,
        max_tokens=700,
    )

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text",
                        content=(
                            "Gradient descent optimizes parameters. The learning "
                            "rate controls step size. Momentum accelerates "
                            "convergence."
                        ),
                    )
                ],
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() == 1
    assert "Gradient descent" in chunked.chunks[0].content
    assert "Momentum accelerates convergence" in chunked.chunks[0].content


def test_hard_maximum_token_enforcement(mock_semantic_embedder: MagicMock) -> None:
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    # Target: 50 tokens, Max: 70 tokens
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder),
        target_tokens=50,
        max_tokens=70,
        overlap_tokens=10,
    )

    # 10 paragraphs, each ~15 tokens
    paragraphs = [
        (
            f"This is paragraph number {i} detailing system specifications "
            "and distributed database configurations."
        )
        for i in range(10)
    ]
    content = "\n\n".join(paragraphs)

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1, blocks=[CleanedBlock(block_type="text", content=content)]
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() > 1

    for chunk in chunked.chunks:
        assert chunk.token_count <= 70, (
            f"Chunk {chunk.chunk_index} exceeded max_tokens: {chunk.token_count}"
        )


def test_oversized_paragraph_fallback(mock_semantic_embedder: MagicMock) -> None:
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    # Max tokens = 30
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder),
        target_tokens=25,
        max_tokens=30,
        overlap_tokens=0,
    )

    # Single paragraph with multiple sentences exceeding 30 tokens
    sentences = [
        "First sentence provides initial context for storage.",
        "Second sentence explains how replication ensures tolerance.",
        "Third sentence details how consensus coordinates election.",
        "Fourth sentence summarizes read and write performance.",
    ]
    huge_paragraph = " ".join(sentences)

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[CleanedBlock(block_type="text", content=huge_paragraph)],
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() >= 2
    for chunk in chunked.chunks:
        assert chunk.token_count <= 30


def test_overlap_behavior_and_sentence_preservation(
    mock_semantic_embedder: MagicMock,
) -> None:
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    # Target: 15, Max: 20, Overlap: 10
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder),
        target_tokens=15,
        max_tokens=20,
        overlap_tokens=10,
    )

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text",
                        content=(
                            "Sentence Alpha sets up the experiment.\n\n"
                            "Sentence Beta explains methodology.\n\n"
                            "Sentence Gamma shows initial results.\n\n"
                            "Sentence Delta concludes findings."
                        ),
                    )
                ],
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() >= 2

    # Chunk 0 has trailing content that overlaps into Chunk 1
    chunk0_content = chunked.chunks[0].content
    chunk1_content = chunked.chunks[1].content

    # An entire sentence from chunk 0 must be present in chunk 1 (not cut in half)
    overlapping_sentences = [
        s
        for s in [
            "Sentence Alpha sets up the experiment.",
            "Sentence Beta explains methodology.",
            "Sentence Gamma shows initial results.",
        ]
        if s in chunk0_content and s in chunk1_content
    ]
    assert len(overlapping_sentences) >= 1
    # Check that words are not sliced
    assert not any(w.endswith("...") for w in chunk1_content.split())


def test_image_text_and_description_remain_together(
    mock_semantic_embedder: MagicMock,
) -> None:
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder),
        target_tokens=500,
        max_tokens=700,
    )

    image_content = (
        "[Image text: Revenue increased 28% in Q4]\n\n"
        "[Image description: A bar chart comparing quarterly revenue for 2026]"
    )

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="image",
                        content=image_content,
                    )
                ],
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() == 1
    chunk = chunked.chunks[0]
    assert "[Image text: Revenue increased 28% in Q4]" in chunk.content
    assert (
        "[Image description: A bar chart comparing quarterly revenue for 2026]"
        in chunk.content
    )
    assert chunk.block_types == ["image"]


def test_list_structure_is_preserved(mock_semantic_embedder: MagicMock) -> None:
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder),
        target_tokens=500,
        max_tokens=700,
    )

    list_content = "Benefits:\n- Fast retrieval\n- Fault tolerance\n- Semantic matching"

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text",
                        content=list_content,
                    )
                ],
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() == 1
    chunk = chunked.chunks[0]
    # Header and all items stay in the exact same chunk
    assert "Benefits:" in chunk.content
    assert "- Fast retrieval" in chunk.content
    assert "- Fault tolerance" in chunk.content
    assert "- Semantic matching" in chunk.content


def test_page_order_and_spans(mock_semantic_embedder: MagicMock) -> None:
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    # Large target so pages 1 and 2 combine into chunk 0
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder),
        target_tokens=500,
        max_tokens=700,
    )

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text", content="Content starting on page one."
                    )
                ],
            ),
            CleanedPage(
                page_number=2,
                blocks=[
                    CleanedBlock(
                        block_type="text", content="Content continuing onto page two."
                    )
                ],
            ),
            CleanedPage(
                page_number=3,
                blocks=[
                    CleanedBlock(
                        block_type="image",
                        content="[Image description: Diagram on page three]",
                    )
                ],
            ),
        ]
    )

    chunked = chunker.chunk_document(doc)
    assert chunked.total_chunks() == 1
    c = chunked.chunks[0]
    assert c.start_page == 1
    assert c.end_page == 3
    assert c.page_numbers == [1, 2, 3]
    assert set(c.block_types) == {"text", "image"}


def test_deterministic_chunk_ordering(mock_semantic_embedder: MagicMock) -> None:
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder),
        target_tokens=20,
        max_tokens=40,
        overlap_tokens=5,
    )

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text",
                        content=(
                            "Paragraph A is here.\n\n"
                            "Paragraph B is here.\n\n"
                            "Paragraph C is here.\n\n"
                            "Paragraph D is here."
                        ),
                    )
                ],
            )
        ]
    )

    run_1 = chunker.chunk_document(doc)
    run_2 = chunker.chunk_document(doc)

    assert run_1.total_chunks() == run_2.total_chunks()
    for idx in range(run_1.total_chunks()):
        assert run_1.chunks[idx].chunk_index == idx
        assert run_1.chunks[idx].chunk_index == run_2.chunks[idx].chunk_index
        assert run_1.chunks[idx].content == run_2.chunks[idx].content
        assert run_1.chunks[idx].token_count == run_2.chunks[idx].token_count
        assert run_1.chunks[idx].start_page == run_2.chunks[idx].start_page
        assert run_1.chunks[idx].end_page == run_2.chunks[idx].end_page


def test_no_external_model_download_during_unit_tests() -> None:
    """
    Guarantees that SentenceTransformer is never called or downloaded during unit tests.
    """
    mock_model = MagicMock()
    mock_model.encode.return_value = [[1.0, 0.0], [0.0, 1.0]]

    # Pass mock_model into SemanticEmbedder
    embedder = SemanticEmbedder(model=mock_model)
    embeddings = embedder.embed(["Hello", "World"])

    assert len(embeddings) == 2
    mock_model.encode.assert_called_once()

    # Clear cache test
    SemanticEmbedder._clear_cache()
    assert SemanticEmbedder._cached_model is None


def test_heading_not_orphaned_at_chunk_end(mock_semantic_embedder: MagicMock) -> None:
    """
    Verifies that introductory headings are never left as an orphan at the end
    of a chunk, but stay with following content.
    """
    mock_semantic_embedder.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]

    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder),
        target_tokens=10,
        max_tokens=25,
        overlap_tokens=0,
    )

    doc = CleanedDocument(
        pages=[
            CleanedPage(
                page_number=1,
                blocks=[
                    CleanedBlock(
                        block_type="text",
                        content=(
                            "Introductory paragraph.\n\n"
                            "# Key Features\n\n"
                            "Detail line A."
                        ),
                    )
                ],
            )
        ]
    )

    chunked = chunker.chunk_document(doc)
    # Check that '# Key Features' is not orphaned or severed from Detail line A
    for c in chunked.chunks:
        if "# Key Features" in c.content:
            assert "Detail line A." in c.content


def test_semantic_splitter_percentile_threshold(
    mock_semantic_embedder: MagicMock,
) -> None:
    """
    Verifies statistical percentile threshold calculation in SemanticSplitter.
    """
    mock_semantic_embedder.embed.side_effect = None
    # 5 units with distances: 0.1, 0.1, 0.9 (spike), 0.1
    # Topic A, Topic A, Topic A, Topic B, Topic B
    v1 = [1.0, 0.0]
    v2 = [0.0, 1.0]
    mock_semantic_embedder.embed.return_value = [v1, v1, v1, v2, v2]

    splitter = SemanticSplitter(
        embedder=mock_semantic_embedder,
        distance_threshold=0.3,
        percentile_threshold=75.0,
    )

    units = ["Line 1", "Line 2", "Line 3", "Line 4", "Line 5"]
    boundaries = splitter.find_semantic_boundaries(units)
    # Boundary occurs between Line 3 and Line 4 (index 2)
    assert boundaries == {2}


def test_token_counter_single_oversized_word() -> None:
    """
    Verifies fallback when a single contiguous string without spaces exceeds max_tokens.
    """
    counter = TokenCounter()
    long_word = "a" * 100
    segments = counter.split_by_tokens(long_word, max_tokens=10)
    assert len(segments) > 1
    for seg in segments:
        assert counter.count(seg) <= 10
