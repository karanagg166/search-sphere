"""Full evidence lookup only after live document authorization in the caller."""

from fastapi import HTTPException
from sqlalchemy import select

from src.models.document_evidence import DocumentEvidence


async def read_structured_evidence(db, kind, source_record_id, evidence_id):
    row = (await db.execute(select(DocumentEvidence).where(
        DocumentEvidence.document_kind == kind, DocumentEvidence.source_record_id == source_record_id,
        DocumentEvidence.evidence_id == evidence_id,
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Structured evidence not found.")
    return {"evidence_id": row.evidence_id, "kind": row.kind, "source_sha256": row.source_sha256, "data": row.data_json}
