#!/usr/bin/env python3
"""
scripts/backfill_pgvector_chunks.py

Bounded, idempotent backfill utility for Search Sphere pgvector vector store.
Audits and reconciles document records against the document_chunks table.

Safety & Resource Guarantees:
- Bounded batching and sleep intervals to respect 512 MiB Render Free Tier limits.
- Checks storage file existence before attempting download/extraction.
- Idempotent: Skips documents with verified chunks already in pgvector.
- Respects tenant ownership and metadata boundaries.
- Supports --dry-run mode for safe capacity and impact evaluation.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Load environment configuration
load_dotenv()
load_dotenv("apps/api/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_pgvector")


@dataclass
class BackfillStats:
    total_audited: int = 0
    already_indexed: int = 0
    storage_missing: int = 0
    reindexed_success: int = 0
    reindex_failed: int = 0
    unrecoverable_ids: list[str] = field(default_factory=list)
    successful_ids: list[str] = field(default_factory=list)
    failed_ids: list[str] = field(default_factory=list)


async def audit_and_backfill(
    engine: Any,
    target: str = "all",
    batch_size: int = 5,
    limit: int | None = None,
    document_id: str | None = None,
    dry_run: bool = True,
    mark_missing: bool = False,
) -> dict[str, BackfillStats]:
    """
    Audits and optionally backfills vector chunks into document_chunks table.
    """
    # Defer heavy model imports until execution
    from src.models.document import Document
    from src.models.external_document import ExternalDocument
    from src.processing.chunking import DocumentChunker
    from src.processing.cleaning import TextCleaner
    from src.processing.embedding.dense_embedder import DenseEmbedder
    from src.processing.extraction.document_extractor import DocumentExtractor
    from src.processing.sparse_embedding.bm25_embedder import BM25Embedder
    from src.services.model_manager import model_manager
    from src.storage.object_storage import get_object_storage
    from src.vector_store import get_vector_store

    results: dict[str, BackfillStats] = {
        "documents": BackfillStats(),
        "external_documents": BackfillStats(),
    }

    storage = get_object_storage()
    vector_store = get_vector_store()

    # -------------------------------------------------------------------------
    # 1. User Documents Backfill
    # -------------------------------------------------------------------------
    if target in ("all", "documents"):
        stats = results["documents"]
        logger.info("Auditing user documents table...")

        async with AsyncSession(engine) as session:
            stmt = select(Document).order_by(Document.created_at.desc())
            if document_id:
                stmt = stmt.where(Document.id == document_id)
            if limit:
                stmt = stmt.limit(limit)

            doc_res = await session.execute(stmt)
            documents = list(doc_res.scalars().all())

        stats.total_audited = len(documents)
        logger.info(f"Auditing {len(documents)} user document records (dry_run={dry_run})...")

        for idx, doc in enumerate(documents, start=1):
            doc_id = doc.id
            storage_key = doc.storage_key

            # Check existing chunks in pgvector
            chunk_count = await vector_store.count_document_points(
                document_id=doc_id,
                filters={"client_id": None, "source_system": None},
            )

            if chunk_count > 0:
                stats.already_indexed += 1
                if doc.chunk_count != chunk_count:
                    if dry_run:
                        logger.info(
                            f"[PROPOSED] Would update chunk_count={chunk_count} for doc {doc_id} (current={doc.chunk_count})"
                        )
                    else:
                        async with AsyncSession(engine) as update_session:
                            await update_session.execute(
                                update(Document)
                                .where(Document.id == doc_id)
                                .values(chunk_count=chunk_count, updated_at=datetime.now(UTC))
                            )
                            await update_session.commit()
                        logger.info(
                            f"[COMMITTED] Updated chunk_count={chunk_count} for doc {doc_id}"
                        )
                continue

            # Check if PDF exists in durable storage
            file_exists = False
            try:
                file_exists = await storage.exists(storage_key)
            except Exception as st_err:  # noqa: BLE001
                logger.warning(f"Storage check error for doc {doc_id}: {st_err}")

            if not file_exists:
                stats.storage_missing += 1
                stats.unrecoverable_ids.append(doc_id)
                logger.warning(
                    f"Document {doc_id} ({doc.filename}) has missing storage object: {storage_key}"
                )
                if mark_missing:
                    if dry_run:
                        logger.info(
                            f"[PROPOSED] Would mark missing storage file for doc {doc_id} ({doc.filename})"
                        )
                    else:
                        async with AsyncSession(engine) as update_session:
                            await update_session.execute(
                                update(Document)
                                .where(Document.id == doc_id)
                                .values(
                                    status="failed:missing_storage_file",
                                    updated_at=datetime.now(UTC),
                                )
                            )
                            await update_session.commit()
                        logger.info(
                            f"[COMMITTED] Marked doc {doc_id} as failed:missing_storage_file"
                        )
                continue

            if dry_run:
                stats.reindexed_success += 1
                stats.successful_ids.append(doc_id)
                logger.info(f"[PROPOSED] Would reindex {doc_id} ({doc.filename}) from {storage_key}")
                continue

            # Live Reindexing Execution
            logger.info(f"Reindexing document {doc_id} ({doc.filename}) from {storage_key}...")
            try:
                pdf_bytes = await storage.download(storage_key)
                if not pdf_bytes:
                    raise ValueError("Downloaded storage file is empty (0 bytes).")

                extractor = DocumentExtractor()
                extracted = await asyncio.to_thread(extractor.extract, pdf_bytes)
                del pdf_bytes

                cleaner = TextCleaner()
                cleaned = cleaner.clean_document(extracted)
                del extracted

                chunker = DocumentChunker()
                chunked = await asyncio.to_thread(chunker.chunk_document, cleaned)
                del cleaned

                n_chunks = len(chunked.chunks)
                if n_chunks > 0:
                    dense_embedder = DenseEmbedder()
                    embedded = await asyncio.to_thread(dense_embedder.embed_document, chunked)

                    sparse_embedder = BM25Embedder()
                    sparse_vecs = await asyncio.to_thread(sparse_embedder.embed_chunks, chunked.chunks)

                    await vector_store.index_document(
                        document_id=doc_id,
                        document=embedded,
                        sparse_vectors=sparse_vecs,
                    )

                async with AsyncSession(engine) as update_session:
                    await update_session.execute(
                        update(Document)
                        .where(Document.id == doc_id)
                        .values(
                            status="indexed",
                            chunk_count=n_chunks,
                            updated_at=datetime.now(UTC),
                        )
                    )
                    await update_session.commit()

                stats.reindexed_success += 1
                stats.successful_ids.append(doc_id)
                logger.info(f"[COMMITTED] Reindexed document {doc_id} ({doc.filename}) with {n_chunks} chunks.")

            except Exception:
                stats.reindex_failed += 1
                stats.failed_ids.append(doc_id)
                logger.exception(f"Failed to reindex document {doc_id}")

            # Yield and trim memory between documents to respect 512 MiB limit
            model_manager.release_memory()
            if idx % batch_size == 0:
                await asyncio.sleep(0.5)

    # -------------------------------------------------------------------------
    # 2. External Documents Backfill
    # -------------------------------------------------------------------------
    if target in ("all", "external_documents"):
        stats = results["external_documents"]
        logger.info("Auditing external documents table...")

        async with AsyncSession(engine) as session:
            stmt = select(ExternalDocument).order_by(ExternalDocument.created_at.desc())
            if document_id:
                stmt = stmt.where(ExternalDocument.id == document_id)
            if limit:
                stmt = stmt.limit(limit)

            ext_res = await session.execute(stmt)
            ext_documents = list(ext_res.scalars().all())

        stats.total_audited = len(ext_documents)
        logger.info(f"Auditing {len(ext_documents)} external document records (dry_run={dry_run})...")

        for idx, doc in enumerate(ext_documents, start=1):
            doc_id = doc.id
            storage_path = doc.storage_path

            # Check existing chunks in pgvector for this external doc
            chunk_count = await vector_store.count_document_points(
                document_id=doc_id,
                filters={
                    "source_system": doc.source_system,
                    "tenant_id": doc.tenant_id,
                },
            )

            if chunk_count > 0:
                stats.already_indexed += 1
                if doc.chunk_count != chunk_count:
                    if dry_run:
                        logger.info(
                            f"[PROPOSED] Would update chunk_count={chunk_count} for external doc {doc_id} (current={doc.chunk_count})"
                        )
                    else:
                        async with AsyncSession(engine) as update_session:
                            await update_session.execute(
                                update(ExternalDocument)
                                .where(ExternalDocument.id == doc_id)
                                .values(chunk_count=chunk_count, updated_at=datetime.now(UTC))
                            )
                            await update_session.commit()
                        logger.info(
                            f"[COMMITTED] Updated chunk_count={chunk_count} for external doc {doc_id}"
                        )
                continue

            # Check storage existence
            file_exists = False
            try:
                file_exists = await storage.exists(storage_path)
            except Exception as st_err:  # noqa: BLE001
                logger.warning(f"Storage check error for ext doc {doc_id}: {st_err}")

            if not file_exists:
                stats.storage_missing += 1
                stats.unrecoverable_ids.append(doc_id)
                if mark_missing:
                    if dry_run:
                        logger.info(
                            f"[PROPOSED] Would mark missing storage file for external doc {doc_id}"
                        )
                    else:
                        async with AsyncSession(engine) as update_session:
                            await update_session.execute(
                                update(ExternalDocument)
                                .where(ExternalDocument.id == doc_id)
                                .values(
                                    status="FAILED",
                                    processing_error="Missing storage file",
                                    updated_at=datetime.now(UTC),
                                )
                            )
                            await update_session.commit()
                        logger.info(
                            f"[COMMITTED] Marked external doc {doc_id} as FAILED: Missing storage file"
                        )
                continue

            if dry_run:
                stats.reindexed_success += 1
                stats.successful_ids.append(doc_id)
                logger.info(f"[PROPOSED] Would reindex external doc {doc_id} from {storage_path}")
                continue

            # Live processing for external doc
            logger.info(f"Reindexing external document {doc_id} from {storage_path}...")
            try:
                pdf_bytes = await storage.download(storage_path)
                extractor = DocumentExtractor()
                extracted = await asyncio.to_thread(extractor.extract, pdf_bytes)
                del pdf_bytes

                cleaner = TextCleaner()
                cleaned = cleaner.clean_document(extracted)
                del extracted

                chunker = DocumentChunker()
                chunked = await asyncio.to_thread(chunker.chunk_document, cleaned)
                del cleaned

                n_chunks = len(chunked.chunks)
                if n_chunks > 0:
                    dense_embedder = DenseEmbedder()
                    embedded = await asyncio.to_thread(dense_embedder.embed_document, chunked)

                    sparse_embedder = BM25Embedder()
                    sparse_vecs = await asyncio.to_thread(sparse_embedder.embed_chunks, chunked.chunks)

                    await vector_store.index_document(
                        document_id=doc_id,
                        document=embedded,
                        sparse_vectors=sparse_vecs,
                        client_id=doc.source_system,
                        tenant_id=doc.tenant_id,
                        patient_id=doc.external_patient_id,
                        source_system=doc.source_system,
                        collection_id=doc.collection_id,
                        owner_subject_id=doc.owner_subject_id,
                        document_type=doc.document_type,
                        file_name=doc.file_name,
                    )

                async with AsyncSession(engine) as update_session:
                    await update_session.execute(
                        update(ExternalDocument)
                        .where(ExternalDocument.id == doc_id)
                        .values(
                            status="READY",
                            chunk_count=n_chunks,
                            updated_at=datetime.now(UTC),
                        )
                    )
                    await update_session.commit()

                stats.reindexed_success += 1
                stats.successful_ids.append(doc_id)
                logger.info(f"[COMMITTED] Reindexed external doc {doc_id} ({n_chunks} chunks).")

            except Exception:
                stats.reindex_failed += 1
                stats.failed_ids.append(doc_id)
                logger.exception(f"Failed to reindex external document {doc_id}")

            model_manager.release_memory()
            if idx % batch_size == 0:
                await asyncio.sleep(0.5)

    return results


def print_report(results: dict[str, BackfillStats], dry_run: bool) -> None:
    print("\n" + "=" * 60)
    print(f"  SEARCH SPHERE PGVECTOR AUDIT & RECOVERY REPORT ({'DRY-RUN (PROPOSED)' if dry_run else 'LIVE (COMMITTED)'})")
    print("=" * 60)
    for category, stats in results.items():
        print(f"\n[{category.upper()}]")
        print(f"  Total Audited:         {stats.total_audited}")
        print(f"  Already Has Chunks:    {stats.already_indexed}")
        print(f"  Missing Storage Files: {stats.storage_missing}")
        print(f"  {'Would Reindex' if dry_run else 'Successfully Reindexed'}: {stats.reindexed_success}")
        print(f"  Failed Reindex:        {stats.reindex_failed}")
        if stats.unrecoverable_ids:
            print(f"  Unrecoverable Sample:  {stats.unrecoverable_ids[:5]}")
    print("\n" + "=" * 60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit and backfill pgvector document chunks. Defaults to safe, read-only audit mode."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Perform actual database writes and vector indexing. By default, audit mode is active.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Explicitly request audit-only mode without writes (this is the default behavior).",
    )
    parser.add_argument(
        "--target",
        choices=["all", "documents", "external_documents"],
        default="all",
        help="Entity target to audit or backfill (default: all).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Batch size before memory trim.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of documents to audit or backfill.",
    )
    parser.add_argument(
        "--max-documents",
        type=int,
        default=None,
        help="Alias for --limit: maximum number of documents to audit or backfill.",
    )
    parser.add_argument(
        "--document-id",
        type=str,
        default=None,
        help="Audit/backfill a single document ID.",
    )
    parser.add_argument(
        "--mark-missing",
        action="store_true",
        default=False,
        help="Update DB status for missing storage files.",
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve limit vs max_documents
    effective_limit = args.limit if args.limit is not None else args.max_documents

    # Default is audit-only unless --execute is explicitly passed
    # If both --execute and --dry-run are passed, safety takes precedence: dry_run wins
    dry_run = not args.execute or args.dry_run

    # Enforce bounded scope for live production execution
    if not dry_run and effective_limit is None and not args.document_id:
        logger.error(
            "Safety error: Live execution (--execute) requires explicit bounded scope. "
            "Specify --limit (or --max-documents) or --document-id to prevent unbounded writes."
        )
        return 2

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL is not set.")
        return 1

    engine = create_async_engine(db_url, echo=False)
    try:
        results = await audit_and_backfill(
            engine=engine,
            target=args.target,
            batch_size=args.batch_size,
            limit=effective_limit,
            document_id=args.document_id,
            dry_run=dry_run,
            mark_missing=args.mark_missing,
        )
        print_report(results, dry_run=dry_run)
    finally:
        await engine.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
