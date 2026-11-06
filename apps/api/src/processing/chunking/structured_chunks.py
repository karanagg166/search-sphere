"""Keep native tables whole or split only at row boundaries, repeating headers."""

from dataclasses import replace

from src.inference.cohere_gateway import EMBEDDING_TOKEN_LIMIT, CohereGateway
from src.processing.extraction.native_tables import table_text
from src.processing.models.document import ChunkedDocument, DocumentChunk


async def table_chunks(block, page_number, gateway=None):
    gateway = gateway or CohereGateway()
    data = block.structured
    output = []

    async def fit(start, end):
        if len(output) >= 1000:
            raise ValueError("Table exceeds the chunk limit")
        content = table_text(data, data["rows"][start:end])
        if len(content) > 65536:
            count = EMBEDDING_TOKEN_LIMIT + 1
        else:
            count = await gateway.token_count(content, background=True)
        if count <= EMBEDDING_TOKEN_LIMIT:
            provenance = {**block.provenance, "evidence_id": data["evidence_id"],
                          "row_start": data.get("row_offset", 0) + start, "row_end": data.get("row_offset", 0) + end,
                          "structured_kind": "table"}
            output.append(DocumentChunk(0, content, count, page_number, page_number, [page_number], ["table"], [provenance]))
            return
        if end - start <= 1:
            raise ValueError("Table header and row exceed the embedding limit; review required")
        middle = (start + end) // 2
        await fit(start, middle)
        await fit(middle, end)

    await fit(0, len(data["rows"]))
    return output


async def chunk_with_structures(document, chunker, gateway=None):
    """Prose keeps semantic chunking; structured evidence cannot mix with prose."""
    import asyncio

    from src.processing.chunking.hosted_token_limits import fit_hosted_chunks
    from src.processing.models.document import CleanedDocument, CleanedPage

    output, prose = [], []

    async def flush_prose():
        if prose:
            coarse = await asyncio.to_thread(chunker.chunk_document, CleanedDocument(list(prose)))
            output.extend((await fit_hosted_chunks(coarse, gateway)).chunks)
            prose.clear()

    for page in document.pages:
        for block in page.blocks:
            if block.block_type == "table":
                await flush_prose()
                output.extend(await table_chunks(block, page.page_number, gateway))
            else:
                if not prose or prose[-1].page_number != page.page_number:
                    prose.append(CleanedPage(page.page_number, []))
                prose[-1].blocks.append(block)
    await flush_prose()
    return ChunkedDocument([replace(chunk, chunk_index=index) for index, chunk in enumerate(output)])
