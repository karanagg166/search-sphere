import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
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
    MedicalRagAnswerRequest,
    MedicalRagAnswerResponse,
)
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
from src.services.search_service import get_retriever

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/medical-rag", tags=["Internal Medical RAG"])


@router.post(
    "/answer",
    response_model=MedicalRagAnswerResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Secure patient-isolated grounded medical RAG answer generation",
    description=(
        "Executes patient-isolated hybrid retrieval and cross-encoder reranking, "
        "filters for READY documents, constructs clinically safe grounded context, "
        "and synthesizes an objective answer with programmatically verified citations."
    ),
)
async def generate_medical_answer(
    body: MedicalRagAnswerRequest,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> MedicalRagAnswerResponse:
    start_time = time.perf_counter()

    # 1. Input sanitization & validation
    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")
    clean_query = body.query.strip()
    if not clean_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query cannot be empty or whitespace only.",
        )

    resolved_limit = max(1, min(body.limit, 20))
    candidate_k = max(resolved_limit * 2, 20)

    # 2. Construct mandatory Qdrant server-side filter for strict patient isolation
    qdrant_filters: dict[str, Any] = {
        "source_system": "quick_clinic",
        "patient_id": clean_patient_id,
    }
    if body.document_type:
        clean_doc_type = body.document_type.strip().upper()
        if clean_doc_type:
            qdrant_filters["document_type"] = clean_doc_type

    # 3. Execute two-stage retrieval (Dense + Sparse RRF followed by Cross-Encoder reranking)
    try:
        reranked_chunks = await retriever.search(
            query=clean_query,
            top_k=resolved_limit,
            candidate_k=candidate_k,
            filters=qdrant_filters,
        )
    except Exception as exc:
        logger.exception(
            "Search Sphere medical retrieval failed during answer generation",
            patient_id=clean_patient_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Medical retrieval execution failed: {exc}",
        ) from exc

    if not reranked_chunks:
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

    # 4. Filter only READY, non-deleted documents for this patient in PostgreSQL
    chunk_doc_ids = list({chunk.document_id for chunk in reranked_chunks})
    db_result = await session.execute(
        select(ExternalDocument).where(
            ExternalDocument.source_system == "quick_clinic",
            ExternalDocument.external_patient_id == clean_patient_id,
            ExternalDocument.external_document_id.in_(chunk_doc_ids),
            ExternalDocument.status == "READY",
        )
    )
    ready_docs = {doc.external_document_id: doc for doc in db_result.scalars().all()}

    ready_chunks = []
    for chunk in reranked_chunks:
        # Exclude chunks from documents not found or not in READY state
        if chunk.document_id not in ready_docs:
            continue

        # Defense-in-depth: check patient ownership on chunk
        if chunk.patient_id and chunk.patient_id != clean_patient_id:
            logger.error(
                "Security anomaly: chunk patient_id mismatch detected",
                expected_patient=clean_patient_id,
                chunk_patient=chunk.patient_id,
                document_id=chunk.document_id,
            )
            continue

        ready_chunks.append(chunk)

    # Guard: If no valid READY chunks remain, return safe fallback directly without calling LLM
    if not ready_chunks:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "No READY chunks available for patient; returning safe fallback",
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

    # 5. Grounded LLM generation via shared AnswerGenerator
    try:
        raw_answer_text, used_chunks = await answer_generator.generate_answer(
            query=clean_query,
            chunks=ready_chunks,
            preamble=MEDICAL_ANSWER_SYSTEM_PREAMBLE,
            context_formatter=format_medical_context_chunks,
            no_results_answer=MEDICAL_NO_RESULTS_ANSWER,
        )
    except AnswerGenerationTimeoutError as exc:
        logger.error(
            "Medical answer generation timed out",
            patient_id=clean_patient_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Medical answer generation timed out. Please try again.",
        ) from exc
    except (AnswerGenerationUnavailableError, AnswerGenerationConfigError) as exc:
        logger.error(
            "Medical answer generation provider unavailable or unconfigured",
            patient_id=clean_patient_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Medical answer generation service is currently unavailable.",
        ) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected error during medical answer generation",
            patient_id=clean_patient_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while generating medical answer.",
        ) from exc

    # 6. Sanitize citations against actual supplied sources count
    clean_answer = sanitize_citations(raw_answer_text, max_source_id=len(used_chunks))

    # 7. Programmatically map citations from retrieved chunks
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

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    # Strictly PHI-safe logging: log patient_id, citationCount, resultCount, duration. NEVER query or medical text.
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
