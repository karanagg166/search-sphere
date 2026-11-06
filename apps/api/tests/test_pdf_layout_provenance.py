"""Native layout/provenance survives the canonical processing path, without APIs."""

from io import BytesIO
from types import SimpleNamespace

import pymupdf
from PIL import Image

from src.processing.chunking.document_chunker import DocumentChunker
from src.processing.cleaning.text_cleaner import TextCleaner
from src.processing.embedding.dense_embedder import DenseEmbedder
from src.processing.extraction.document_extractor import DocumentExtractor
from src.processing.extraction.reading_order import reading_order
from src.processing.models.document import ExtractedBlock


def block(label, bbox):
    return {"label": label, "bbox": bbox}


def test_columns_keep_left_column_together_between_wide_header_and_footer():
    blocks = [
        block("right-bottom", (330, 200, 550, 260)),
        block("left-top", (40, 80, 260, 140)),
        block("header", (40, 20, 550, 50)),
        block("right-top", (330, 80, 550, 140)),
        block("left-bottom", (40, 200, 260, 260)),
        block("footer", (40, 700, 550, 720)),
    ]
    assert [b["label"] for b in reading_order(blocks, 612)] == [
        "header", "left-top", "left-bottom", "right-top", "right-bottom", "footer",
    ]


def test_three_columns_and_overlapping_figures_have_deterministic_order():
    columns = [block(str(i), (40 + i * 180, 80, 180 + i * 180, 500)) for i in [2, 0, 1]]
    assert [b["label"] for b in reading_order(columns, 612)] == ["0", "1", "2"]
    overlap = [block("lower", (20, 30, 200, 150)), block("upper", (30, 20, 180, 100))]
    assert [b["label"] for b in reading_order(overlap, 612)] == ["upper", "lower"]


def native_pdf():
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((330, 80), "Right policy details ID-920. Exactly 42 days.")
    page.insert_text((40, 80), "Left policy details ID-120. Exactly 12 weeks.")
    content = document.tobytes()
    document.close()
    return content


def test_native_pdf_retains_source_order_bbox_page_and_hash():
    extractor = DocumentExtractor()
    extracted = extractor.extract(native_pdf())
    blocks = extracted.pages[0].blocks
    assert blocks[0].content.startswith("Left")
    assert blocks[1].content.startswith("Right")
    assert [b.provenance["original_order"] for b in blocks] == [0, 0]
    assert [b.provenance["source_line_indices"] for b in blocks] == [[1], [0]]
    assert all(b.provenance["page_number"] == 1 for b in blocks)
    assert all(len(b.provenance["document_sha256"]) == 64 for b in blocks)
    assert len({b.provenance["block_id"] for b in blocks}) == 2
    assert blocks[0].provenance["bbox"][0] == 40


def test_clean_chunk_embed_preserve_provenance_and_identifiers():
    extracted = DocumentExtractor().extract(native_pdf())
    cleaned = TextCleaner().clean_document(extracted)
    assert cleaned.pages[0].blocks[0].provenance == extracted.pages[0].blocks[0].provenance
    chunker = DocumentChunker(
        semantic_splitter=SimpleNamespace(find_semantic_boundaries=lambda texts: set()),
        target_tokens=500, max_tokens=700, overlap_tokens=60,
    )
    chunked = chunker.chunk_document(cleaned)
    embedded = DenseEmbedder(model=lambda texts: [[1.0] + [0.0] * 383 for _ in texts]).embed_document(chunked)
    assert len(chunked.chunks) == 1
    assert "ID-120" in embedded.chunks[0].content and "ID-920" in embedded.chunks[0].content
    assert "12 weeks" in embedded.chunks[0].content and "42 days" in embedded.chunks[0].content
    assert embedded.chunks[0].provenance == [b.provenance for b in extracted.pages[0].blocks]


def test_image_ocr_and_description_share_original_region_and_review_flag():
    stream = BytesIO()
    Image.new("RGB", (400, 300), "white").save(stream, format="PNG")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(40, 80, 440, 380), stream=stream.getvalue())
    content = document.tobytes()
    document.close()
    extractor = DocumentExtractor(
        ocr_processor=SimpleNamespace(extract_text=lambda image: "Public fixture BP 120/80 [unreadable]"),
        image_captioner=SimpleNamespace(describe=lambda image: "Generated description of the public fixture"),
    )
    image = extractor.extract(content).pages[0].blocks[0]
    assert image.block_type == "image"
    assert "120/80" in image.content and "Image description:" in image.content
    assert image.provenance["bbox"] == [40.0, 80.0, 440.0, 380.0]
    assert image.provenance["ocr_review_required"] and image.provenance["caption_is_description"]


def test_uncertain_ocr_marker_does_not_truncate_later_clinical_values():
    block = ExtractedBlock("image", "[Image text: Name [unreadable]. BP 120/80 mmHg]\n\n[Image description: Public synthetic form]")
    cleaned = TextCleaner().clean_block(block)
    assert "[unreadable]" in cleaned.content and "120/80 mmHg" in cleaned.content
