import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.collection import DocumentCollection
from src.schemas.collection import CollectionCreateRequest
from src.security.service_context import ServiceContext

logger = structlog.get_logger()


class CollectionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_collection(
        self, context: ServiceContext, request: CollectionCreateRequest
    ) -> DocumentCollection:
        stmt = select(DocumentCollection).where(
            DocumentCollection.client_id == context.client_id,
            DocumentCollection.tenant_id == context.tenant_id,
            DocumentCollection.collection_id == request.collection_id,
        )
        res = await self.db.execute(stmt)
        if res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Collection '{request.collection_id}' already exists in this tenant scope.",
            )

        coll = DocumentCollection(
            client_id=context.client_id,
            tenant_id=context.tenant_id,
            collection_id=request.collection_id,
            name=request.name,
            description=request.description,
            metadata_json=request.metadata,
        )
        self.db.add(coll)
        await self.db.commit()
        await self.db.refresh(coll)

        logger.info(
            "Created document collection",
            client_id=context.client_id,
            tenant_id=context.tenant_id,
            collection_id=coll.collection_id,
        )
        return coll

    async def get_collection(
        self, context: ServiceContext, collection_id: str
    ) -> DocumentCollection | None:
        stmt = select(DocumentCollection).where(
            DocumentCollection.client_id == context.client_id,
            DocumentCollection.tenant_id == context.tenant_id,
            DocumentCollection.collection_id == collection_id,
        )
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none()

    async def list_collections(
        self, context: ServiceContext, limit: int = 50, offset: int = 0
    ) -> tuple[list[DocumentCollection], int]:
        base_stmt = select(DocumentCollection).where(
            DocumentCollection.client_id == context.client_id,
            DocumentCollection.tenant_id == context.tenant_id,
        )
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            base_stmt.order_by(DocumentCollection.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await self.db.execute(stmt)
        return list(res.scalars().all()), total

    async def delete_collection(
        self, context: ServiceContext, collection_id: str
    ) -> bool:
        coll = await self.get_collection(context, collection_id)
        if not coll:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Collection '{collection_id}' not found.",
            )

        await self.db.delete(coll)
        await self.db.commit()
        logger.info(
            "Deleted document collection",
            client_id=context.client_id,
            tenant_id=context.tenant_id,
            collection_id=collection_id,
        )
        return True
