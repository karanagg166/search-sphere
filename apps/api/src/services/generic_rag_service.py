import json
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.external_document import ExternalDocument
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.search import ConversationMessage
from src.schemas.v1.answers import (
    AnswerCitation,
    AnswerRequest,
    AnswerResponse,
)
from src.schemas.v1.search import (
    SearchChunkResult,
    SearchRequest,
    SearchResponse,
)
from src.security.service_context import ServiceContext
from src.services.answer_generator import (
    ANSWER_SYSTEM_PREAMBLE,
    NO_RESULTS_ANSWER,
    AnswerGenerator,
    extract_citation_numbers,
    sanitize_citations,
    format_context_chunks,
)

logger = structlog.get_logger()


class GenericRagService:
    def __init__(
        self,
        db: AsyncSession,
        retriever: RerankedHybridRetriever,
        answer_generator: AnswerGenerator,
    ):
        self.db = db
        self.retriever = retriever
        self.answer_generator = answer_generator

    def _build_qdrant_filters(
        self,
        context: ServiceContext,
        collection_id: str | None = None,
        owner_subject_id: str | None = None,
        document_type: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Builds mandatory security filters plus optional user filters."""
        # 1. Mandatory isolation filter
        filters = context.to_qdrant_filter(
            collection_id=collection_id,
            owner_subject_id=owner_subject_id,
        )

        # 2. Add document_type if specified
        if document_type:
            clean_type = document_type.strip().upper()
            if clean_type:
                filters["document_type"] = clean_type

        # 3. Add caller metadata filters without letting them override security bounds
        if metadata_filters:
            for k, v in metadata_filters.items():
                if k not in ("client_id", "tenant_id", "collection_id", "owner_subject_id", "source_system", "patient_id"):
                    filters[k] = v

        return filters

    async def _verify_and_enrich_chunks(
        self,
        context: ServiceContext,
        raw_chunks: list[Any],
    ) -> list[tuple[Any, ExternalDocument]]:
        """
        Verifies retrieved chunks against authoritative PostgreSQL database metadata.
        Ensures chunks belong to READY, non-deleted documents in the authorized client & tenant scope.
        """
        if not raw_chunks:
            return []

        doc_ids = list({chunk.document_id for chunk in raw_chunks})
        # Check both id and external_document_id
        stmt = select(ExternalDocument).where(
            ExternalDocument.source_system == context.client_id,
            ExternalDocument.tenant_id == context.tenant_id,
            (ExternalDocument.id.in_(doc_ids) | ExternalDocument.external_document_id.in_(doc_ids)),
            ExternalDocument.status == "READY",
        )
        if context.subject_id:
            stmt = stmt.where(ExternalDocument.owner_subject_id == context.subject_id)
        if context.collection_id:
            stmt = stmt.where(ExternalDocument.collection_id == context.collection_id)
        res = await self.db.execute(stmt)
        docs = list(res.scalars().all())

        # Build lookup map by both id and external_document_id
        doc_map: dict[str, ExternalDocument] = {}
        for d in docs:
            doc_map[d.id] = d
            doc_map[d.external_document_id] = d

        verified: list[tuple[Any, ExternalDocument]] = []
        for chunk in raw_chunks:
            doc = doc_map.get(chunk.document_id)
            if doc:
                expected = context.to_qdrant_filter()
                if any(getattr(chunk, key, None) != value for key, value in expected.items()):
                    continue
                verified.append((chunk, doc))

        return verified

    async def search(
        self,
        context: ServiceContext,
        request: SearchRequest,
    ) -> SearchResponse:
        """Executes two-stage reranked hybrid search scoped strictly to authenticated client & tenant."""
        context = context.resolve_scope(request.collection_id, request.owner_subject_id)
        start_time = time.perf_counter()
        clean_query = request.query.strip()
        if not clean_query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search query cannot be empty or whitespace only.",
            )

        resolved_limit = max(1, min(request.limit, 50))
        candidate_k = max(resolved_limit * 2, 20)

        filters = self._build_qdrant_filters(
            context=context,
            collection_id=request.collection_id,
            owner_subject_id=request.owner_subject_id,
            document_type=request.document_type,
            metadata_filters=request.metadata_filters,
        )

        try:
            raw_chunks = await self.retriever.search(
                query=clean_query,
                top_k=resolved_limit,
                candidate_k=candidate_k,
                filters=filters,
            )
        except Exception as exc:
            logger.exception("Generic retrieval failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Retrieval is temporarily unavailable.",
            ) from exc

        verified_pairs = await self._verify_and_enrich_chunks(context, raw_chunks)

        results: list[SearchChunkResult] = []
        for idx, (chunk, doc) in enumerate(verified_pairs, start=1):
            chunk_id = getattr(chunk, "point_id", getattr(chunk, "id", f"chunk_{idx}"))
            results.append(
                SearchChunkResult(
                    chunk_id=chunk_id,
                    document_id=doc.external_document_id,
                    text=chunk.content,
                    score=round(float(chunk.score), 4),
                    rank=idx,
                    start_page=chunk.start_page,
                    end_page=chunk.end_page,
                    block_types=chunk.block_types or [],
                    client_id=doc.client_id,
                    tenant_id=doc.tenant_id,
                    collection_id=doc.collection_id,
                    owner_subject_id=doc.owner_subject_id,
                    document_type=doc.document_type,
                    file_name=doc.file_name,
                )
            )

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return SearchResponse(
            query=clean_query,
            total=len(results),
            results=results,
            duration_ms=duration_ms,
        )

    async def answer(
        self,
        context: ServiceContext,
        request: AnswerRequest,
    ) -> AnswerResponse:
        """Generates grounded answer with source citations based on retrieved documents."""
        context = context.resolve_scope(request.collection_id, request.owner_subject_id)
        start_time = time.perf_counter()
        clean_query = request.query.strip()
        if not clean_query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Query cannot be empty or whitespace only.",
            )

        resolved_limit = max(1, min(request.limit, 20))
        candidate_k = max(resolved_limit * 2, 20)

        filters = self._build_qdrant_filters(
            context=context,
            collection_id=request.collection_id,
            owner_subject_id=request.owner_subject_id,
            document_type=request.document_type,
            metadata_filters=request.metadata_filters,
        )

        try:
            raw_chunks = await self.retriever.search(
                query=clean_query,
                top_k=resolved_limit,
                candidate_k=candidate_k,
                filters=filters,
            )
        except Exception as exc:
            logger.exception("Retrieval failed during answer generation", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Retrieval is temporarily unavailable.",
            ) from exc

        verified_pairs = await self._verify_and_enrich_chunks(context, raw_chunks)
        verified_chunks = [pair[0] for pair in verified_pairs]
        doc_lookup = {getattr(pair[0], "point_id", getattr(pair[0], "id", None)): pair[1] for pair in verified_pairs}

        if not verified_chunks:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return AnswerResponse(
                answer=NO_RESULTS_ANSWER,
                citations=[],
                retrieved_chunk_count=0,
                duration_ms=duration_ms,
            )

        conversation_context: list[ConversationMessage] | None = None
        if request.conversation_history:
            conversation_context = [
                ConversationMessage(role=m.role, content=m.content)
                for m in request.conversation_history
            ]

        preamble = request.system_prompt or ANSWER_SYSTEM_PREAMBLE

        try:
            answer_text, verified_chunks = await self.answer_generator.generate_answer(
                query=clean_query,
                chunks=verified_chunks,
                conversation_context=conversation_context,
                preamble=preamble,
                no_results_answer=NO_RESULTS_ANSWER,
            )
        except Exception as exc:
            logger.error("Answer generation LLM failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Answer generation is temporarily unavailable.",
            ) from exc

        answer_text = sanitize_citations(answer_text, len(verified_chunks))
        # Extract citation numbers and build citation list
        cited_numbers = extract_citation_numbers(answer_text, len(verified_chunks))
        citations: list[AnswerCitation] = []
        for num in cited_numbers:
            chunk = verified_chunks[num - 1]
            chunk_pid = getattr(chunk, "point_id", getattr(chunk, "id", None))
            doc = doc_lookup.get(chunk_pid)
            citations.append(
                AnswerCitation(
                    citation_number=num,
                    chunk_id=chunk_pid or str(num),
                    document_id=doc.external_document_id if doc else chunk.document_id,
                    file_name=doc.file_name if doc else None,
                    page_number=chunk.start_page,
                    text_snippet=chunk.content[:200],
                    collection_id=doc.collection_id if doc else None,
                    owner_subject_id=doc.owner_subject_id if doc else None,
                )
            )

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return AnswerResponse(
            answer=answer_text,
            citations=citations,
            retrieved_chunk_count=len(verified_chunks),
            duration_ms=duration_ms,
        )

    async def answer_stream(
        self,
        context: ServiceContext,
        request: AnswerRequest,
    ) -> AsyncIterator[str]:
        """Streams grounded answer tokens via Server-Sent Events (SSE)."""
        context = context.resolve_scope(request.collection_id, request.owner_subject_id)
        clean_query = request.query.strip()
        if not clean_query:
            yield f"data: {json.dumps({'event': 'error', 'detail': 'Empty query'})}\n\n"
            yield "data: [DONE]\n\n"
            return

        resolved_limit = max(1, min(request.limit, 20))
        candidate_k = max(resolved_limit * 2, 20)

        filters = self._build_qdrant_filters(
            context=context,
            collection_id=request.collection_id,
            owner_subject_id=request.owner_subject_id,
            document_type=request.document_type,
            metadata_filters=request.metadata_filters,
        )

        raw_chunks = await self.retriever.search(
            query=clean_query,
            top_k=resolved_limit,
            candidate_k=candidate_k,
            filters=filters,
        )

        verified_pairs = await self._verify_and_enrich_chunks(context, raw_chunks)
        verified_chunks = [pair[0] for pair in verified_pairs]
        doc_lookup = {getattr(pair[0], "point_id", getattr(pair[0], "id", None)): pair[1] for pair in verified_pairs}

        if not verified_chunks:
            yield f"data: {json.dumps({'event': 'token', 'text': NO_RESULTS_ANSWER})}\n\n"
            yield f"data: {json.dumps({'event': 'citations', 'citations': []})}\n\n"
            yield "data: [DONE]\n\n"
            return

        conversation_context: list[ConversationMessage] | None = None
        if request.conversation_history:
            conversation_context = [
                ConversationMessage(role=m.role, content=m.content)
                for m in request.conversation_history
            ]

        preamble = request.system_prompt or ANSWER_SYSTEM_PREAMBLE

        accumulated_text = []
        token_stream, verified_chunks = await self.answer_generator.generate_answer_stream(
            query=clean_query,
            chunks=verified_chunks,
            conversation_context=conversation_context,
            preamble=preamble,
            no_results_answer=NO_RESULTS_ANSWER,
        )
        async for token in token_stream:
            accumulated_text.append(token)
            yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"

        full_answer = "".join(accumulated_text)
        cited_numbers = extract_citation_numbers(full_answer, len(verified_chunks))
        citations: list[dict[str, Any]] = []
        for num in cited_numbers:
            chunk = verified_chunks[num - 1]
            chunk_pid = getattr(chunk, "point_id", getattr(chunk, "id", None))
            doc = doc_lookup.get(chunk_pid)
            citations.append(
                {
                    "citation_number": num,
                    "chunk_id": chunk_pid or str(num),
                    "document_id": doc.external_document_id if doc else chunk.document_id,
                    "file_name": doc.file_name if doc else None,
                    "page_number": chunk.start_page,
                    "text_snippet": chunk.content[:200],
                    "collection_id": doc.collection_id if doc else None,
                    "owner_subject_id": doc.owner_subject_id if doc else None,
                }
            )

        yield f"data: {json.dumps({'event': 'citations', 'citations': citations})}\n\n"
        yield "data: [DONE]\n\n"
