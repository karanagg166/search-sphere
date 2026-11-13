"""Write-ahead private crop cleanup intents survive interruption before publication."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from src.models.document_deletion import DocumentDeletion
from src.services.ingestion_source import IngestionSourceError, load_ingestion_source


async def stage_derived_assets(sessions, queue, lease, source, extracted, storage, guard):
    total = sum(len(block.crop or b"") for page in extracted.pages for block in page.blocks)
    if total > 8 * 1024 * 1024:
        raise IngestionSourceError("derived_assets_too_large")
    for page in extracted.pages:
        for index, block in enumerate(page.blocks):
            if block.crop is None:
                continue
            await guard()
            asset_id = str(uuid.uuid4())
            key = f"derived/{source.kind}/{source.internal_id}/{asset_id}.png"
            async with sessions() as db, db.begin():
                if await queue.lock_lease(db, lease) is None or await load_ingestion_source(db, source.kind, source.internal_id) != source:
                    raise IngestionSourceError("document_removed")
                db.add(DocumentDeletion(
                    id=asset_id, kind="asset", document_id=asset_id, source_record_id=source.internal_id,
                    client_id=source.client_id, tenant_id=source.tenant_id, collection_id=source.collection_id,
                    subject_id=source.subject_id, storage_key=key, status="STAGED", lease_token=lease.token,
                    available_at=datetime.now(UTC) + timedelta(seconds=180),
                ))
            try:
                await storage.upload(key, block.crop, "image/png")
            except Exception:
                raise IngestionSourceError("storage_unavailable", retryable=True) from None
            await guard()
            structured = dict(block.structured)
            if structured:
                structured.update({"asset_id": asset_id, "asset_ids": [asset_id]})
                structured["regions"] = [{**region, "asset_id": asset_id} for region in structured.get("regions", [])]
            page.blocks[index] = replace(
                block, provenance={**block.provenance, "asset_id": asset_id},
                structured=structured, crop=None,
            )
    resolve_source_images(extracted)


def resolve_source_images(extracted):
    # Logical tables can span several crops. Resolve after every segment is staged
    # so a later-page source never points to the first page's image.
    assets = {block.provenance["block_id"]: block.provenance["asset_id"]
              for page in extracted.pages for block in page.blocks if "asset_id" in block.provenance}
    for page in extracted.pages:
        for block in page.blocks:
            for data in (block.structured, block.structured.get("normalized", {})):
                if not data:
                    continue
                data["sources"] = [{**ref, "source_image": assets.get(ref["region_id"], ref.get("source_image"))}
                                   for ref in data.get("sources", [])]


def attach_asset_references(processed):
    assets = {block.provenance["block_id"]: block.provenance["asset_id"]
              for page in processed.extracted.pages for block in page.blocks if "asset_id" in block.provenance}
    processed.embedded = replace(processed.embedded, chunks=[replace(chunk, provenance=[
        {**item, **({"asset_id": assets[item["block_id"]]} if item.get("block_id") in assets else {}),
         "sources": [{**ref, "source_image": assets.get(ref["region_id"], ref.get("source_image"))}
                     for ref in item.get("sources", [])]}
        for item in chunk.provenance
    ]) for chunk in processed.embedded.chunks])
