from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.document import Document


class DocumentRepository:
    """Repository handling database operations for the Document entity."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        document_id: str,
        user_id: str,
        filename: str,
        storage_key: str,
        file_url: str,
        file_size: int,
        mime_type: str = "application/pdf",
        status: str = "uploaded",
    ) -> Document:
        doc = Document(
            id=document_id,
            user_id=user_id,
            filename=filename,
            storage_key=storage_key,
            file_url=file_url,
            file_size=file_size,
            mime_type=mime_type,
            status=status,
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def get_by_id(
        self, document_id: str, user_id: str | None = None
    ) -> Document | None:
        query = select(Document).where(Document.id == document_id)
        if user_id is not None:
            query = query.where(Document.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        query = (
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def count_by_user(self, user_id: str) -> int:
        query = select(func.count(Document.id)).where(Document.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalar_one() or 0

    async def delete(self, document: Document) -> None:
        await self.db.delete(document)
        await self.db.commit()
