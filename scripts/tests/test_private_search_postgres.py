"""Real owner/deletion/disable checks; no inference approval or provider calls."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from ingestion_test_support import upload_native
from sqlalchemy import delete, update
from src.models.document import Document
from src.models.user import User
from src.services.private_text_search import private_text_search


@pytest.mark.asyncio
async def test_unapproved_native_pdf_remains_searchable_as_private_literal_evidence(fixture):
    document = await upload_native(fixture)
    async with fixture.sessions() as db:
        result = await private_text_search(db, fixture.storage, document.id, fixture.owner_id, "SS-QUEUE-42", 5)
    assert result["mode"] == "private_text_search" and not result["generated_answer"]
    assert not result["external_provider_used"]
    source = result["results"][0]
    assert source["document_id"] == document.id and source["page_number"] == 1
    assert "SS-QUEUE-42" in source["text"] and len(source["source_sha256"]) == 64
    assert fixture.calls == []


@pytest.mark.asyncio
async def test_other_owner_cannot_trigger_private_storage_or_parsing(fixture):
    document = await upload_native(fixture)
    storage = type("ForbiddenStorage", (), {"download": AsyncMock()})()
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as failure:
            await private_text_search(db, storage, document.id, "other-owner", "PUBLIC", 5)
    assert failure.value.status_code == 404
    storage.download.assert_not_awaited()
    assert fixture.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "disable", "replace"])
async def test_access_and_source_changes_during_download_withhold_all_passages(fixture, change):
    document = await upload_native(fixture)
    async def download(key):
        content = await fixture.storage.download(key)
        async with fixture.sessions() as writer, writer.begin():
            if change == "delete":
                await writer.execute(delete(Document).where(Document.id == document.id))
            elif change == "disable":
                await writer.execute(update(User).where(User.id == fixture.owner_id).values(is_active=False))
            else:
                await writer.execute(update(Document).where(Document.id == document.id).values(storage_key="changed-synthetic-path"))
        return content
    storage = type("ChangedStorage", (), {"download": staticmethod(download)})()
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as failure:
            await private_text_search(db, storage, document.id, fixture.owner_id, "PUBLIC", 5)
    assert failure.value.status_code == {"delete": 404, "disable": 403, "replace": 409}[change]
    assert fixture.calls == []
