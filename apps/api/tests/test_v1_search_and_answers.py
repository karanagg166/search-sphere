import uuid
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.models.external_document import ExternalDocument
from src.processing.models.search import DenseSearchResult
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.services.answer_generator import AnswerGenerator, get_answer_generator
from src.services.search_service import get_retriever
from tests.conftest import VALID_AUTH_HEADER


@pytest.mark.asyncio
async def test_v1_search_and_grounded_answers():
    """
    Simulates second external client ExamArena:
    Tenant: school_cbse_10
    Collection: physics_class_10
    Tests hybrid search, grounded answer generation, and SSE streaming.
    """
    cid = f"exam_arena_{uuid.uuid4().hex[:6]}"
    doc_ext_id = f"physics_ch7_{uuid.uuid4().hex[:4]}"

    # Setup mock retriever and mock answer generator
    mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
    mock_generator = AsyncMock(spec=AnswerGenerator)

    chunk_1 = DenseSearchResult(
        point_id="pt-1",
        score=0.92,
        document_id=doc_ext_id,
        chunk_index=0,
        content="Universal Gravitation: Every particle attracts every other particle with a force directly proportional to the product of their masses and inversely proportional to the square of the distance between them: F = G * (m1 * m2) / r^2.",
        token_count=40,
        start_page=12,
        end_page=12,
        page_numbers=[12],
        block_types=["text"],
        rank=1,
        client_id=cid,
        tenant_id="school_cbse_10",
        collection_id="physics_class_10",
        owner_subject_id="curriculum_team",
        document_type="TEXTBOOK",
        file_name="gravitation_cbse10.pdf",
    )
    mock_retriever.search.return_value = [chunk_1]

    mock_generator.generate_answer.return_value = (
        "According to Newton's Universal Gravitation Law, the gravitational force between two objects is given by F = G * (m1 * m2) / r^2 [1]."
    )

    async def mock_stream(*args, **kwargs):
        async def token_gen():
            tokens = ["According ", "to ", "Newton's ", "law, ", "F = G * (m1 * m2) / r^2 [1]."]
            for t in tokens:
                yield t
        return token_gen(), [chunk_1]

    mock_generator.generate_answer_stream.side_effect = mock_stream

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    app.dependency_overrides[get_answer_generator] = lambda: mock_generator

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Register ExamArena service client
            res = await client.post(
                "/api/v1/clients",
                json={
                    "client_id": cid,
                    "name": "ExamArena Learning",
                    "allowed_scopes": ["documents:read", "documents:write", "search:execute", "answers:generate", "collections:manage"],
                    "authorized_tenants": ["*"],
                },
                headers=VALID_AUTH_HEADER,
            )
            assert res.status_code == 201
            api_key = res.json()["api_key"]

            auth_headers = {
                "Authorization": f"Bearer {api_key}",
                "X-Client-ID": cid,
                "X-Tenant-ID": "school_cbse_10",
            }

            # 2. Create collection
            coll_res = await client.post(
                "/api/v1/collections",
                json={"collection_id": "physics_class_10", "name": "Physics Class 10"},
                headers=auth_headers,
            )
            assert coll_res.status_code == 201

            # 3. Register document in DB with status READY
            reg_res = await client.post(
                "/api/v1/documents",
                json={
                    "external_document_id": doc_ext_id,
                    "storage_key": f"documents/{cid}/school_cbse_10/physics_class_10/{doc_ext_id}/gravitation.pdf",
                    "file_name": "gravitation_cbse10.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 250000,
                    "collection_id": "physics_class_10",
                    "owner_subject_id": "curriculum_team",
                    "document_type": "TEXTBOOK",
                },
                headers=auth_headers,
            )
            assert reg_res.status_code == 202

            # Directly update document status to READY in database so retriever verification passes
            from src.db import AsyncSessionLocal
            from sqlalchemy import update
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(ExternalDocument)
                    .where(ExternalDocument.source_system == cid, ExternalDocument.external_document_id == doc_ext_id)
                    .values(status="READY")
                )
                await session.commit()

            # 4. Execute Semantic Search
            search_res = await client.post(
                "/api/v1/search",
                json={
                    "query": "formula for universal gravitation",
                    "collection_id": "physics_class_10",
                    "limit": 5,
                },
                headers=auth_headers,
            )
            assert search_res.status_code == 200, search_res.text
            search_data = search_res.json()
            assert search_data["total"] == 1
            assert len(search_data["results"]) == 1
            res_chunk = search_data["results"][0]
            assert res_chunk["document_id"] == doc_ext_id
            assert "F = G *" in res_chunk["text"]
            assert res_chunk["client_id"] == cid
            assert res_chunk["tenant_id"] == "school_cbse_10"
            assert res_chunk["collection_id"] == "physics_class_10"

            # 5. Generate Grounded Answer
            ans_res = await client.post(
                "/api/v1/answers",
                json={
                    "query": "What is the formula for universal gravitation?",
                    "collection_id": "physics_class_10",
                    "limit": 5,
                },
                headers=auth_headers,
            )
            assert ans_res.status_code == 200, ans_res.text
            ans_data = ans_res.json()
            assert "F = G *" in ans_data["answer"]
            assert len(ans_data["citations"]) == 1
            citation = ans_data["citations"][0]
            assert citation["citation_number"] == 1
            assert citation["document_id"] == doc_ext_id
            assert citation["page_number"] == 12

            # 6. Stream Grounded Answer via SSE
            stream_res = await client.post(
                "/api/v1/answers/stream",
                json={
                    "query": "What is the formula for universal gravitation?",
                    "collection_id": "physics_class_10",
                },
                headers=auth_headers,
            )
            assert stream_res.status_code == 200
            assert "text/event-stream" in stream_res.headers["content-type"]
            stream_content = stream_res.text
            assert "data: " in stream_content
            assert "[DONE]" in stream_content
            assert "F = G *" in stream_content

    finally:
        app.dependency_overrides.pop(get_retriever, None)
        app.dependency_overrides.pop(get_answer_generator, None)
