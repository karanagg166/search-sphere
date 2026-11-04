import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import or_, select

from src.db import AsyncSessionLocal
from src.models.document import Document
from src.services.durable_processor import (
    LEASE_TIMEOUT_SECONDS as STALE_PROCESSING_TIMEOUT_SECONDS,
)
from src.services.durable_processor import (
    MAX_INDEXING_RETRIES,
    durable_processor,
)

logger = structlog.get_logger()

# Track in-flight document indexing to prevent duplicate processing within the same process
_active_indexing_ids: set[str] = set()


async def index_document_pipeline(document_id: str) -> bool:
    """
    In-process indexing pipeline for a document.

    Ensures that when background workers or RabbitMQ are unavailable
    (e.g., single-container web deployment on Render free tier), documents
    are fully extracted, cleaned, chunked, embedded, and indexed into Qdrant.
    Uses bounded retries and safe atomic state transitions under workload scheduler guardrails.
    """
    logger.info("Starting in-process document indexing", document_id=document_id)

    # 1. Fetch document metadata and claim it with bounded retry tracking
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.id == document_id)
        )
        document = result.scalar_one_or_none()
        if not document:
            logger.error("Document not found for indexing", document_id=document_id)
            return False

        if document.status == "indexed":
            logger.info("Document already indexed, skipping", document_id=document_id)
            return True

        # Parse attempt count from status
        attempt = 1
        for prefix in (":retry:", ":attempt:", "proc:"):
            if prefix in document.status:
                try:
                    attempt = int(document.status.split(":")[-1])
                    break
                except ValueError:
                    attempt = 1

        if attempt > MAX_INDEXING_RETRIES:
            logger.warning("Document exceeded maximum retries", document_id=document_id, attempts=attempt)
            document.status = "failed:max_retries_exceeded"
            await session.commit()
            return False

        storage_key = document.storage_key

    # Execute durable processing pipeline with memory guardrails
    return await durable_processor.process_job(
        document_id=document_id,
        storage_key=storage_key,
        attempt=attempt,
    )


async def recover_stale_processing_documents() -> list[str]:
    """
    Recovers documents stranded in 'processing' or 'proc:%' status after a container restart, crash, or OOM.
    Requeues stranded documents if within retry limits, or marks them 'failed' if max retries exceeded.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_PROCESSING_TIMEOUT_SECONDS)
    recovered_ids: list[str] = []

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(
                    or_(
                        Document.status.like("processing%"),
                        Document.status.like("proc:%"),
                    ),
                    Document.updated_at < cutoff,
                )
            )
            stale_docs = result.scalars().all()
            for doc in stale_docs:
                attempt = 1
                if ":attempt:" in doc.status:
                    try:
                        attempt = int(doc.status.split(":")[-1])
                    except ValueError:
                        attempt = 1
                elif "proc:" in doc.status:
                    try:
                        attempt = int(doc.status.split(":")[-1])
                    except ValueError:
                        attempt = 1

                if attempt >= MAX_INDEXING_RETRIES:
                    doc.status = "failed"
                    logger.error(
                        "Stale document exceeded max processing retries across restarts; marked permanently failed",
                        document_id=doc.id,
                        attempts=attempt,
                    )
                else:
                    next_attempt = attempt + 1
                    doc.status = f"uploaded:retry:{next_attempt}"
                    logger.warning(
                        "Requeued stranded document for retry after restart/timeout",
                        document_id=doc.id,
                        next_attempt=next_attempt,
                    )
                    recovered_ids.append(doc.id)

            if stale_docs:
                await session.commit()
                logger.info(
                    "Processed stale in-flight documents",
                    count=len(stale_docs),
                    recovered=len(recovered_ids),
                )

    except Exception as exc:
        logger.warning("Error recovering stale processing documents", error=str(exc))

    return recovered_ids


async def sync_unindexed_documents() -> list[str]:
    """
    Scans PostgreSQL for unindexed documents and triggers processing sequentially.
    1. Durably recovers any documents stranded in 'processing' state across restarts.
    2. Sequentially indexes documents in 'uploaded' or 'uploaded:retry:*' status.
    3. Prevents duplicate in-flight processing and enforces single-job concurrency under memory limits.
    """
    await asyncio.sleep(2.0)
    logger.info("Scanning for unindexed uploaded documents...")

    # 1. Recover any documents stranded from a previous crash/restart
    await recover_stale_processing_documents()

    unindexed_ids: list[str] = []
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document.id).where(
                    or_(
                        Document.status == "uploaded",
                        Document.status.like("uploaded:retry:%"),
                    )
                ).order_by(Document.created_at.asc())
            )
            unindexed_ids = [row[0] for row in result.fetchall()]

        # Filter out documents already being indexed in-process
        pending_ids = [
            doc_id for doc_id in unindexed_ids if doc_id not in _active_indexing_ids
        ]

        if not pending_ids:
            logger.info("No unindexed documents found")
            return []

        logger.info(
            "Found unindexed documents to process",
            count=len(pending_ids),
            ids=pending_ids,
        )
        successful_ids = []
        for doc_id in pending_ids:
            if doc_id in _active_indexing_ids:
                continue
            _active_indexing_ids.add(doc_id)
            try:
                success = await index_document_pipeline(doc_id)
                if success:
                    successful_ids.append(doc_id)
            finally:
                _active_indexing_ids.discard(doc_id)

        return successful_ids

    except Exception as exc:
        logger.warning("Error while syncing unindexed documents", error=str(exc))
        return []


async def audit_and_recover_missing_vectors(batch_size: int = 10) -> dict[str, Any]:
    """
    Audits PostgreSQL documents against Qdrant vector store.
    Identifies documents marked 'indexed' but missing actual vector points (e.g. from historical
    ephemeral restarts or connection resets).
    Safely reindexes them from durable object storage with bounded concurrency.
    """
    from src.vector_store.qdrant_store import QdrantVectorStore

    try:
        vector_store = QdrantVectorStore()
        await vector_store.ensure_collection()
    except Exception as exc:
        logger.warning("Vector store unreachable; skipping missing vector recovery audit", error=str(exc))
        return {"status": "skipped", "reason": f"Vector store unavailable: {exc}"}

    reindexed: list[str] = []
    backfilled: list[str] = []
    audited = 0

    try:
        async with AsyncSessionLocal() as session:
            # Check documents marked indexed
            result = await session.execute(
                select(Document).where(Document.status == "indexed").order_by(Document.created_at.desc())
            )
            indexed_docs = result.scalars().all()
            audited = len(indexed_docs)

            for doc in indexed_docs:
                if len(reindexed) >= batch_size:
                    break
                try:
                    points = await vector_store.count_document_points(doc.id)
                    if points == 0:
                        logger.info("Identified indexed document missing vectors; recovering from object storage", document_id=doc.id)
                        # Reindex document from durable object storage
                        success = await durable_processor.process_job(
                            document_id=doc.id,
                            storage_key=doc.storage_key,
                            attempt=1,
                        )
                        if success:
                            reindexed.append(doc.id)
                    elif getattr(doc, "chunk_count", None) is None:
                        doc.chunk_count = points
                        backfilled.append(doc.id)
                except Exception as doc_err:
                    logger.warning("Error auditing document vector points", document_id=doc.id, error=str(doc_err))

            if backfilled:
                await session.commit()

        return {
            "status": "completed",
            "audited": audited,
            "reindexed_count": len(reindexed),
            "reindexed_ids": reindexed,
            "backfilled_count": len(backfilled),
        }
    except Exception as exc:
        logger.warning("Error during missing vector recovery audit", error=str(exc))
        return {"status": "error", "error": str(exc)}
