from typing import Any
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.conversation import Conversation
from src.models.message import Message


class ConversationRepository:
    """
    Data access layer for Conversations and Messages in PostgreSQL.
    Enforces strict tenant isolation by always scoping operations by user_id.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, user_id: str, title: str | None = None) -> Conversation:
        """Create a new conversation belonging to user_id."""
        conversation = Conversation(
            user_id=user_id,
            title=title or "New Conversation",
        )
        self.db.add(conversation)
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation

    async def get_by_id(
        self, conversation_id: str, user_id: str, include_messages: bool = True
    ) -> Conversation | None:
        """
        Fetch a single conversation by ID and user_id.
        Returns None if not found or if owned by another user.
        """
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        if include_messages:
            stmt = stmt.options(selectinload(Conversation.messages))

        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def list_for_user(self, user_id: str) -> list[tuple[Conversation, int]]:
        """
        List all conversations for user_id along with their message counts,
        ordered by updated_at descending.
        """
        # Subquery or count join
        stmt = (
            select(
                Conversation,
                func.count(Message.id).label("message_count"),
            )
            .outerjoin(Message, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user_id)
            .group_by(Conversation.id)
            .order_by(Conversation.updated_at.desc())
        )
        result = await self.db.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def update_title(
        self, conversation_id: str, user_id: str, title: str
    ) -> Conversation | None:
        """Update conversation title if owned by user_id."""
        conv = await self.get_by_id(conversation_id, user_id, include_messages=False)
        if not conv:
            return None
        conv.title = title
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def delete(self, conversation_id: str, user_id: str) -> bool:
        """
        Delete a conversation and cascade delete its messages.
        Returns True if deleted, False if conversation did not exist or belonged to another user.
        """
        conv = await self.get_by_id(conversation_id, user_id, include_messages=False)
        if not conv:
            return False
        await self.db.delete(conv)
        await self.db.commit()
        return True

    async def create_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        original_query: str | None = None,
        retrieval_query: str | None = None,
        rewritten: bool = False,
        sources: list[dict[str, Any]] | None = None,
    ) -> Message:
        """Persist a new message into the specified conversation."""
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            original_query=original_query,
            retrieval_query=retrieval_query,
            rewritten=rewritten,
            sources=sources,
        )
        self.db.add(msg)
        await self.db.commit()
        await self.db.refresh(msg)
        return msg

    async def get_recent_messages(
        self, conversation_id: str, limit: int = 10
    ) -> list[Message]:
        """
        Fetch the most recent `limit` messages in chronological order (created_at asc).
        """
        # Fetch latest `limit` messages ordered desc, then reverse
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        messages = list(result.scalars().all())
        messages.reverse()
        return messages
