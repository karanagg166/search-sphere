"""Opt-in API/PostgreSQL/RabbitMQ/worker/Qdrant lifecycle; synthetic data only.

Run inside the isolated API container with RUN_GENERIC_LIVE=1. This calls
the running HTTP server, so the unit-test broker patch does not intercept jobs.
"""
import asyncio
import os
import uuid

import fitz
import httpx
import pytest
from sqlalchemy import delete

from src.db import AsyncSessionLocal
from src.models.service_client import ServiceClient
from src.schemas.service_client import ServiceClientCreateRequest
from src.services.service_client_service import ServiceClientService


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_GENERIC_LIVE") != "1", reason="requires isolated running services")
async def test_live_clients_colliding_identifiers_reindex_and_delete():
    suffix = uuid.uuid4().hex[:12]
    clients = [f"generic_test_{suffix}", f"exam_arena_test_{suffix}"]
    keys = []
    tenant, collection, document = "synthetic_tenant", "shared_collection", "shared_document"
    scopes = ["documents:read", "documents:write", "documents:delete", "collections:manage", "search:execute", "answers:generate"]
    async with AsyncSessionLocal() as db:
        for client in clients:
            _, key = await ServiceClientService(db).register_client(ServiceClientCreateRequest(
                client_id=client, name="Synthetic lifecycle", authorized_tenants=[tenant], allowed_scopes=scopes,
            ))
            keys.append(key)

    def headers(index, subject="synthetic_owner"):
        return {"Authorization": f"Bearer {keys[index]}", "X-Client-ID": clients[index],
                "X-Tenant-ID": tenant, "X-Collection-ID": collection, "X-Subject-ID": subject}

    def pdf(text):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 70), text)
            return doc.tobytes()

    async with httpx.AsyncClient(base_url=os.getenv("SEARCH_SPHERE_LIVE_URL", "http://127.0.0.1:8000"), timeout=120) as http:
        async def register(index, text):
            upload = await http.post("/api/v1/documents/upload", headers=headers(index),
                data={"document_id": document, "collection_id": collection},
                files={"file": ("synthetic.pdf", pdf(text), "application/pdf")})
            assert upload.status_code == 201, upload.status_code
            stored = upload.json()
            result = await http.post("/api/v1/documents", headers=headers(index), json={
                "external_document_id": document, "collection_id": collection,
                "owner_subject_id": "synthetic_owner", "file_name": "synthetic.pdf", **stored,
            })
            assert result.status_code == 202, result.status_code
            for _ in range(240):
                status = await http.get(f"/api/v1/documents/{document}", headers=headers(index))
                assert status.status_code == 200
                state = status.json()["status"]
                assert state != "FAILED", "Synthetic ingestion failed"
                if state == "READY":
                    return
                await asyncio.sleep(1)
            pytest.fail("Ingestion did not become READY")

        async def search(index, query):
            response = await http.post("/api/v1/search", headers=headers(index), json={"query": query})
            assert response.status_code == 200, response.status_code
            return response.json()["results"]

        try:
            for index in range(2):
                response = await http.post("/api/v1/collections", headers=headers(index), json={"collection_id": collection, "name": "Synthetic records"})
                assert response.status_code == 201
                await register(index, ["Synthetic astronomy record: the observatory tracks Saturn.", "Synthetic history record: the archive describes the Roman empire."][index])
            for index in range(2):
                results = await search(index, ["Saturn observatory", "Roman empire"][index])
                assert results
                assert all(r["client_id"] == clients[index] and r["owner_subject_id"] == "synthetic_owner" for r in results)
            foreign = await http.get(f"/api/v1/documents/{document}", headers=headers(0, "foreign_owner"))
            assert foreign.status_code == 404
            overwrite = await http.post("/api/v1/documents/upload", headers=headers(0, "foreign_owner"), data={"document_id": document, "collection_id": collection}, files={"file": ("synthetic.pdf", pdf("Foreign overwrite"), "application/pdf")})
            assert overwrite.status_code == 403
            before = await search(0, "Saturn")
            await register(0, "Synthetic astronomy record: the observatory now tracks Neptune.")
            after = await search(0, "Neptune observatory")
            assert len(after) == len(before)
            assert all("Saturn" not in r["text"] for r in after)
            await register(0, "Synthetic astronomy record: the observatory now tracks Neptune.")
            assert len(await search(0, "Neptune")) == len(after)
            assert (await http.delete(f"/api/v1/documents/{document}", headers=headers(0))).status_code == 200
            assert await search(0, "Neptune") == []
            assert await search(1, "Roman empire")
            async with AsyncSessionLocal() as db:
                row = await ServiceClientService(db).get_client_by_id(clients[1])
                row.status = "revoked"
                await db.commit()
            assert (await http.post("/api/v1/search", headers=headers(1), json={"query": "Roman"})).status_code == 401
            async with AsyncSessionLocal() as db:
                row = await ServiceClientService(db).get_client_by_id(clients[1])
                row.status = "active"
                await db.commit()
        finally:
            for index in range(2):
                await http.delete(f"/api/v1/documents/{document}", headers=headers(index))
                await http.delete(f"/api/v1/collections/{collection}", headers=headers(index))
            async with AsyncSessionLocal() as db:
                await db.execute(delete(ServiceClient).where(ServiceClient.client_id.in_(clients)))
                await db.commit()
