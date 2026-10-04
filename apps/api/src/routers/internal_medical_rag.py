import json
import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.external_document import ExternalDocument
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.routers.internal_medical_documents import (
    sanitize_identifier,
    verify_service_secret,
)
from src.schemas.internal_medical_rag import (
    MedicalAnswerCitation,
    MedicalChatMessage,
    MedicalChatRequest,
    MedicalChatResponse,
    MedicalRagAnswerRequest,
    MedicalRagAnswerResponse,
)
from src.schemas.search import ConversationMessage
from src.services.answer_generator import (
    MEDICAL_ANSWER_SYSTEM_PREAMBLE,
    MEDICAL_NO_RESULTS_ANSWER,
    AnswerGenerationConfigError,
    AnswerGenerationTimeoutError,
    AnswerGenerationUnavailableError,
    AnswerGenerator,
    extract_citation_numbers,
    format_medical_context_chunks,
    get_answer_generator,
    sanitize_citations,
)
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/medical-rag", tags=["Internal Medical RAG"])


async def _retrieve_patient_ready_chunks(
    patient_id: str,
    query: str,
    limit: int,
    document_type: str | None,
    session: AsyncSession,
    retriever: RerankedHybridRetriever,
) -> tuple[list[Any], dict[str, ExternalDocument]]:
    """
    Executes patient-isolated hybrid retrieval and filters for READY PostgreSQL records.
    """
    resolved_limit = max(1, min(limit, 20))
    candidate_k = max(resolved_limit * 2, 20)

    # Strict server-side patient filtering in vector database
    qdrant_filters: dict[str, Any] = {
        "source_system": "quick_clinic",
        "patient_id": patient_id,
    }
    if document_type:
        clean_doc_type = document_type.strip().upper()
        if clean_doc_type:
            qdrant_filters["document_type"] = clean_doc_type

    reranked_chunks = await retriever.search(
        query=query,
        top_k=resolved_limit,
        candidate_k=candidate_k,
        filters=qdrant_filters,
    )

    if not reranked_chunks:
        return [], {}

    # Verification against primary PostgreSQL document records
    chunk_doc_ids = list({chunk.document_id for chunk in reranked_chunks})
    db_result = await session.execute(
        select(ExternalDocument).where(
            ExternalDocument.source_system == "quick_clinic",
            ExternalDocument.external_patient_id == patient_id,
            ExternalDocument.external_document_id.in_(chunk_doc_ids),
            ExternalDocument.status == "READY",
        )
    )
    ready_docs = {doc.external_document_id: doc for doc in db_result.scalars().all()}

    ready_chunks: list[Any] = []
    for chunk in reranked_chunks:
        if chunk.document_id not in ready_docs:
            continue
        if chunk.patient_id and chunk.patient_id != patient_id:
            logger.error(
                "Security anomaly: chunk patient_id mismatch detected",
                expected_patient=patient_id,
                chunk_patient=chunk.patient_id,
                document_id=chunk.document_id,
            )
            continue
        ready_chunks.append(chunk)

    return ready_chunks, ready_docs


def _build_citations(
    clean_answer: str,
    used_chunks: list[Any],
    ready_docs: dict[str, ExternalDocument],
) -> list[MedicalAnswerCitation]:
    """Map used chunk metadata to clean citation objects."""
    cited_ids = extract_citation_numbers(clean_answer, max_source_id=len(used_chunks))
    citations: list[MedicalAnswerCitation] = []

    for cid in cited_ids:
        chunk = used_chunks[cid - 1]
        doc_meta = ready_docs.get(chunk.document_id)

        report_date_str = chunk.report_date
        if not report_date_str and doc_meta and doc_meta.report_date:
            report_date_str = doc_meta.report_date.isoformat()

        page_num = chunk.start_page
        if page_num is None and chunk.page_numbers:
            page_num = chunk.page_numbers[0]

        file_name = chunk.file_name or (doc_meta.file_name if doc_meta else "unknown")
        doc_type = chunk.document_type or (doc_meta.document_type if doc_meta else "UNKNOWN")
        score_val = float(chunk.score if chunk.score is not None else getattr(chunk, "rerank_score", 0.0))

        citations.append(
            MedicalAnswerCitation(
                citationId=cid,
                documentId=chunk.document_id,
                fileName=file_name,
                documentType=doc_type,
                reportDate=report_date_str,
                pageNumber=page_num,
                chunkIndex=chunk.chunk_index,
                content=chunk.content,
                score=round(score_val, 4),
            )
        )

    return citations


@router.post(
    "/answer",
    response_model=MedicalRagAnswerResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Secure patient-isolated grounded medical RAG answer generation",
)
async def generate_medical_answer(
    body: MedicalRagAnswerRequest,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> MedicalRagAnswerResponse:
    start_time = time.perf_counter()

    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")
    clean_query = body.query.strip()
    if not clean_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query cannot be empty or whitespace only.",
        )

    try:
        ready_chunks, ready_docs = await _retrieve_patient_ready_chunks(
            patient_id=clean_patient_id,
            query=clean_query,
            limit=body.limit,
            document_type=body.document_type,
            session=session,
            retriever=retriever,
        )
    except Exception as exc:
        logger.exception("Medical retrieval failed", patient_id=clean_patient_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Medical retrieval execution failed: {exc}",
        ) from exc

    if not ready_chunks:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "Medical RAG completed with zero chunks found",
            patient_id=clean_patient_id,
            result_count=0,
            citation_count=0,
            duration_ms=duration_ms,
        )
        return MedicalRagAnswerResponse(
            answer=MEDICAL_NO_RESULTS_ANSWER,
            citations=[],
            result_count=0,
        )

    try:
        raw_answer_text, used_chunks = await answer_generator.generate_answer(
            query=clean_query,
            chunks=ready_chunks,
            preamble=MEDICAL_ANSWER_SYSTEM_PREAMBLE,
            context_formatter=format_medical_context_chunks,
            no_results_answer=MEDICAL_NO_RESULTS_ANSWER,
        )
    except AnswerGenerationTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Medical answer generation timed out. Please try again.",
        ) from exc
    except (AnswerGenerationUnavailableError, AnswerGenerationConfigError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Medical answer generation service is currently unavailable.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while generating medical answer.",
        ) from exc

    clean_answer = sanitize_citations(raw_answer_text, max_source_id=len(used_chunks))
    citations = _build_citations(clean_answer, used_chunks, ready_docs)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    logger.info(
        "Medical RAG answer generated successfully",
        patient_id=clean_patient_id,
        result_count=len(ready_chunks),
        citation_count=len(citations),
        duration_ms=duration_ms,
    )

    return MedicalRagAnswerResponse(
        answer=clean_answer,
        citations=citations,
        result_count=len(ready_chunks),
    )


@router.post(
    "/chat",
    response_model=MedicalChatResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Multi-turn patient-isolated medical chat answer generation",
)
async def generate_medical_chat(
    body: MedicalChatRequest,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> MedicalChatResponse:
    start_time = time.perf_counter()

    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")
    clean_message = body.message.strip()
    if not clean_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty or whitespace only.",
        )

    # 1. Limit history to bounded context (last 6 messages)
    limited_history = body.history[-6:] if body.history else []
    conversation_context = [
        ConversationMessage(
            role="user" if msg.role.lower() == "user" else "assistant",
            content=msg.content.strip(),
        )
        for msg in limited_history
        if msg.content and msg.content.strip()
    ]

    # 2. Multi-turn query reformulation (used strictly for retrieval, never for authorization)
    effective_query = clean_message
    rewritten = False
    if conversation_context:
        rewrite_result = await query_rewriter.rewrite(
            query=clean_message,
            conversation_context=conversation_context,
        )
        if rewrite_result.rewritten and rewrite_result.retrieval_query:
            effective_query = rewrite_result.retrieval_query
            rewritten = True

    # 3. Patient-isolated retrieval with mandatory patient_id constraint
    try:
        ready_chunks, ready_docs = await _retrieve_patient_ready_chunks(
            patient_id=clean_patient_id,
            query=effective_query,
            limit=body.limit,
            document_type=body.document_type,
            session=session,
            retriever=retriever,
        )
    except Exception as exc:
        logger.exception("Medical chat retrieval failed", patient_id=clean_patient_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Medical retrieval execution failed: {exc}",
        ) from exc

    if not ready_chunks:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "Medical chat completed with zero chunks found",
            patient_id=clean_patient_id,
            history_count=len(limited_history),
            result_count=0,
            citation_count=0,
            duration_ms=duration_ms,
        )
        return MedicalChatResponse(
            answer=MEDICAL_NO_RESULTS_ANSWER,
            citations=[],
            result_count=0,
            retrieval_query=effective_query,
            rewritten=rewritten,
        )

    try:
        raw_answer_text, used_chunks = await answer_generator.generate_answer(
            query=effective_query,
            chunks=ready_chunks,
            conversation_context=conversation_context,
            preamble=MEDICAL_ANSWER_SYSTEM_PREAMBLE,
            context_formatter=format_medical_context_chunks,
            no_results_answer=MEDICAL_NO_RESULTS_ANSWER,
        )
    except AnswerGenerationTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Medical answer generation timed out.",
        ) from exc
    except (AnswerGenerationUnavailableError, AnswerGenerationConfigError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Medical answer generation service is currently unavailable.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while generating medical answer.",
        ) from exc

    clean_answer = sanitize_citations(raw_answer_text, max_source_id=len(used_chunks))
    citations = _build_citations(clean_answer, used_chunks, ready_docs)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    logger.info(
        "Medical chat answer generated successfully",
        patient_id=clean_patient_id,
        history_count=len(limited_history),
        result_count=len(ready_chunks),
        citation_count=len(citations),
        duration_ms=duration_ms,
    )

    return MedicalChatResponse(
        answer=clean_answer,
        citations=citations,
        result_count=len(ready_chunks),
        retrieval_query=effective_query,
        rewritten=rewritten,
    )


@router.post(
    "/chat/stream",
    status_code=status.HTTP_200_OK,
    summary="Stream multi-turn medical chat answer via SSE",
)
async def stream_medical_chat(
    body: MedicalChatRequest,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> StreamingResponse:
    start_time = time.perf_counter()

    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")
    clean_message = body.message.strip()
    if not clean_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty or whitespace only.",
        )

    limited_history = body.history[-6:] if body.history else []
    conversation_context = [
        ConversationMessage(
            role="user" if msg.role.lower() == "user" else "assistant",
            content=msg.content.strip(),
        )
        for msg in limited_history
        if msg.content and msg.content.strip()
    ]

    effective_query = clean_message
    if conversation_context:
        rewrite_result = await query_rewriter.rewrite(
            query=clean_message,
            conversation_context=conversation_context,
        )
        if rewrite_result.rewritten and rewrite_result.retrieval_query:
            effective_query = rewrite_result.retrieval_query

    try:
        ready_chunks, ready_docs = await _retrieve_patient_ready_chunks(
            patient_id=clean_patient_id,
            query=effective_query,
            limit=body.limit,
            document_type=body.document_type,
            session=session,
            retriever=retriever,
        )
    except Exception as exc:
        logger.exception("Medical chat stream retrieval failed", patient_id=clean_patient_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Medical retrieval execution failed: {exc}",
        ) from exc

    async def chat_event_stream():
        if not ready_chunks:
            yield f"event: token\ndata: {json.dumps({'text': MEDICAL_NO_RESULTS_ANSWER})}\n\n"
            yield f"event: citations\ndata: {json.dumps({'citations': []})}\n\n"
            yield f"event: done\ndata: {json.dumps({'answer': MEDICAL_NO_RESULTS_ANSWER, 'citations': []})}\n\n"
            return

        accumulated_tokens: list[str] = []
        try:
            token_iter, used_chunks = await answer_generator.generate_answer_stream(
                query=effective_query,
                chunks=ready_chunks,
                conversation_context=conversation_context,
                preamble=MEDICAL_ANSWER_SYSTEM_PREAMBLE,
                context_formatter=format_medical_context_chunks,
                no_results_answer=MEDICAL_NO_RESULTS_ANSWER,
            )
            async for token in token_iter:
                accumulated_tokens.append(token)
                yield f"event: token\ndata: {json.dumps({'text': token})}\n\n"

            raw_answer = "".join(accumulated_tokens).strip()
            if not raw_answer:
                try:
                    fb_answer, fb_used = await answer_generator.generate_answer(
                        query=effective_query,
                        chunks=ready_chunks,
                        conversation_context=conversation_context,
                        preamble=MEDICAL_ANSWER_SYSTEM_PREAMBLE,
                        context_formatter=format_medical_context_chunks,
                        no_results_answer=MEDICAL_NO_RESULTS_ANSWER,
                    )
                    raw_answer = fb_answer.strip() if fb_answer else ""
                    if raw_answer:
                        yield f"event: token\ndata: {json.dumps({'text': raw_answer})}\n\n"
                except Exception:
                    pass

            if not raw_answer:
                raw_answer = MEDICAL_NO_RESULTS_ANSWER
                yield f"event: token\ndata: {json.dumps({'text': raw_answer})}\n\n"

            clean_answer = sanitize_citations(raw_answer, max_source_id=len(used_chunks))
            citations = _build_citations(clean_answer, used_chunks, ready_docs)
            citations_payload = [c.model_dump(by_alias=True) for c in citations]

            yield f"event: citations\ndata: {json.dumps({'citations': citations_payload})}\n\n"
            yield f"event: done\ndata: {json.dumps({'answer': clean_answer, 'citations': citations_payload})}\n\n"

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(
                "Medical chat stream completed successfully",
                patient_id=clean_patient_id,
                history_count=len(limited_history),
                result_count=len(ready_chunks),
                citation_count=len(citations),
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.exception("Medical chat streaming failed", patient_id=clean_patient_id, error=str(exc))
            yield f"event: error\ndata: {json.dumps({'message': 'Unable to generate answer'})}\n\n"

    return StreamingResponse(
        chat_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
