"""Full evidence lookup only after live document authorization in the caller."""

from fastapi import HTTPException
from sqlalchemy import select

from src.models.document_deletion import DocumentDeletion
from src.models.document_evidence import DocumentEvidence


async def read_structured_evidence(db, kind, source_record_id, evidence_id):
    row = (await db.execute(select(DocumentEvidence).where(
        DocumentEvidence.document_kind == kind, DocumentEvidence.source_record_id == source_record_id,
        DocumentEvidence.evidence_id == evidence_id,
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Structured evidence not found.")
    return {"evidence_id": row.evidence_id, "kind": row.kind, "source_sha256": row.source_sha256, "data": row.data_json}


async def structured_crop_access(db, storage, kind, source_record_id, evidence_id, asset_id=None):
    evidence = await read_structured_evidence(db, kind, source_record_id, evidence_id)
    allowed = evidence["data"].get("asset_ids", [])
    asset_id = asset_id or evidence["data"].get("asset_id")
    if not asset_id or asset_id not in allowed:
        raise HTTPException(status_code=404, detail="Source crop not available.")
    row = (await db.execute(select(DocumentDeletion).where(
        DocumentDeletion.id == asset_id, DocumentDeletion.kind == "asset",
        DocumentDeletion.source_record_id == source_record_id, DocumentDeletion.status == "PUBLISHED",
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Source crop not available.")
    return {"url": await storage.create_signed_url(row.storage_key, expires_in=300), "expires_in": 300}
