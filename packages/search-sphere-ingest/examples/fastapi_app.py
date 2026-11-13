"""Independent FastAPI example; install FastAPI separately in the host project.

Add your own authentication, owner-scoped private storage and request admission
before exposing an upload route. This local example accepts PDF bytes directly.
"""

import asyncio

from fastapi import FastAPI, HTTPException, Request

from search_sphere_ingest import DocumentPipeline, IngestError
from search_sphere_ingest.isolation import extract_isolated

app = FastAPI()
slots = asyncio.Semaphore(1)


@app.post("/extract")
async def extract(request: Request):
    maximum = 20 * 1024 * 1024
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > maximum:
            raise HTTPException(413, "File too large")
        body.extend(part)
    async with slots:
        try:
            doc = await asyncio.to_thread(extract_isolated, bytes(body))
            chunks = await asyncio.to_thread(DocumentPipeline().chunk, doc)
            return {
                "document": doc.model_dump(mode="json"),
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            }
        except IngestError as error:
            raise HTTPException(422, {"code": error.code, "retryable": error.retryable}) from None
