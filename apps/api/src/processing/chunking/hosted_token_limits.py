"""Verify final evidence against the actual hosted embedding tokenizer."""

from dataclasses import replace

from src.inference.cohere_gateway import EMBEDDING_TOKEN_LIMIT, CohereGateway
from src.processing.models.document import ChunkedDocument


async def fit_hosted_chunks(document: ChunkedDocument, gateway=None) -> ChunkedDocument:
    gateway = gateway or CohereGateway()
    fitted = []

    async def fit(chunk, text, depth=0):
        if depth > 12 or len(fitted) >= 1000:
            raise ValueError("Document exceeds bounded hosted chunk limits")
        count = await gateway.token_count(text, background=True)
        if count <= EMBEDDING_TOKEN_LIMIT:
            fitted.append(replace(chunk, chunk_index=len(fitted), content=text, token_count=count))
            return
        middle = len(text) // 2
        candidates = [text.rfind(separator, len(text) // 4, middle + 1) for separator in ("\n\n", "\n", " ")]
        split = max(candidates)
        if split <= 0:
            split = middle
        left, right = text[:split].strip(), text[split:].strip()
        if not left or not right:
            raise ValueError("Evidence cannot fit within the hosted token limit")
        await fit(chunk, left, depth + 1)
        await fit(chunk, right, depth + 1)

    for chunk in document.chunks:
        await fit(chunk, chunk.content)
    return ChunkedDocument(fitted)
