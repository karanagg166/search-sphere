import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.external_document import ExternalDocument
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.routers.internal_medical_documents import sanitize_identifier
from src.security.service_auth import verify_service_secret
from src.schemas.internal_medical_retrieval import (
    MedicalRetrievalChunkResult,
    MedicalRetrievalSearchRequest,
    MedicalRetrievalSearchResponse,
)
from src.services.search_service import get_retriever

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/medical-retrieval", tags=["Internal Medical Retrieval"])


@router.post(
    "/search",
    response_model=MedicalRetrievalSearchResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Secure patient-isolated medical record semantic retrieval",
    description=(
        "Retrieves the most relevant indexed medical document chunks for an authorized patient. "
        "Strictly applies Qdrant filters for source_system='quick_clinic' and patient_id, "
        "ensuring patient isolation in both dense and sparse retrieval stages before reranking."
    ),
)
async def search_medical_records(
    body: MedicalRetrievalSearchRequest,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
) -> MedicalRetrievalSearchResponse:
    start_time = time.perf_counter()

    # 1. Input sanitization & validation
    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")
    clean_query = body.query.strip()
    if not clean_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query cannot be empty or whitespace only.",
        )

    resolved_limit = max(1, min(body.limit, 50))
    candidate_k = max(resolved_limit * 2, 20)

    # 2. Construct mandatory Qdrant server-side filter
    # Mandatory patient isolation: filtering MUST occur inside the Qdrant query itself
    qdrant_filters: dict[str, Any] = {
        "source_system": "quick_clinic",
        "patient_id": clean_patient_id,
    }
    if body.document_type:
        clean_doc_type = body.document_type.strip().upper()
        if clean_doc_type:
            qdrant_filters["document_type"] = clean_doc_type

    # 3. Execute two-stage retrieval (Dense ANN + BM25 Sparse RRF, followed by Cross-Encoder reranking)
    try:
        reranked_chunks = await retriever.search(
            query=clean_query,
            top_k=resolved_limit,
            candidate_k=candidate_k,
            filters=qdrant_filters,
        )
    except Exception as exc:
        logger.exception(
            "Search Sphere medical retrieval failed",
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
            "Medical retrieval completed with zero chunks found",
            patient_id=clean_patient_id,
            result_count=0,
            duration_ms=duration_ms,
        )
        return MedicalRetrievalSearchResponse(results=[])

    # 4. PART 6 & 17: Only return chunks belonging to READY, non-deleted documents for this patient
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

    # 5. Build output results with verified metadata
    results: list[MedicalRetrievalChunkResult] = []
    for chunk in reranked_chunks:
        # Defense-in-depth: exclude chunks from documents that are not READY or deleted
        if chunk.document_id not in ready_docs:
            continue

        doc_meta = ready_docs[chunk.document_id]

        # Defense-in-depth: double check patient ownership
        if chunk.patient_id and chunk.patient_id != clean_patient_id:
            logger.error(
                "Security anomaly: chunk patient_id mismatch detected",
                expected_patient=clean_patient_id,
                chunk_patient=chunk.patient_id,
                document_id=chunk.document_id,
            )
            continue

        # Resolve report_date ISO string
        report_date_str = chunk.report_date
        if not report_date_str and doc_meta.report_date:
            report_date_str = doc_meta.report_date.isoformat()

        # Resolve page number
        page_num = chunk.start_page
        if page_num is None and chunk.page_numbers:
            page_num = chunk.page_numbers[0]

        file_name = chunk.file_name or doc_meta.file_name
        doc_type = chunk.document_type or doc_meta.document_type

        score_val = float(chunk.score if chunk.score is not None else chunk.rerank_score)

        results.append(
            MedicalRetrievalChunkResult(
                score=round(score_val, 4),
                content=chunk.content,
                documentId=chunk.document_id,
                documentType=doc_type,
                reportDate=report_date_str,
                fileName=file_name,
                pageNumber=page_num,
                chunkIndex=chunk.chunk_index,
                patientId=clean_patient_id,
            )
        )

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    # Strictly PHI-safe logging: log patient_id and result count, never query or chunk content
    logger.info(
        "Medical retrieval completed successfully",
        patient_id=clean_patient_id,
        raw_chunks=len(reranked_chunks),
        filtered_results=len(results),
        duration_ms=duration_ms,
    )

    return MedicalRetrievalSearchResponse(results=results)
