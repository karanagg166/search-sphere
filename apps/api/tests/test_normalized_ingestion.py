"""Application boundary checks; fake tokenizer transport is not live inference evidence."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tiktoken

from src.processing.cleaning.text_cleaner import TextCleaner
from src.processing.extraction.document_extractor import DocumentExtractor
from src.services.package_chunking import chunk_extracted
from tests.binary_fixtures import table_pdf


def test_shared_package_preserves_source_links_and_512_token_contract():
    extracted = DocumentExtractor().extract(table_pdf())
    cleaned = TextCleaner().clean_document(extracted)
    tokenizer = tiktoken.get_encoding("cl100k_base")
    gateway = SimpleNamespace(token_count=AsyncMock(side_effect=lambda text, **kw: len(tokenizer.encode(text))))
    adapter = SimpleNamespace(gateway=gateway, run=lambda operation: asyncio.run(operation()))
    with patch("src.services.package_chunking.CohereSyncAdapter", return_value=adapter):
        chunked = chunk_extracted(cleaned, manifest_target=extracted)
    evidence = {block.structured["evidence_id"]: block.structured for page in extracted.pages for block in page.blocks}
    assert chunked.chunks and gateway.token_count.await_count
    assert all(chunk.token_count <= 480 for chunk in chunked.chunks)
    assert all(item["evidence_id"] in evidence for chunk in chunked.chunks for item in chunk.provenance)
    assert all(len(key) <= 64 for key in evidence)
    assert any(data.get("cells") for data in evidence.values())


def test_cross_page_source_images_use_their_actual_segment_crop():
    from src.processing.models.document import (
        ExtractedBlock,
        ExtractedDocument,
        ExtractedPage,
    )
    from src.services.derived_assets import resolve_source_images

    refs = [{"region_id": "page-one", "source_image": None}, {"region_id": "page-two", "source_image": None}]
    first = ExtractedBlock("table", "", {"block_id": "page-one", "asset_id": "asset-one"},
                           {"sources": refs, "normalized": {"sources": refs}})
    second = ExtractedBlock("table", "", {"block_id": "page-two", "asset_id": "asset-two"})
    doc = ExtractedDocument([ExtractedPage(1, [first]), ExtractedPage(2, [second])])
    resolve_source_images(doc)
    assert [ref["source_image"] for ref in first.structured["sources"]] == ["asset-one", "asset-two"]
    assert first.structured["normalized"]["sources"] == first.structured["sources"]
    assert second.structured == {}


def test_cross_page_table_retains_all_original_segment_cells():
    import pymupdf

    with pymupdf.open(stream=table_pdf(), filetype="pdf") as original, pymupdf.open() as pdf:
        for shift in (620, -60):
            page = pdf.new_page(width=595, height=842)
            page.show_pdf_page(pymupdf.Rect(0, shift, 595, shift + 842), original, 0)
        extracted = DocumentExtractor().extract(pdf.tobytes())
    cleaned = TextCleaner().clean_document(extracted)
    gateway = SimpleNamespace(token_count=AsyncMock(side_effect=lambda text, **kw: len(text.split())))
    adapter = SimpleNamespace(gateway=gateway, run=lambda operation: asyncio.run(operation()))
    with patch("src.services.package_chunking.CohereSyncAdapter", return_value=adapter):
        chunked = chunk_extracted(cleaned, manifest_target=extracted)
    table = next(block.structured for page in extracted.pages for block in page.blocks if block.block_type == "table")
    assert len(table["segments"]) == 2
    assert [segment["page_number"] for segment in table["segments"]] == [1, 2]
    assert sum(len(segment["data"]["cells"]) for segment in table["segments"]) == 16
    assert len(table["cells"]) == 14  # Repeated header retained in segments, logical grid has one header.
    assert len(table["normalized"]["rows"]) == 6
    assert {ref["page_number"] for ref in table["sources"]} == {1, 2}
    assert len({ref["evidence_id"] for chunk in chunked.chunks if "table" in chunk.block_types for ref in chunk.provenance}) == 1
