"""Dry-run by default. Update legacy payloads only with unambiguous DB ownership."""
import argparse
import asyncio

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import select

from src.config import settings
from src.db import AsyncSessionLocal
from src.models.external_document import ExternalDocument


def plan_payload(payload: dict, documents: list[ExternalDocument]) -> dict | None:
    matches = [doc for doc in documents if (
        doc.source_system == payload.get("source_system", payload.get("client_id"))
        and doc.external_document_id == payload.get("document_id")
        and (payload.get("patient_id") == doc.external_patient_id or payload.get("owner_subject_id") == doc.owner_subject_id)
        and (not payload.get("tenant_id") or payload["tenant_id"] == doc.tenant_id)
    )]
    if len(matches) != 1:
        return None
    doc = matches[0]
    scope = {"client_id": doc.client_id, "tenant_id": doc.tenant_id, "collection_id": doc.collection_id, "owner_subject_id": doc.owner_subject_id}
    if not all(scope.values()):
        return None
    if any(payload.get(key) is not None and payload[key] != value for key, value in scope.items()):
        return None
    return scope


async def run(client_id: str, tenant_id: str, apply: bool = False):
    # Load all records for this client to detect ambiguous legacy IDs across tenants.
    async with AsyncSessionLocal() as session:
        documents = list((await session.execute(select(ExternalDocument).where(ExternalDocument.source_system == client_id))).scalars().all())
    client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    scanned = planned = ambiguous = 0
    try:
        offset = None
        while True:
            points, offset = await client.scroll(collection_name=settings.QDRANT_COLLECTION_NAME, scroll_filter=models.Filter(must=[models.FieldCondition(key="source_system", match=models.MatchValue(value=client_id))]), offset=offset, limit=100, with_payload=True, with_vectors=False)
            for point in points:
                scanned += 1
                payload = point.payload or {}
                scope = plan_payload(payload, documents)
                if scope is None:
                    ambiguous += 1
                    continue
                if scope["tenant_id"] != tenant_id or all(payload.get(k) == v for k, v in scope.items()):
                    continue
                planned += 1
                if apply:
                    await client.set_payload(collection_name=settings.QDRANT_COLLECTION_NAME, points=[point.id], payload=scope, wait=True)
            if offset is None:
                break
    finally:
        await client.close()
    # Counts only: no patient identifiers, content, storage keys, or credentials.
    print(f"scanned={scanned} planned={planned} ambiguous_or_incomplete={ambiguous} applied={apply}")
    if ambiguous:
        raise RuntimeError("Some vectors could not be safely mapped. Review or reindex before switching retrieval contracts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.client_id, args.tenant_id, args.apply))
