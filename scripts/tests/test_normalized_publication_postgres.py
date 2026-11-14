"""Real PDF -> native package -> leased PostgreSQL publication and owned evidence.

Uses the existing synthetic provider fixture: validates integration/transactions,
not live inference availability or embedding retrieval quality.
"""

from io import BytesIO

import pytest
from fastapi import UploadFile
from ingestion_test_support import approve
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import select
from src.models.document_chunk import DocumentChunk
from src.models.document_evidence import DocumentEvidence
from src.models.ingestion_job import IngestionJob
from src.models.user import User
from src.services.document_deletion import delete_registered_document
from src.services.document_service import DocumentService
from src.services.ingestion_worker import PostgresIngestionWorker
from tests.binary_fixtures import table_pdf


@pytest.mark.asyncio
async def test_normalized_pipeline_publishes_full_sources_and_scoped_links(
    fixture, monkeypatch
):
    await approve(fixture, subject=fixture.owner_id)
    async with fixture.sessions() as db:
        document = await DocumentService(db, fixture.storage).upload_document(
            UploadFile(filename="public-native-table.pdf", file=BytesIO(table_pdf())),
            await db.get(User, fixture.owner_id),
        )
    worker = PostgresIngestionWorker(
        fixture.sessions, storage_factory=lambda: fixture.storage
    )
    assert await worker.run_once()
    async with fixture.sessions() as db:
        job = (
            await db.execute(
                select(IngestionJob).where(IngestionJob.document_id == document.id)
            )
        ).scalar_one()
        assert job.status == "SUCCEEDED", job.error_code
        objects = (
            (
                await db.execute(
                    select(DocumentEvidence).where(
                        DocumentEvidence.source_record_id == document.id
                    )
                )
            )
            .scalars()
            .all()
        )
        chunks = (
            (
                await db.execute(
                    select(DocumentChunk).where(
                        DocumentChunk.document_id == document.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert objects and chunks
        by_id = {item.evidence_id: item for item in objects}
        table = next(item for item in objects if item.kind == "table")
        assert table.data_json["rows"][0] == ["A-101", "120"]
        assert table.data_json["cells"] and table.data_json["sources"]
        assert all(item.tenant_id == fixture.owner_id for item in objects)
        for chunk in chunks:
            for ref in chunk.metadata_json["provenance"]:
                assert ref["evidence_id"] in by_id
                assert ref["pipeline_version"] == "0.3.0"
        await delete_registered_document(
            db, "standalone", document.id, "search_sphere", fixture.owner_id
        )
        await db.commit()
        assert (
            not (
                await db.execute(
                    select(DocumentEvidence).where(
                        DocumentEvidence.source_record_id == document.id
                    )
                )
            )
            .scalars()
            .all()
        )
