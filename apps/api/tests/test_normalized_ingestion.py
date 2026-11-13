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
