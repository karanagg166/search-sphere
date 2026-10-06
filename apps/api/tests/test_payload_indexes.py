from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.vector_store.qdrant_store import QdrantVectorStore

FIELDS = {
    "document_id", "client_id", "tenant_id", "collection_id",
    "owner_subject_id", "source_system", "patient_id", "document_type",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [None, {}, {field: object() for field in FIELDS}, {"document_id": object()}])
async def test_existing_payload_indexes(schema):
    client = AsyncMock()
    store = QdrantVectorStore(client=client)
    await store._ensure_payload_indexes(SimpleNamespace(payload_schema=schema))
    created = {call.kwargs["field_name"] for call in client.create_payload_index.await_args_list}
    assert created == FIELDS - set(schema or {})
    assert client.create_payload_index.await_count == len(created)


@pytest.mark.asyncio
@pytest.mark.parametrize("info", [None, SimpleNamespace()])
async def test_absent_collection_info(info):
    client = AsyncMock()
    await QdrantVectorStore(client=client)._ensure_payload_indexes(info)
    assert {call.kwargs["field_name"] for call in client.create_payload_index.await_args_list} == FIELDS


@pytest.mark.asyncio
async def test_new_collection_creates_all_indexes():
    client = AsyncMock()
    client.collection_exists.return_value = False
    await QdrantVectorStore(client=client).ensure_collection()
    client.create_collection.assert_awaited_once()
    assert {call.kwargs["field_name"] for call in client.create_payload_index.await_args_list} == FIELDS
