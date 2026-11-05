from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from src.services.document_access import private_document_response


def owned_document():
    return SimpleNamespace(
        id="synthetic-document",
        user_id="synthetic-owner",
        filename="public-fixture.pdf",
        storage_key="documents/synthetic.pdf",
        file_url="https://storage/object/public/old.pdf",
        file_size=20,
        mime_type="application/pdf",
        status="uploaded",
        chunk_count=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_private_response_uses_expiring_access_without_changing_stored_provenance():
    document = owned_document()
    storage = SimpleNamespace(
        create_signed_url=AsyncMock(
            return_value="https://storage/signed?token=synthetic"
        )
    )
    response = await private_document_response(document, storage)
    assert response.file_url == "https://storage/signed?token=synthetic"
    assert document.file_url == "https://storage/object/public/old.pdf"
    storage.create_signed_url.assert_awaited_once_with(
        document.storage_key, expires_in=600
    )


@pytest.mark.asyncio
async def test_signing_failure_never_falls_back_to_a_public_link():
    storage = SimpleNamespace(
        create_signed_url=AsyncMock(
            side_effect=RuntimeError("internal provider failure")
        )
    )
    with pytest.raises(HTTPException) as error:
        await private_document_response(owned_document(), storage)
    assert error.value.status_code == 503
    assert "provider" not in error.value.detail
