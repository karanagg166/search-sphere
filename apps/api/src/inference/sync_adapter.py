"""Bridge existing CPU parsing/chunking interfaces to the guarded async gateway."""

import asyncio
import hashlib
import math
import re
from collections import Counter
from concurrent.futures import TimeoutError as FutureTimeoutError
from io import BytesIO

from PIL import Image, ImageFilter, ImageOps

from src.inference.cohere_gateway import CohereGateway
from src.inference.policy import ProviderError, current_inference_scope
from src.processing.models.sparse_vector import SparseVector


class CohereSyncAdapter:
    def __init__(self, gateway=None):
        self.gateway = gateway or CohereGateway()

    def run(self, operation, *, timeout=60):
        scope = current_inference_scope()
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is scope.loop or not scope.loop.is_running():
            raise ProviderError("Synchronous inference must run in a worker thread.")
        # Schedule DB quota operations on the owning loop, never a new thread loop.
        pending = asyncio.run_coroutine_threadsafe(operation(), scope.loop)
        try:
            return pending.result(timeout=timeout)
        except FutureTimeoutError:
            pending.cancel()
            raise ProviderError("Hosted inference exceeded the operation deadline.") from None

    def embed(self, texts, batch_size=32):
        return self.run(lambda: self.gateway.embed(list(texts)))

    def query_embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        return self.run(lambda: self.gateway.embed(list(texts), query=True))

    def semantic_embed(self, texts):
        return self.run(lambda: self.gateway.semantic_embed(list(texts)), timeout=180)

    def rerank(self, query, documents):
        return self.run(lambda: self.gateway.rerank(query, list(documents)))

    def inspect_image(self, image_bytes: bytes, instruction: str, *, preprocess=False, max_tokens=1024):
        with Image.open(BytesIO(image_bytes)) as source:
            if source.width * source.height > 16_000_000:
                raise ValueError("Image exceeds the processing pixel limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((2048, 2048))
            if preprocess:
                image = ImageOps.autocontrast(ImageOps.grayscale(image))
                image = image.filter(ImageFilter.MedianFilter(size=3))
            buffer = BytesIO()
            image.save(buffer, format="PNG", optimize=True)
        return self.run(lambda: self.gateway.inspect_image(buffer.getvalue(), instruction, max_tokens=max_tokens))


class PostgresLexicalAdapter:
    """Deterministic legacy-interface tokens; PostgreSQL tsvector owns ranking.

    These hashes are not model embeddings or BM25 scores. The pgvector store uses
    original query text with websearch_to_tsquery/ts_rank_cd and RRF instead.
    """

    def embed(self, texts):
        results = []
        for text in texts:
            counts = Counter(re.findall(r"[\w]+", text.casefold()))
            features = {}
            for token, count in counts.items():
                index = int.from_bytes(hashlib.blake2s(token.encode(), digest_size=4).digest(), "big")
                features[index] = features.get(index, 0.0) + math.log1p(count)
            indices = sorted(features)
            results.append(SparseVector(indices, [features[i] for i in indices]))
        return results

    passage_embed = embed

    def query_embed(self, query):
        return self.embed([query])
