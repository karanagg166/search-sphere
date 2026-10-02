import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.db import AsyncSessionLocal
from src.main import app
from src.models.conversation import Conversation
from src.models.message import Message


async def create_user(client: AsyncClient, name: str = "FeedbackUser") -> tuple[str, str, dict[str, str]]:
    email = f"{name.lower()}_{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/auth/signup",
        json={"name": name, "email": email, "password": "Password123!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    return user_id, token, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_submit_generic_feedback():
    """Verify generic answer feedback submission."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        user_id, _, headers = await create_user(ac, "GenFeedbackUser")

        payload = {
            "rating": 1,
            "comment": "Accurate and grounded answer!",
        }
        response = await ac.post("/feedback", json=payload, headers=headers)
        assert response.status_code == 201
        data = response.json()
        assert data["rating"] == 1
        assert data["comment"] == "Accurate and grounded answer!"
        assert data["user_id"] == user_id
        assert "id" in data


@pytest.mark.asyncio
async def test_submit_negative_feedback():
    """Verify negative rating with feedback comment."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        user_id, _, headers = await create_user(ac, "NegFeedbackUser")

        payload = {
            "rating": -1,
            "comment": "The answer missed key details in Section 2.",
        }
        response = await ac.post("/feedback", json=payload, headers=headers)
        assert response.status_code == 201
        data = response.json()
        assert data["rating"] == -1
        assert data["comment"] == "The answer missed key details in Section 2."


@pytest.mark.asyncio
async def test_submit_message_feedback_in_conversation():
    """Verify feedback specifically linked to conversation message."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        user_id, _, headers = await create_user(ac, "ConvFeedbackUser")

        # Create conversation and assistant message directly in DB
        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as session:
            conv = Conversation(id=conv_id, user_id=user_id, title="RAG Thread")
            session.add(conv)
            msg = Message(
                id=msg_id,
                conversation_id=conv_id,
                role="assistant",
                content="Here is the retrieved answer.",
            )
            session.add(msg)
            await session.commit()

        # Submit feedback for this message
        payload = {
            "rating": 1,
            "comment": "Helpful response!",
        }
        response = await ac.post(
            f"/conversations/{conv_id}/messages/{msg_id}/feedback",
            json=payload,
            headers=headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["conversation_id"] == conv_id
        assert data["message_id"] == msg_id
        assert data["rating"] == 1


@pytest.mark.asyncio
async def test_message_feedback_rejects_foreign_user():
    """Verify that a user cannot submit feedback on another user's conversation."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        user_1_id, _, _ = await create_user(ac, "UserOne")
        _, _, headers_2 = await create_user(ac, "UserTwo")

        conv_id = str(uuid.uuid4())
        msg_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as session:
            conv = Conversation(id=conv_id, user_id=user_1_id, title="User One Thread")
            session.add(conv)
            msg = Message(
                id=msg_id,
                conversation_id=conv_id,
                role="assistant",
                content="User One private answer",
            )
            session.add(msg)
            await session.commit()

        # User 2 tries to submit feedback for User 1's conversation
        response = await ac.post(
            f"/conversations/{conv_id}/messages/{msg_id}/feedback",
            json={"rating": 1},
            headers=headers_2,
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_user_feedback():
    """Verify that GET /feedback returns only the authenticated user's feedback."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        user_1_id, _, headers_1 = await create_user(ac, "ListUser1")
        _, _, headers_2 = await create_user(ac, "ListUser2")

        # User 1 submits 2 feedbacks
        await ac.post("/feedback", json={"rating": 1, "comment": "Great"}, headers=headers_1)
        await ac.post("/feedback", json={"rating": -1, "comment": "Poor"}, headers=headers_1)

        # User 2 submits 1 feedback
        await ac.post("/feedback", json={"rating": 1, "comment": "User2 note"}, headers=headers_2)

        # User 1 lists feedback
        resp_1 = await ac.get("/feedback", headers=headers_1)
        assert resp_1.status_code == 200
        items_1 = resp_1.json()
        assert len(items_1) == 2
        assert all(item["user_id"] == user_1_id for item in items_1)

        # User 2 lists feedback
        resp_2 = await ac.get("/feedback", headers=headers_2)
        assert resp_2.status_code == 200
        items_2 = resp_2.json()
        assert len(items_2) == 1
