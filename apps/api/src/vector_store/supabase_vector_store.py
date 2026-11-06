import json
import math
from typing import Any

import structlog
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import AsyncSessionLocal, engine
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.processing.models.document import EmbeddedDocument
from src.processing.models.search import (
    DenseSearchResult,
    HybridSearchResult,
    SparseSearchResult,
)
from src.processing.models.sparse_vector import SparseVector
from src.vector_store.qdrant_store import (
    QdrantVectorStoreError,
    generate_point_id,
)

logger = structlog.get_logger()


class SupabaseVectorStoreError(QdrantVectorStoreError):
    """Raised when an operation against Supabase pgvector store fails."""


ALLOWED_CHUNK_FILTER_COLUMNS: frozenset[str] = frozenset({
    "document_id",
    "client_id",
    "tenant_id",
    "patient_id",
    "collection_id",
    "owner_subject_id",
    "source_system",
    "document_type",
    "file_name",
})


def _build_sql_filters(
    document_id: str | None = None,
    document_ids: list[str] | None = None,
    patient_id: str | None = None,
    source_system: str | None = None,
    filters: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """
    Builds parameterized SQL WHERE clauses strictly validating filter keys against ALLOWED_CHUNK_FILTER_COLUMNS.
    Never interpolates raw caller values or unallowlisted column names.
    """
    where_clauses: list[str] = []
    params: dict[str, Any] = {}

    if document_id is not None:
        where_clauses.append("document_id = :doc_id")
        params["doc_id"] = str(document_id).strip()

    if document_ids is not None:
        cleaned_ids = [str(did).strip() for did in document_ids if str(did).strip()]
        if not cleaned_ids:
            where_clauses.append("1 = 0")
        else:
            where_clauses.append("document_id = ANY(:allowed_doc_ids)")
            params["allowed_doc_ids"] = cleaned_ids

    resolved_patient = patient_id or (filters.get("patient_id") if filters else None)
    if resolved_patient is not None:
        where_clauses.append("patient_id = :filter_patient_id")
        params["filter_patient_id"] = str(resolved_patient).strip()

    resolved_source = source_system or (filters.get("source_system") if filters else None)
    if resolved_source is not None:
        where_clauses.append("source_system = :filter_source_system")
        params["filter_source_system"] = str(resolved_source).strip()

    if filters:
        for k, v in filters.items():
            if k in ("patient_id", "source_system", "document_id", "document_ids"):
                continue
            if k not in ALLOWED_CHUNK_FILTER_COLUMNS:
                raise SupabaseVectorStoreError(
                    f"Disallowed filter column '{k}'. Allowed columns: {sorted(ALLOWED_CHUNK_FILTER_COLUMNS)}"
                )
            if v is None:
                continue

            param_name = f"filter_{k}"
            if isinstance(v, (list, tuple, set)):
                cleaned_items = [str(item).strip() for item in v if str(item).strip()]
                if not cleaned_items:
                    where_clauses.append("1 = 0")
                else:
                    where_clauses.append(f"{k} = ANY(:{param_name})")
                    params[param_name] = cleaned_items
            else:
                where_clauses.append(f"{k} = :{param_name}")
                params[param_name] = v

    return where_clauses, params


def _apply_sqlite_filters(
    stmt: Any,
    document_id: str | None = None,
    document_ids: list[str] | None = None,
    patient_id: str | None = None,
    source_system: str | None = None,
    filters: dict[str, Any] | None = None,
) -> Any:
    """Applies strict allowlisted filters against SQLAlchemy DocumentChunk expressions for SQLite fallback."""
    if document_id is not None:
        stmt = stmt.where(DocumentChunk.document_id == str(document_id).strip())

    if document_ids is not None:
        cleaned_ids = [str(did).strip() for did in document_ids if str(did).strip()]
        if not cleaned_ids:
            stmt = stmt.where(DocumentChunk.id.is_(None))
        else:
            stmt = stmt.where(DocumentChunk.document_id.in_(cleaned_ids))

    resolved_patient = patient_id or (filters.get("patient_id") if filters else None)
    if resolved_patient is not None:
        stmt = stmt.where(DocumentChunk.patient_id == str(resolved_patient).strip())

    resolved_source = source_system or (filters.get("source_system") if filters else None)
    if resolved_source is not None:
        stmt = stmt.where(DocumentChunk.source_system == str(resolved_source).strip())

    if filters:
        for k, v in filters.items():
            if k in ("patient_id", "source_system", "document_id", "document_ids"):
                continue
            if k not in ALLOWED_CHUNK_FILTER_COLUMNS:
                raise SupabaseVectorStoreError(
                    f"Disallowed filter column '{k}'. Allowed columns: {sorted(ALLOWED_CHUNK_FILTER_COLUMNS)}"
                )
            if v is None:
                continue
            if isinstance(v, (list, tuple, set)):
                cleaned_list = [str(x) for x in v]
                if not cleaned_list:
                    stmt = stmt.where(DocumentChunk.id.is_(None))
                else:
                    stmt = stmt.where(getattr(DocumentChunk, k).in_(cleaned_list))
            else:
                stmt = stmt.where(getattr(DocumentChunk, k) == v)

    return stmt


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Calculate cosine similarity between two float vectors in Python."""
    if len(v1) != len(v2) or not v1:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2, strict=True))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


class SupabaseVectorStore:
    """
    Durable vector storage and hybrid retrieval backed by Supabase PostgreSQL (pgvector).

    Features:
    - 384-dimensional dense vectors stored in PostgreSQL table `document_chunks`;
    - Fast Approximate Nearest Neighbor (ANN) search via HNSW indexing;
    - Native lexical retrieval via PostgreSQL `tsvector` / `websearch_to_tsquery`;
    - Server-side Reciprocal Rank Fusion (RRF) combining dense and lexical signals;
    - Relational integrity: document deletion automatically cleans up chunks;
    - Fully compatible with QdrantVectorStore interface for zero-friction swapping.
    """

    def __init__(
        self,
        collection_name: str | None = None,
        vector_dimension: int | None = None,
        batch_size: int | None = None,
        session_factory: Any | None = None,
        sparse_embedder: Any | None = None,
        **kwargs: Any,
    ) -> None:
        self.collection_name = collection_name or getattr(settings, "QDRANT_COLLECTION_NAME", "documents")
        self.vector_dimension = vector_dimension or 384
        self.batch_size = batch_size or getattr(settings, "QDRANT_UPSERT_BATCH_SIZE", 100)
        self.session_factory = session_factory or AsyncSessionLocal
        self.sparse_embedder = sparse_embedder
        self.is_embedded = False

    async def ensure_collection(self, force_check: bool = False) -> None:
        """Verify that the document_chunks table and pgvector extension are available."""
        try:
            async with self.session_factory() as session:
                await session.execute(text("SELECT 1 FROM document_chunks LIMIT 1;"))
        except Exception as exc:
            logger.info(
                "Verifying or creating document_chunks table in database",
                error=str(exc),
            )
            try:
                from src.db import init_db
                await init_db()
            except Exception as init_exc:
                raise SupabaseVectorStoreError(
                    f"Failed to ensure document_chunks table in database: {init_exc}"
                ) from init_exc

    async def index_document(
        self,
        document_id: str,
        document: EmbeddedDocument,
        sparse_vectors: list[SparseVector] | None = None,
        extra_payload: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> int:
        """
        Synchronize PostgreSQL document_chunks table with an EmbeddedDocument.

        Steps:
        1. Ensure table exists.
        2. Prepare replacement rows with deterministic scoped point IDs.
        3. Serialize deliveries for the same client/tenant/document identity.
        4. Delete old chunks and insert replacements in one transaction.
        5. Update native document telemetry in that same transaction.
        6. Return total chunks written.
        """
        if session is None:
            await self.ensure_collection()

        external = bool(extra_payload and extra_payload.get("client_id"))
        if external and not extra_payload.get("tenant_id"):
            raise SupabaseVectorStoreError("External indexing requires a tenant scope.")
        if extra_payload and extra_payload.get("source_system") and not external:
            raise SupabaseVectorStoreError("External indexing requires a client scope.")

        total_chunks = len(document.chunks)
        identity = document_id
        if external:
            identity = json.dumps([extra_payload["client_id"], extra_payload.get("tenant_id"), document_id], separators=(",", ":"))

        chunks_to_insert: list[DocumentChunk] = []
        for chunk in document.chunks:
            point_id = generate_point_id(identity, chunk.chunk_index)

            # Metadata extraction
            client_id = extra_payload.get("client_id") if extra_payload else None
            tenant_id = extra_payload.get("tenant_id") if extra_payload else None
            patient_id = extra_payload.get("patient_id") if extra_payload else None
            source_system = extra_payload.get("source_system") if extra_payload else None
            collection_id = extra_payload.get("collection_id") if extra_payload else None
            owner_subject_id = extra_payload.get("owner_subject_id") if extra_payload else None
            doc_type = extra_payload.get("document_type") if extra_payload else None
            file_name = extra_payload.get("file_name") if extra_payload else None

            # Retain non-core payload fields in metadata_json
            extra_meta = {
                k: v for k, v in (extra_payload or {}).items()
                if k not in (
                    "client_id", "tenant_id", "patient_id", "source_system",
                    "collection_id", "owner_subject_id", "document_type", "file_name",
                )
            }

            row = DocumentChunk(
                point_id=point_id,
                document_id=document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                token_count=chunk.token_count,
                start_page=chunk.start_page,
                end_page=chunk.end_page,
                page_numbers=list(chunk.page_numbers),
                block_types=list(chunk.block_types),
                embedding=chunk.embedding,
                client_id=client_id,
                tenant_id=tenant_id,
                patient_id=patient_id,
                source_system=source_system,
                collection_id=collection_id,
                owner_subject_id=owner_subject_id,
                document_type=doc_type,
                file_name=file_name,
                metadata_json={**extra_meta, "provenance": [dict(item) for item in getattr(chunk, "provenance", [])]},
            )
            chunks_to_insert.append(row)

        try:
            if session is not None:
                # The caller holds the job lease and commits vectors, content and status
                # together. Never commit a caller-owned transaction here.
                await self._replace_chunks(session, document_id, identity, chunks_to_insert, extra_payload)
            else:
                async with self.session_factory() as owned, owned.begin():
                    await self._replace_chunks(owned, document_id, identity, chunks_to_insert, extra_payload)

            logger.info(
                "Document chunks successfully indexed in Supabase pgvector",
                document_id=document_id,
                chunks_written=total_chunks,
            )
            return total_chunks

        except Exception as exc:
            logger.error("Document indexing transaction failed", document_id=document_id, error_type=type(exc).__name__)
            raise SupabaseVectorStoreError(
                "Document indexing transaction failed."
            ) from None

    async def _replace_chunks(
        self,
        session: AsyncSession,
        document_id: str,
        identity: str,
        rows: list[DocumentChunk],
        payload: dict[str, Any] | None,
    ) -> None:
        if session.get_bind().dialect.name == "postgresql":
            # Serializes concurrent deliveries even when no native parent row exists.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": identity},
            )
        deletion = delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        if payload and payload.get("client_id"):
            deletion = deletion.where(
                DocumentChunk.client_id == payload["client_id"],
                DocumentChunk.tenant_id == payload["tenant_id"],
            )
        else:
            deletion = deletion.where(
                DocumentChunk.client_id.is_(None), DocumentChunk.source_system.is_(None)
            )
            parent = await session.get(Document, document_id)
            if parent is not None:
                parent.chunk_count = len(rows)
        await session.execute(deletion)
        session.add_all(rows)
        # Force validation/insertion before reporting success, including caller sessions.
        await session.flush()

    async def delete_document_points(
        self,
        document_id: str,
        filters: dict[str, Any] | None = None,
    ) -> None:
        """Delete all chunk points belonging to a specific document_id."""
        if filters:
            for k in filters:
                if k not in ALLOWED_CHUNK_FILTER_COLUMNS:
                    raise SupabaseVectorStoreError(
                        f"Disallowed filter column '{k}'. Allowed columns: {sorted(ALLOWED_CHUNK_FILTER_COLUMNS)}"
                    )
        try:
            async with self.session_factory() as session:
                stmt = delete(DocumentChunk).where(DocumentChunk.document_id == str(document_id).strip())
                if filters:
                    for k, v in filters.items():
                        if k in ("document_id", "document_ids"):
                            continue
                        if hasattr(DocumentChunk, k) and v is not None:
                            if isinstance(v, (list, tuple, set)):
                                stmt = stmt.where(getattr(DocumentChunk, k).in_(list(v)))
                            else:
                                stmt = stmt.where(getattr(DocumentChunk, k) == v)
                else:
                    stmt = stmt.where(
                        DocumentChunk.client_id.is_(None),
                        DocumentChunk.source_system.is_(None),
                    )

                await session.execute(stmt)
                await session.commit()

        except SupabaseVectorStoreError:
            raise
        except Exception as exc:
            logger.warning("Failed to delete document chunks from Supabase pgvector", document_id=document_id, error=str(exc))
            raise SupabaseVectorStoreError(
                f"Failed to delete chunks for document '{document_id}': {exc}"
            ) from exc

    async def count_document_points(
        self,
        document_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> int:
        """Count the number of chunks indexed (overall or for a specific document_id)."""
        if filters:
            for k in filters:
                if k not in ALLOWED_CHUNK_FILTER_COLUMNS:
                    raise SupabaseVectorStoreError(
                        f"Disallowed filter column '{k}'. Allowed columns: {sorted(ALLOWED_CHUNK_FILTER_COLUMNS)}"
                    )
        try:
            async with self.session_factory() as session:
                stmt = select(func.count(DocumentChunk.id))
                if document_id is not None:
                    stmt = stmt.where(DocumentChunk.document_id == str(document_id).strip())
                if filters:
                    for k, v in filters.items():
                        if k in ("document_id", "document_ids"):
                            continue
                        if hasattr(DocumentChunk, k) and v is not None:
                            if isinstance(v, (list, tuple, set)):
                                stmt = stmt.where(getattr(DocumentChunk, k).in_(list(v)))
                            else:
                                stmt = stmt.where(getattr(DocumentChunk, k) == v)
                elif document_id is not None:
                    stmt = stmt.where(
                        DocumentChunk.client_id.is_(None),
                        DocumentChunk.source_system.is_(None),
                    )

                result = await session.execute(stmt)
                return int(result.scalar_one() or 0)

        except SupabaseVectorStoreError:
            raise
        except Exception as exc:
            logger.warning("Failed to count chunks", document_id=document_id, error=str(exc))
            raise SupabaseVectorStoreError(
                f"Failed to count chunks for document '{document_id}': {exc}"
            ) from exc

    async def search_dense(
        self,
        query_vector: list[float],
        limit: int,
        document_id: str | None = None,
        document_ids: list[str] | None = None,
        patient_id: str | None = None,
        source_system: str | None = None,
        query_filter: Any = None,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[DenseSearchResult]:
        """Execute ANN dense vector search using cosine distance in Supabase pgvector."""
        if not query_vector:
            raise SupabaseVectorStoreError("query_vector must not be empty.")
        if limit <= 0:
            raise SupabaseVectorStoreError(f"limit must be positive, got {limit}.")
        if document_ids is not None and len(document_ids) == 0:
            return []

        is_postgres = "postgresql" in engine.dialect.name.lower()

        try:
            async with self.session_factory() as session:
                if is_postgres:
                    # Native PostgreSQL pgvector cosine distance: 1 - (embedding <=> :vec)
                    where_clauses, params = _build_sql_filters(
                        document_id=document_id,
                        document_ids=document_ids,
                        patient_id=patient_id,
                        source_system=source_system,
                        filters=filters,
                    )
                    params["query_vec"] = "[" + ",".join(str(float(x)) for x in query_vector) + "]"
                    params["limit"] = limit

                    if score_threshold is not None:
                        where_clauses.append("(1.0 - (embedding <=> CAST(:query_vec AS vector))) >= :threshold")
                        params["threshold"] = float(score_threshold)

                    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

                    sql = text(f"""
                        SELECT
                            id, point_id, document_id, chunk_index, content, token_count,
                            start_page, end_page, page_numbers, block_types,
                            client_id, tenant_id, collection_id, owner_subject_id, patient_id,
                            source_system, document_type, file_name, metadata_json,
                            (1.0 - (embedding <=> CAST(:query_vec AS vector))) AS score
                        FROM document_chunks
                        WHERE {where_sql}
                        ORDER BY embedding <=> CAST(:query_vec AS vector) ASC
                        LIMIT :limit;
                    """)

                    result = await session.execute(sql, params)
                    rows = result.fetchall()

                    results = []
                    for rank, row in enumerate(rows, start=1):
                        mapping = row._mapping
                        results.append(
                            DenseSearchResult(
                                point_id=str(mapping["point_id"]),
                                score=float(mapping["score"]),
                                document_id=str(mapping["document_id"]),
                                chunk_index=int(mapping["chunk_index"]),
                                content=str(mapping["content"]),
                                token_count=int(mapping["token_count"]),
                                start_page=int(mapping["start_page"]),
                                end_page=int(mapping["end_page"]),
                                page_numbers=list(mapping["page_numbers"] or []),
                                block_types=list(mapping["block_types"] or []),
                                rank=rank,
                                client_id=mapping.get("client_id"),
                                tenant_id=mapping.get("tenant_id"),
                                collection_id=mapping.get("collection_id"),
                                owner_subject_id=mapping.get("owner_subject_id"),
                                patient_id=mapping.get("patient_id"),
                                source_system=mapping.get("source_system"),
                                document_type=mapping.get("document_type"),
                                file_name=mapping.get("file_name"),
                            )
                        )
                    return results

                else:
                    # SQLite fallback: load candidates and calculate cosine similarity in Python
                    stmt = select(DocumentChunk)
                    stmt = _apply_sqlite_filters(
                        stmt,
                        document_id=document_id,
                        document_ids=document_ids,
                        patient_id=patient_id,
                        source_system=source_system,
                        filters=filters,
                    )

                    res = await session.execute(stmt)
                    chunks = res.scalars().all()

                    scored = []
                    for chunk in chunks:
                        sim = _cosine_similarity(query_vector, chunk.embedding)
                        if score_threshold is None or sim >= score_threshold:
                            scored.append((sim, chunk))

                    scored.sort(key=lambda x: x[0], reverse=True)
                    top_scored = scored[:limit]

                    results = []
                    for rank, (sim, chunk) in enumerate(top_scored, start=1):
                        results.append(
                            DenseSearchResult(
                                point_id=chunk.point_id,
                                score=float(sim),
                                document_id=chunk.document_id,
                                chunk_index=chunk.chunk_index,
                                content=chunk.content,
                                token_count=chunk.token_count,
                                start_page=chunk.start_page,
                                end_page=chunk.end_page,
                                page_numbers=list(chunk.page_numbers or []),
                                block_types=list(chunk.block_types or []),
                                rank=rank,
                                client_id=chunk.client_id,
                                tenant_id=chunk.tenant_id,
                                collection_id=chunk.collection_id,
                                owner_subject_id=chunk.owner_subject_id,
                                patient_id=chunk.patient_id,
                                source_system=chunk.source_system,
                                document_type=chunk.document_type,
                                file_name=chunk.file_name,
                            )
                        )
                    return results

        except SupabaseVectorStoreError:
            raise
        except Exception as exc:
            logger.exception("Dense vector search failed in SupabaseVectorStore", error=str(exc))
            raise SupabaseVectorStoreError(f"Dense vector search failed: {exc}") from exc

    async def search_sparse(
        self,
        query_vector: SparseVector | Any,
        limit: int,
        document_id: str | None = None,
        document_ids: list[str] | None = None,
        patient_id: str | None = None,
        source_system: str | None = None,
        query_filter: Any = None,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
        query_text: str | None = None,
        **kwargs: Any,
    ) -> list[SparseSearchResult]:
        """Execute lexical full-text search in PostgreSQL or keyword overlap in SQLite."""
        if limit <= 0:
            raise SupabaseVectorStoreError(f"limit must be positive, got {limit}.")
        if document_ids is not None and len(document_ids) == 0:
            return []

        text_to_query = query_text or ""
        is_postgres = "postgresql" in engine.dialect.name.lower()

        try:
            async with self.session_factory() as session:
                if is_postgres and text_to_query.strip():
                    where_clauses, params = _build_sql_filters(
                        document_id=document_id,
                        document_ids=document_ids,
                        patient_id=patient_id,
                        source_system=source_system,
                        filters=filters,
                    )
                    where_clauses.insert(0, "tsv_content @@ websearch_to_tsquery('english', :query_text)")
                    params["query_text"] = text_to_query.strip()
                    params["limit"] = limit

                    where_sql = " AND ".join(where_clauses)

                    sql = text(f"""
                        SELECT
                            id, point_id, document_id, chunk_index, content, token_count,
                            start_page, end_page, page_numbers, block_types,
                            client_id, tenant_id, collection_id, owner_subject_id, patient_id,
                            source_system, document_type, file_name, metadata_json,
                            ts_rank_cd(tsv_content, websearch_to_tsquery('english', :query_text)) AS score
                        FROM document_chunks
                        WHERE {where_sql}
                        ORDER BY score DESC
                        LIMIT :limit;
                    """)

                    result = await session.execute(sql, params)
                    rows = result.fetchall()

                    results = []
                    for rank, row in enumerate(rows, start=1):
                        mapping = row._mapping
                        results.append(
                            SparseSearchResult(
                                point_id=str(mapping["point_id"]),
                                score=float(mapping["score"]),
                                document_id=str(mapping["document_id"]),
                                chunk_index=int(mapping["chunk_index"]),
                                content=str(mapping["content"]),
                                token_count=int(mapping["token_count"]),
                                start_page=int(mapping["start_page"]),
                                end_page=int(mapping["end_page"]),
                                page_numbers=list(mapping["page_numbers"] or []),
                                block_types=list(mapping["block_types"] or []),
                                rank=rank,
                                client_id=mapping.get("client_id"),
                                tenant_id=mapping.get("tenant_id"),
                                collection_id=mapping.get("collection_id"),
                                owner_subject_id=mapping.get("owner_subject_id"),
                                patient_id=mapping.get("patient_id"),
                                source_system=mapping.get("source_system"),
                                document_type=mapping.get("document_type"),
                                file_name=mapping.get("file_name"),
                            )
                        )
                    return results

                else:
                    # SQLite / fallback: simple token matching
                    stmt = select(DocumentChunk)
                    stmt = _apply_sqlite_filters(
                        stmt,
                        document_id=document_id,
                        document_ids=document_ids,
                        patient_id=patient_id,
                        source_system=source_system,
                        filters=filters,
                    )
                    res = await session.execute(stmt)
                    chunks = res.scalars().all()

                    terms = [t.lower() for t in text_to_query.split() if len(t) > 2]
                    scored = []
                    for chunk in chunks:
                        content_lower = chunk.content.lower()
                        match_count = sum(1 for t in terms if t in content_lower)
                        score = float(match_count) / max(len(terms), 1) if terms else 0.5
                        if score > 0 or not terms:
                            scored.append((score, chunk))

                    scored.sort(key=lambda x: x[0], reverse=True)
                    top_scored = scored[:limit]

                    return [
                        SparseSearchResult(
                            point_id=chunk.point_id,
                            score=float(score),
                            document_id=chunk.document_id,
                            chunk_index=chunk.chunk_index,
                            content=chunk.content,
                            token_count=chunk.token_count,
                            start_page=chunk.start_page,
                            end_page=chunk.end_page,
                            page_numbers=list(chunk.page_numbers or []),
                            block_types=list(chunk.block_types or []),
                            rank=idx + 1,
                            client_id=chunk.client_id,
                            tenant_id=chunk.tenant_id,
                            collection_id=chunk.collection_id,
                            owner_subject_id=chunk.owner_subject_id,
                            patient_id=chunk.patient_id,
                            source_system=chunk.source_system,
                            document_type=chunk.document_type,
                            file_name=chunk.file_name,
                        )
                        for idx, (score, chunk) in enumerate(top_scored)
                    ]

        except SupabaseVectorStoreError:
            raise
        except Exception as exc:
            logger.exception("Sparse search failed in SupabaseVectorStore", error=str(exc))
            raise SupabaseVectorStoreError(f"Sparse search failed: {exc}") from exc

    async def search_hybrid(
        self,
        dense_query_vector: list[float],
        sparse_query_vector: SparseVector | Any,
        limit: int,
        candidate_limit: int,
        document_id: str | None = None,
        document_ids: list[str] | None = None,
        patient_id: str | None = None,
        source_system: str | None = None,
        filter: Any = None,
        filters: dict[str, Any] | None = None,
        query_text: str | None = None,
        **kwargs: Any,
    ) -> list[HybridSearchResult]:
        """
        Execute hybrid retrieval combining dense ANN vector retrieval and lexical retrieval
        using Reciprocal Rank Fusion (RRF) in PostgreSQL.
        """
        if limit <= 0:
            raise SupabaseVectorStoreError(f"limit must be positive, got {limit}.")
        if candidate_limit < limit:
            raise SupabaseVectorStoreError(f"candidate_limit ({candidate_limit}) must be >= limit ({limit}).")
        if document_ids is not None and len(document_ids) == 0:
            return []

        # Retrieve dense candidates
        dense_candidates = await self.search_dense(
            query_vector=dense_query_vector,
            limit=candidate_limit,
            document_id=document_id,
            document_ids=document_ids,
            patient_id=patient_id,
            source_system=source_system,
            filters=filters,
        )

        # Retrieve sparse lexical candidates
        sparse_candidates = await self.search_sparse(
            query_vector=sparse_query_vector,
            limit=candidate_limit,
            document_id=document_id,
            document_ids=document_ids,
            patient_id=patient_id,
            source_system=source_system,
            filters=filters,
            query_text=query_text,
        )

        # Reciprocal Rank Fusion (RRF): score = sum(1 / (60 + rank))
        rrf_scores: dict[str, float] = {}
        candidate_map: dict[str, DenseSearchResult | SparseSearchResult] = {}

        for rank, item in enumerate(dense_candidates, start=1):
            rrf_scores[item.point_id] = rrf_scores.get(item.point_id, 0.0) + (1.0 / (60.0 + rank))
            candidate_map[item.point_id] = item

        for rank, item in enumerate(sparse_candidates, start=1):
            rrf_scores[item.point_id] = rrf_scores.get(item.point_id, 0.0) + (1.0 / (60.0 + rank))
            if item.point_id not in candidate_map:
                candidate_map[item.point_id] = item

        # Sort by RRF score descending
        sorted_point_ids = sorted(rrf_scores.keys(), key=lambda pid: rrf_scores[pid], reverse=True)

        results: list[HybridSearchResult] = []
        for pid in sorted_point_ids:
            source_item = candidate_map[pid]
            score = rrf_scores[pid]

            # Defense-in-depth boundary checks
            if document_id is not None and source_item.document_id != str(document_id).strip():
                continue
            if document_ids is not None and source_item.document_id not in [str(did).strip() for did in document_ids]:
                continue
            if patient_id is not None and source_item.patient_id != str(patient_id).strip():
                continue
            if source_system is not None and source_item.source_system != str(source_system).strip():
                continue
            if filters:
                mismatch = False
                for fk, fv in filters.items():
                    if fv is None or fk in ("document_id", "document_ids"):
                        continue
                    item_val = getattr(source_item, fk, None)
                    if isinstance(fv, (list, tuple, set)):
                        if item_val not in [str(x) for x in fv]:
                            mismatch = True
                            break
                    elif item_val != str(fv).strip() if isinstance(fv, str) else item_val != fv:
                        mismatch = True
                        break
                if mismatch:
                    continue

            results.append(
                HybridSearchResult(
                    point_id=source_item.point_id,
                    score=float(score),
                    document_id=source_item.document_id,
                    chunk_index=source_item.chunk_index,
                    content=source_item.content,
                    token_count=source_item.token_count,
                    start_page=source_item.start_page,
                    end_page=source_item.end_page,
                    page_numbers=list(source_item.page_numbers),
                    block_types=list(source_item.block_types),
                    rank=len(results) + 1,
                    client_id=source_item.client_id,
                    tenant_id=source_item.tenant_id,
                    collection_id=source_item.collection_id,
                    owner_subject_id=source_item.owner_subject_id,
                    patient_id=source_item.patient_id,
                    source_system=source_item.source_system,
                    document_type=source_item.document_type,
                    file_name=source_item.file_name,
                )
            )
            if len(results) >= limit:
                break

        return results

    async def close(self) -> None:
        """Close vector store resources."""
        pass

    async def __aenter__(self) -> "SupabaseVectorStore":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
