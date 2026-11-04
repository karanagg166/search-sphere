import json
import math
import uuid
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
    QDRANT_POINT_NAMESPACE,
    QdrantVectorStoreError,
    generate_point_id,
)

logger = structlog.get_logger()


class SupabaseVectorStoreError(QdrantVectorStoreError):
    """Raised when an operation against Supabase pgvector store fails."""


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Calculate cosine similarity between two float vectors in Python."""
    if len(v1) != len(v2) or not v1:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
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
    ) -> None:
        self.collection_name = collection_name or getattr(settings, "QDRANT_COLLECTION_NAME", "documents")
        self.vector_dimension = vector_dimension or 384
        self.batch_size = batch_size or getattr(settings, "QDRANT_UPSERT_BATCH_SIZE", 100)
        self.session_factory = session_factory or AsyncSessionLocal
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
    ) -> int:
        """
        Synchronize PostgreSQL document_chunks table with an EmbeddedDocument.

        Steps:
        1. Ensure table exists.
        2. Delete any existing chunks for this document (prevent stale chunks on retry).
        3. Convert EmbeddedChunks to DocumentChunk ORM rows with deterministic UUID point_ids.
        4. Batch insert chunks into PostgreSQL.
        5. Atomically update documents.chunk_count if matching document exists.
        6. Return total chunks written.
        """
        await self.ensure_collection()

        # 1. Clean up existing chunks for this document scope
        filters = {}
        if extra_payload and extra_payload.get("client_id"):
            filters = {k: extra_payload[k] for k in ("client_id", "tenant_id") if k in extra_payload}
            await self.delete_document_points(document_id, filters=filters)
        else:
            await self.delete_document_points(document_id)

        if not document.chunks:
            logger.info("Document has no chunks to index; cleaned up existing points", document_id=document_id)
            return 0

        total_chunks = len(document.chunks)
        identity = document_id
        if extra_payload and extra_payload.get("client_id"):
            identity = json.dumps([extra_payload["client_id"], extra_payload.get("tenant_id"), document_id], separators=(",", ":"))

        chunks_to_insert: list[DocumentChunk] = []
        for idx, chunk in enumerate(document.chunks):
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
                metadata_json=extra_meta,
            )
            chunks_to_insert.append(row)

        try:
            async with self.session_factory() as session:
                session.add_all(chunks_to_insert)

                # Update document chunk_count telemetry if parent document exists
                doc_res = await session.execute(
                    select(Document).where(Document.id == document_id)
                )
                parent_doc = doc_res.scalar_one_or_none()
                if parent_doc:
                    parent_doc.chunk_count = total_chunks

                await session.commit()

            logger.info(
                "Document chunks successfully indexed in Supabase pgvector",
                document_id=document_id,
                chunks_written=total_chunks,
            )
            return total_chunks

        except Exception as exc:
            logger.exception("Failed to insert document chunks into Supabase pgvector", document_id=document_id, error=str(exc))
            raise SupabaseVectorStoreError(
                f"Failed to index document '{document_id}' into pgvector: {exc}"
            ) from exc

    async def delete_document_points(
        self,
        document_id: str,
        filters: dict[str, Any] | None = None,
    ) -> None:
        """Delete all chunk points belonging to a specific document_id."""
        try:
            async with self.session_factory() as session:
                stmt = delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
                if filters:
                    for k, v in filters.items():
                        if hasattr(DocumentChunk, k) and v is not None:
                            stmt = stmt.where(getattr(DocumentChunk, k) == v)
                else:
                    stmt = stmt.where(
                        DocumentChunk.client_id.is_(None),
                        DocumentChunk.source_system.is_(None),
                    )

                await session.execute(stmt)
                await session.commit()

        except Exception as exc:
            logger.warning("Failed to delete document chunks from Supabase pgvector", document_id=document_id, error=str(exc))
            raise SupabaseVectorStoreError(
                f"Failed to delete chunks for document '{document_id}': {exc}"
            ) from exc

    async def count_document_points(
        self,
        document_id: str,
        filters: dict[str, Any] | None = None,
    ) -> int:
        """Count the number of chunks indexed for a specific document_id."""
        try:
            async with self.session_factory() as session:
                stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document_id)
                if filters:
                    for k, v in filters.items():
                        if hasattr(DocumentChunk, k) and v is not None:
                            stmt = stmt.where(getattr(DocumentChunk, k) == v)
                else:
                    stmt = stmt.where(
                        DocumentChunk.client_id.is_(None),
                        DocumentChunk.source_system.is_(None),
                    )

                result = await session.execute(stmt)
                return int(result.scalar_one() or 0)

        except Exception as exc:
            logger.warning("Failed to count chunks for document", document_id=document_id, error=str(exc))
            raise SupabaseVectorStoreError(
                f"Failed to count chunks for document '{document_id}': {exc}"
            ) from exc

    async def search_dense(
        self,
        query_vector: list[float],
        limit: int,
        document_id: str | None = None,
        patient_id: str | None = None,
        source_system: str | None = None,
        query_filter: Any = None,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DenseSearchResult]:
        """Execute ANN dense vector search using cosine distance in Supabase pgvector."""
        if not query_vector:
            raise SupabaseVectorStoreError("query_vector must not be empty.")
        if limit <= 0:
            raise SupabaseVectorStoreError(f"limit must be positive, got {limit}.")

        is_postgres = "postgresql" in engine.dialect.name.lower()

        try:
            async with self.session_factory() as session:
                if is_postgres:
                    # Native PostgreSQL pgvector cosine distance: 1 - (embedding <=> :vec)
                    where_clauses = ["1=1"]
                    params: dict[str, Any] = {
                        "query_vec": "[" + ",".join(str(float(x)) for x in query_vector) + "]",
                        "limit": limit,
                    }

                    if document_id is not None:
                        where_clauses.append("document_id = :doc_id")
                        params["doc_id"] = document_id

                    resolved_patient = patient_id or (filters.get("patient_id") if filters else None)
                    if resolved_patient:
                        where_clauses.append("patient_id = :patient_id")
                        params["patient_id"] = resolved_patient

                    resolved_source = source_system or (filters.get("source_system") if filters else None)
                    if resolved_source:
                        where_clauses.append("source_system = :source_system")
                        params["source_system"] = resolved_source

                    if filters:
                        for k, v in filters.items():
                            if k not in ("patient_id", "source_system", "document_id") and v is not None:
                                param_name = f"filter_{k}"
                                if isinstance(v, (list, tuple)):
                                    where_clauses.append(f"{k} = ANY(:{param_name})")
                                    params[param_name] = list(v)
                                else:
                                    where_clauses.append(f"{k} = :{param_name}")
                                    params[param_name] = v

                    if score_threshold is not None:
                        where_clauses.append("(1.0 - (embedding <=> CAST(:query_vec AS vector))) >= :threshold")
                        params["threshold"] = float(score_threshold)

                    sql = text(f"""
                        SELECT 
                            id, point_id, document_id, chunk_index, content, token_count,
                            start_page, end_page, page_numbers, block_types,
                            client_id, tenant_id, collection_id, owner_subject_id, patient_id,
                            source_system, document_type, file_name, metadata_json,
                            (1.0 - (embedding <=> CAST(:query_vec AS vector))) AS score
                        FROM document_chunks
                        WHERE {" AND ".join(where_clauses)}
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
                    if document_id is not None:
                        stmt = stmt.where(DocumentChunk.document_id == document_id)
                    resolved_patient = patient_id or (filters.get("patient_id") if filters else None)
                    if resolved_patient:
                        stmt = stmt.where(DocumentChunk.patient_id == resolved_patient)
                    resolved_source = source_system or (filters.get("source_system") if filters else None)
                    if resolved_source:
                        stmt = stmt.where(DocumentChunk.source_system == resolved_source)
                    if filters:
                        for k, v in filters.items():
                            if hasattr(DocumentChunk, k) and v is not None:
                                if isinstance(v, (list, tuple)):
                                    stmt = stmt.where(getattr(DocumentChunk, k).in_(list(v)))
                                else:
                                    stmt = stmt.where(getattr(DocumentChunk, k) == v)

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

        except Exception as exc:
            logger.exception("Dense vector search failed in SupabaseVectorStore", error=str(exc))
            raise SupabaseVectorStoreError(f"Dense vector search failed: {exc}") from exc

    async def search_sparse(
        self,
        query_vector: SparseVector | Any,
        limit: int,
        document_id: str | None = None,
        patient_id: str | None = None,
        source_system: str | None = None,
        query_filter: Any = None,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
        query_text: str | None = None,
    ) -> list[SparseSearchResult]:
        """Execute lexical full-text search in PostgreSQL or keyword overlap in SQLite."""
        if limit <= 0:
            raise SupabaseVectorStoreError(f"limit must be positive, got {limit}.")

        text_to_query = query_text or ""
        is_postgres = "postgresql" in engine.dialect.name.lower()

        try:
            async with self.session_factory() as session:
                if is_postgres and text_to_query.strip():
                    where_clauses = ["tsv_content @@ websearch_to_tsquery('english', :query_text)"]
                    params: dict[str, Any] = {
                        "query_text": text_to_query.strip(),
                        "limit": limit,
                    }

                    if document_id is not None:
                        where_clauses.append("document_id = :doc_id")
                        params["doc_id"] = document_id

                    resolved_patient = patient_id or (filters.get("patient_id") if filters else None)
                    if resolved_patient:
                        where_clauses.append("patient_id = :patient_id")
                        params["patient_id"] = resolved_patient

                    resolved_source = source_system or (filters.get("source_system") if filters else None)
                    if resolved_source:
                        where_clauses.append("source_system = :source_system")
                        params["source_system"] = resolved_source

                    sql = text(f"""
                        SELECT 
                            id, point_id, document_id, chunk_index, content, token_count,
                            start_page, end_page, page_numbers, block_types,
                            client_id, tenant_id, collection_id, owner_subject_id, patient_id,
                            source_system, document_type, file_name, metadata_json,
                            ts_rank_cd(tsv_content, websearch_to_tsquery('english', :query_text)) AS score
                        FROM document_chunks
                        WHERE {" AND ".join(where_clauses)}
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
                    if document_id is not None:
                        stmt = stmt.where(DocumentChunk.document_id == document_id)
                    res = await session.execute(stmt)
                    chunks = res.scalars().all()

                    terms = [t.lower() for t in text_to_query.split() if len(t) > 2]
                    scored = []
                    for chunk in chunks:
                        content_lower = chunk.content.lower()
                        match_count = sum(1 for t in terms if t in content_lower)
                        score = float(match_count) / max(len(terms), 1) if terms else 0.5
                        if score > 0:
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
    ) -> list[HybridSearchResult]:
        """
        Execute hybrid retrieval combining dense ANN vector retrieval and lexical retrieval
        using Reciprocal Rank Fusion (RRF) in PostgreSQL.
        """
        if limit <= 0:
            raise SupabaseVectorStoreError(f"limit must be positive, got {limit}.")
        if candidate_limit < limit:
            raise SupabaseVectorStoreError(f"candidate_limit ({candidate_limit}) must be >= limit ({limit}).")

        # Fallback to dual retrieval + Python RRF if SQLite or text search not directly executed in SQL
        is_postgres = "postgresql" in engine.dialect.name.lower()

        # Retrieve dense candidates
        dense_candidates = await self.search_dense(
            query_vector=dense_query_vector,
            limit=candidate_limit,
            document_id=document_id,
            patient_id=patient_id,
            source_system=source_system,
            filters=filters,
        )

        # Retrieve sparse lexical candidates
        sparse_candidates = await self.search_sparse(
            query_vector=sparse_query_vector,
            limit=candidate_limit,
            document_id=document_id,
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
        top_point_ids = sorted_point_ids[:limit]

        results: list[HybridSearchResult] = []
        for rank, pid in enumerate(top_point_ids, start=1):
            source_item = candidate_map[pid]
            score = rrf_scores[pid]

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
                    rank=rank,
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

        return results

    async def close(self) -> None:
        """Close vector store resources."""
        pass

    async def __aenter__(self) -> "SupabaseVectorStore":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
