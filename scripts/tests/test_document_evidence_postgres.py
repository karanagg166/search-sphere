"""Full native table and retrieval row groups publish/delete together in PostgreSQL."""

from io import BytesIO

import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient
from ingestion_test_support import approve
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import func, select
from src.db import get_db
from src.main import app
from src.models.document_chunk import DocumentChunk
from src.models.document_evidence import DocumentEvidence
from src.models.user import User
from src.security.jwt import get_current_user
from src.services.document_deletion import delete_registered_document
from src.services.document_service import DocumentService
from src.services.ingestion_worker import PostgresIngestionWorker
from tests.binary_fixtures import table_pdf


@pytest.mark.asyncio
async def test_table_full_data_chunk_links_reindex_and_deletion_are_atomic(fixture):
    await approve(fixture, subject=fixture.owner_id)
    async with fixture.sessions() as db:
        doc = await DocumentService(db, fixture.storage).upload_document(
            UploadFile(filename="public-table.pdf", file=BytesIO(table_pdf())), await db.get(User, fixture.owner_id),
        )
    worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
    assert await worker.run_once()
    async with fixture.sessions() as db:
        evidence = (await db.execute(select(DocumentEvidence).where(DocumentEvidence.source_record_id == doc.id))).scalar_one()
        assert evidence.data_json["headers"] == ["Item", "Value (mg)"]
        assert evidence.data_json["rows"] == [["A-101", "120"], ["B-102", "80"], ["C-103", "42"]]
        assert evidence.tenant_id == fixture.owner_id and evidence.subject_id == fixture.owner_id
        chunks = (await db.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc.id))).scalars().all()
        table_chunks = [chunk for chunk in chunks if chunk.block_types == ["table"]]
        assert len(table_chunks) == 1
        assert table_chunks[0].metadata_json["provenance"][0]["evidence_id"] == evidence.evidence_id
        assert not any("A-101" in chunk.content for chunk in chunks if chunk not in table_chunks)
        async def database():
            async with fixture.sessions() as session:
                yield session
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: User(id=fixture.owner_id)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                path = f"/documents/{doc.id}/evidence/{evidence.evidence_id}"
                response = await client.get(path)
                assert response.status_code == 200 and response.json()["data"]["rows"][0] == ["A-101", "120"]
                app.dependency_overrides[get_current_user] = lambda: User(id="another-owner")
                assert (await client.get(path)).status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
        await DocumentService(db, fixture.storage).reindex_user_document(doc.id, fixture.owner_id)
        original_id = evidence.evidence_id
    assert await worker.run_once()
    async with fixture.sessions() as db:
        evidence = (await db.execute(select(DocumentEvidence).where(DocumentEvidence.source_record_id == doc.id))).scalar_one()
        assert evidence.evidence_id == original_id
        await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
        await db.commit()
        assert (await db.execute(select(func.count()).select_from(DocumentEvidence).where(DocumentEvidence.source_record_id == doc.id))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == doc.id))).scalar_one() == 0
