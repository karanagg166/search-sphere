"""Public generated tables: native extraction, duplicate exclusion, row boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.processing.chunking.structured_chunks import table_chunks
from src.processing.cleaning.text_cleaner import TextCleaner
from src.processing.extraction.document_extractor import DocumentExtractor
from src.processing.extraction.native_tables import continue_tables
from src.processing.models.document import ExtractedBlock, ExtractedPage
from tests.binary_fixtures import table_pdf


def test_native_table_preserves_headers_units_rows_caption_and_excludes_duplicate_text():
    extracted = DocumentExtractor().extract(table_pdf())
    tables = [block for block in extracted.pages[0].blocks if block.block_type == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.structured["headers"] == ["Item", "Value (mg)"]
    assert table.structured["rows"] == [["A-101", "120"], ["B-102", "80"], ["C-103", "42"]]
    assert table.structured["units"] == ["mg"]
    assert table.structured["row_pages"] == [1, 1, 1]
    assert table.structured["caption"] == "Table 1: Public synthetic units"
    prose = " ".join(block.content for block in extracted.pages[0].blocks if block.block_type == "text")
    assert "A-101" not in prose and "120" not in prose and "Table 1" not in prose
    assert "Public prose" in prose
    cleaned = TextCleaner().clean_document(extracted)
    assert next(block for block in cleaned.pages[0].blocks if block.block_type == "table").structured == table.structured


@pytest.mark.asyncio
async def test_small_table_is_one_chunk_with_full_header():
    table = next(block for block in DocumentExtractor().extract(table_pdf()).pages[0].blocks if block.block_type == "table")
    gateway = SimpleNamespace(token_count=AsyncMock(return_value=100))
    chunks = await table_chunks(table, 1, gateway)
    assert len(chunks) == 1
    assert all(value in chunks[0].content for value in ["Item | Value (mg)", "A-101 | 120", "C-103 | 42"])
    assert chunks[0].provenance[0]["row_start"] == 0 and chunks[0].provenance[0]["row_end"] == 3


@pytest.mark.asyncio
async def test_large_table_repeats_headers_splits_rows_and_keeps_stable_id():
    table = next(block for block in DocumentExtractor().extract(table_pdf()).pages[0].blocks if block.block_type == "table")
    table.structured["rows"] = [[f"PUBLIC-ROW-{index}", str(index)] for index in range(100)]
    gateway = SimpleNamespace(token_count=AsyncMock(side_effect=lambda text, **kwargs: len(text)))
    chunks = await table_chunks(table, 1, gateway)
    assert len(chunks) > 1 and all(chunk.token_count <= 512 for chunk in chunks)
    assert all("Item | Value (mg)" in chunk.content for chunk in chunks)
    assert {chunk.provenance[0]["evidence_id"] for chunk in chunks} == {table.structured["evidence_id"]}
    ranges = [(chunk.provenance[0]["row_start"], chunk.provenance[0]["row_end"]) for chunk in chunks]
    assert ranges[0][0] == 0 and ranges[-1][1] == 100
    assert all(before[1] == after[0] for before, after in zip(ranges, ranges[1:], strict=False))


@pytest.mark.asyncio
async def test_unsplittable_row_fails_for_review_without_truncation():
    table = next(block for block in DocumentExtractor().extract(table_pdf()).pages[0].blocks if block.block_type == "table")
    table.structured["rows"] = [["PUBLIC-LONG-ROW", "x" * 1000]]
    gateway = SimpleNamespace(token_count=AsyncMock(return_value=1000))
    with pytest.raises(ValueError, match="review required"):
        await table_chunks(table, 1, gateway)


def continuation(page, bbox, headers=None, edges=None, caption=""):
    data = {
        "evidence_id": f"table-{page}", "kind": "table", "headers": headers or ["ID", "Value"], "rows": [[str(page), "42"]],
        "row_pages": [page], "regions": [{"page_number": page, "bbox": bbox}], "pages": [page],
        "column_edges": edges or [40, 200, 350], "page_width": 600, "page_height": 800, "caption": caption, "row_offset": 0, "extraction_method": "native_pdf_table",
    }
    return ExtractedPage(page, [ExtractedBlock("table", "PUBLIC", {}, data)])


def test_continuation_requires_adjacent_pages_matching_headers_columns_and_margins():
    first = continuation(1, [40, 500, 350, 790])
    second = continuation(2, [40, 40, 350, 300])
    continue_tables([first, second])
    assert second.blocks[0].structured["evidence_id"] == "table-1"
    assert second.blocks[0].structured["row_offset"] == 1
    for other in [
        continuation(3, [40, 40, 350, 300]), continuation(2, [40, 300, 350, 600]),
        continuation(2, [40, 40, 350, 300], headers=["Name", "Total"]),
        continuation(2, [40, 40, 350, 300], edges=[40, 250, 400]),
        continuation(2, [40, 40, 350, 300], caption="Table 9: Different table"),
    ]:
        continue_tables([first, other])
        assert other.blocks[0].structured["evidence_id"] != "table-1"
