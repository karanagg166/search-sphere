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
from src.models.medical_observation import MedicalObservation
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.routers.internal_medical_documents import sanitize_identifier
from src.security.service_auth import verify_service_secret
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
from src.services.medical_observation_service import (
    MedicalObservationService,
    get_medical_observation_service,
)
from src.services.medical_query_router import (
    MedicalQueryRoute,
    MedicalQueryRouter,
    RoutedMedicalQuery,
    get_medical_query_router,
)
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/medical-rag", tags=["Internal Medical RAG"])

MEDICAL_HYBRID_SYSTEM_PREAMBLE = """You are a dependable, strictly grounded Medical Record Retrieval Assistant for Quick Clinic healthcare providers.
Your goal is to answer the clinician's query accurately using BOTH the exact numeric values from the Structured Clinical Observations and narrative facts from the Document Records provided below.

Strict Clinical & Grounding Rules:
1. STRICT NUMERIC GROUNDING: All exact measurements, readings, and timestamps must come directly and unedited from the Structured Observations. Do NOT round, modify, or approximate numbers.
2. NARRATIVE GROUNDING: Clinician notes, physician remarks, medication references, and discharge details must come directly from the Document Records.
3. MEDICAL SAFETY & SCOPE: You are a record-retrieval assistant, NOT a diagnosing physician. Do NOT independently diagnose illness, predict worsening/hypertension, or generate threshold alert warnings.
4. SOURCE CITATIONS: Attribute every fact or reading directly using bracketed numbers like [1], [2], corresponding to [SOURCE 1], [SOURCE 2] in the provided context.
5. CONCISE AND CLINICAL: Provide a direct, professional, objective, and concise answer without conversational filler."""


class HybridSourceItem:
    """Wrapper item representing either a structured observation or a document chunk for hybrid generation."""

    def __init__(
        self,
        source_type: str,
        observation: MedicalObservation | None = None,
        chunk: Any = None,
        doc_meta: ExternalDocument | None = None,
    ):
        self.source_type = source_type
        self.observation = observation
        self.chunk = chunk
        self.doc_meta = doc_meta


def format_hybrid_context_chunks(items: list[Any]) -> str:
    """Formats hybrid context providing distinct sections for structured observations and narrative records."""
    parts = []
    for idx, item in enumerate(items, start=1):
        if getattr(item, "source_type", None) == "OBSERVATION":
            obs = item.observation
            date_str = obs.observed_at.strftime("%b %d, %Y") if obs.observed_at else "unspecified date"
            val_str = f"{obs.value_text or obs.value_numeric} {obs.unit or ''}".strip()
            parts.append(
                f"[SOURCE {idx}] (Structured Clinical Observation):\n"
                f"Type: {obs.display_name}\n"
                f"Reading / Value: {val_str}\n"
                f"Observed Date: {date_str}\n"
                f"Document ID: {obs.external_document_id}\n"
            )
        else:
            chunk = getattr(item, "chunk", item)
            doc_id = getattr(chunk, "document_id", "unknown")
            page_num = getattr(chunk, "start_page", 1) or 1
            content = getattr(chunk, "content", "")
            parts.append(
                f"[SOURCE {idx}] (Clinical Document Record - Document: {doc_id}, Page: {page_num}):\n"
                f"{content}\n"
            )
    return "\n\n".join(parts)


def _build_hybrid_citations(
    clean_answer: str,
    used_items: list[Any],
    ready_docs: dict[str, ExternalDocument],
) -> list[MedicalAnswerCitation]:
    """Map hybrid sources (observations and chunks) to clean citations."""
    cited_ids = extract_citation_numbers(clean_answer, max_source_id=len(used_items))
    citations: list[MedicalAnswerCitation] = []

    for cid in cited_ids:
        item = used_items[cid - 1]
        if getattr(item, "source_type", None) == "OBSERVATION":
            obs = item.observation
            doc_meta = ready_docs.get(obs.external_document_id)
            val_str = f"{obs.display_name}: {obs.value_text or obs.value_numeric} {obs.unit or ''}".strip()
            citations.append(
                MedicalAnswerCitation(
                    citationId=cid,
                    documentId=obs.external_document_id,
                    fileName=doc_meta.file_name if doc_meta else "Medical Record",
                    documentType="OBSERVATION",
                    reportDate=obs.reported_at.isoformat() if obs.reported_at else None,
                    pageNumber=obs.page_number,
                    chunkIndex=obs.chunk_index,
                    content=val_str,
                    score=obs.confidence,
                    sourceType="OBSERVATION",
                    observationId=obs.id,
                )
            )
        else:
            chunk = getattr(item, "chunk", item)
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
                    sourceType="DOCUMENT_CHUNK",
                    observationId=None,
                )
            )

    return citations


async def _handle_structured_chat(
    session: AsyncSession,
    patient_id: str,
    routed_query: RoutedMedicalQuery,
    obs_service: MedicalObservationService,
) -> tuple[str, list[MedicalAnswerCitation]]:
    """Handles structured clinical observation queries deterministically without LLM hallucination risk."""
    types = routed_query.target_observation_types or None
    time_filter = routed_query.time_filter

    if time_filter.is_latest:
        observations = await obs_service.get_latest_observations(
            session=session,
            patient_id=patient_id,
            observation_types=types,
            limit=1,
        )
    else:
        observations = await obs_service.query_observations(
            session=session,
            patient_id=patient_id,
            observation_types=types,
            from_date=time_filter.from_date,
            to_date=time_filter.to_date,
            limit=time_filter.limit,
            sort_order="asc",
        )

    # Missing structured observations handling (PART 54)
    if not observations:
        concept_label = "medical"
        if types:
            first_t = types[0].upper()
            if first_t == "HBA1C":
                concept_label = "an HbA1c"
            elif first_t == "BLOOD_PRESSURE":
                concept_label = "any blood pressure"
            elif first_t == "HEART_RATE":
                concept_label = "any heart rate"
            elif first_t in ("FASTING_GLUCOSE", "RANDOM_GLUCOSE", "BLOOD_GLUCOSE"):
                concept_label = "any glucose"
            elif first_t == "OXYGEN_SATURATION":
                concept_label = "any oxygen saturation"
            elif first_t == "BODY_TEMPERATURE":
                concept_label = "any temperature"
            else:
                concept_label = f"any {types[0].replace('_', ' ').lower()}"

        if "an " in concept_label:
            return f"I couldn't find {concept_label} result in the available structured medical records.", []
        else:
            return f"I couldn't find {concept_label} readings in the available structured medical records.", []

    doc_ids = list({obs.external_document_id for obs in observations})
    db_res = await session.execute(
        select(ExternalDocument).where(
            ExternalDocument.source_system == "quick_clinic",
            ExternalDocument.external_patient_id == patient_id,
            ExternalDocument.external_document_id.in_(doc_ids),
        )
    )
    docs_map = {d.external_document_id: d for d in db_res.scalars().all()}

    first_display = observations[0].display_name
    if time_filter.is_latest:
        obs = observations[0]
        date_str = obs.observed_at.strftime("%b %d, %Y") if obs.observed_at else "recorded date"
        val_str = f"{obs.value_text or obs.value_numeric} {obs.unit or ''}".strip()
        answer = f"The latest recorded {first_display} reading is {val_str} (observed on {date_str}) [1]."
    elif time_filter.is_trend:
        lines = [f"The recorded {first_display} readings in chronological order are:"]
        for idx, obs in enumerate(observations, start=1):
            date_str = obs.observed_at.strftime("%b %d, %Y") if obs.observed_at else "recorded date"
            val_str = f"{obs.value_text or obs.value_numeric} {obs.unit or ''}".strip()
            lines.append(f"- {date_str}: {val_str} [{idx}]")
        answer = "\n".join(lines)
    else:
        time_desc = f" in the {time_filter.description}" if time_filter.description and time_filter.description != "all available" else ""
        lines = [f"The available {first_display} readings{time_desc} are:"]
        for idx, obs in enumerate(observations, start=1):
            date_str = obs.observed_at.strftime("%b %d, %Y") if obs.observed_at else "recorded date"
            val_str = f"{obs.value_text or obs.value_numeric} {obs.unit or ''}".strip()
            lines.append(f"- {date_str}: {val_str} [{idx}]")
        answer = "\n".join(lines)

    citations: list[MedicalAnswerCitation] = []
    for idx, obs in enumerate(observations, start=1):
        doc_meta = docs_map.get(obs.external_document_id)
        val_str = f"{obs.display_name}: {obs.value_text or obs.value_numeric} {obs.unit or ''}".strip()
        citations.append(
            MedicalAnswerCitation(
                citationId=idx,
                documentId=obs.external_document_id,
                fileName=doc_meta.file_name if doc_meta else "Medical Record",
                documentType="OBSERVATION",
                reportDate=obs.reported_at.isoformat() if obs.reported_at else None,
                pageNumber=obs.page_number,
                chunkIndex=obs.chunk_index,
                content=val_str,
                score=obs.confidence,
                sourceType="OBSERVATION",
                observationId=obs.id,
            )
        )

    return answer, citations


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
                sourceType="DOCUMENT_CHUNK",
                observationId=None,
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

    ready_chunks, ready_docs = await _retrieve_patient_ready_chunks(
        patient_id=clean_patient_id,
        query=clean_query,
        limit=body.limit,
        document_type=body.document_type,
        session=session,
        retriever=retriever,
    )

    if not ready_chunks:
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
    summary="Multi-turn patient-isolated medical chat answer generation with structured/RAG/hybrid routing",
)
async def generate_medical_chat(
    body: MedicalChatRequest,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
    query_router: MedicalQueryRouter = Depends(get_medical_query_router),
    obs_service: MedicalObservationService = Depends(get_medical_observation_service),
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

    # 2. Multi-turn query reformulation
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

    # 3. Clinical Query Routing (STRUCTURED / RAG / HYBRID)
    routed = query_router.route_query(
        query=effective_query,
        conversation_context=conversation_context,
    )

    # CASE A: STRUCTURED OBSERVATION QUERY
    if routed.route == MedicalQueryRoute.STRUCTURED:
        ans, citations = await _handle_structured_chat(
            session=session,
            patient_id=clean_patient_id,
            routed_query=routed,
            obs_service=obs_service,
        )
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "Medical chat structured answer generated",
            patient_id=clean_patient_id,
            route_mode="STRUCTURED",
            citation_count=len(citations),
            duration_ms=duration_ms,
        )
        return MedicalChatResponse(
            answer=ans,
            citations=citations,
            result_count=len(citations),
            retrieval_query=effective_query,
            rewritten=rewritten,
            answer_mode="STRUCTURED",
        )

    # CASE B: HYBRID QUERY (Structured Observations + RAG Chunks)
    if routed.route == MedicalQueryRoute.HYBRID:
        # Fetch structured observations
        observations = await obs_service.query_observations(
            session=session,
            patient_id=clean_patient_id,
            observation_types=routed.target_observation_types or None,
            from_date=routed.time_filter.from_date,
            to_date=routed.time_filter.to_date,
            limit=10,
            sort_order="asc",
        )
        # Fetch semantic chunks
        ready_chunks, ready_docs = await _retrieve_patient_ready_chunks(
            patient_id=clean_patient_id,
            query=effective_query,
            limit=body.limit,
            document_type=body.document_type,
            session=session,
            retriever=retriever,
        )

        if not observations and not ready_chunks:
            return MedicalChatResponse(
                answer=MEDICAL_NO_RESULTS_ANSWER,
                citations=[],
                result_count=0,
                retrieval_query=effective_query,
                rewritten=rewritten,
                answer_mode="HYBRID",
            )

        # Build combined hybrid sources
        hybrid_items: list[Any] = []
        for obs in observations:
            doc_meta = ready_docs.get(obs.external_document_id)
            hybrid_items.append(
                HybridSourceItem(
                    source_type="OBSERVATION",
                    observation=obs,
                    doc_meta=doc_meta,
                )
            )
        for chunk in ready_chunks:
            hybrid_items.append(
                HybridSourceItem(
                    source_type="DOCUMENT_CHUNK",
                    chunk=chunk,
                    doc_meta=ready_docs.get(chunk.document_id),
                )
            )

        try:
            raw_answer_text, used_items = await answer_generator.generate_answer(
                query=effective_query,
                chunks=hybrid_items,
                conversation_context=conversation_context,
                preamble=MEDICAL_HYBRID_SYSTEM_PREAMBLE,
                context_formatter=format_hybrid_context_chunks,
                no_results_answer=MEDICAL_NO_RESULTS_ANSWER,
            )
        except Exception as exc:
            logger.exception("Medical chat hybrid answer generation failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Hybrid answer generation failed: {exc}",
            ) from exc

        clean_answer = sanitize_citations(raw_answer_text, max_source_id=len(used_items))
        citations = _build_hybrid_citations(clean_answer, used_items, ready_docs)
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.info(
            "Medical chat hybrid answer generated",
            patient_id=clean_patient_id,
            route_mode="HYBRID",
            observation_count=len(observations),
            chunk_count=len(ready_chunks),
            citation_count=len(citations),
            duration_ms=duration_ms,
        )
        return MedicalChatResponse(
            answer=clean_answer,
            citations=citations,
            result_count=len(hybrid_items),
            retrieval_query=effective_query,
            rewritten=rewritten,
            answer_mode="HYBRID",
        )

    # CASE C: RAG QUERY (Default Semantic Search)
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
        return MedicalChatResponse(
            answer=MEDICAL_NO_RESULTS_ANSWER,
            citations=[],
            result_count=0,
            retrieval_query=effective_query,
            rewritten=rewritten,
            answer_mode="RAG",
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
        "Medical chat RAG answer generated",
        patient_id=clean_patient_id,
        route_mode="RAG",
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
        answer_mode="RAG",
    )


@router.post(
    "/chat/stream",
    status_code=status.HTTP_200_OK,
    summary="Stream multi-turn medical chat answer via SSE with structured/RAG/hybrid routing",
)
async def stream_medical_chat(
    body: MedicalChatRequest,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
    query_router: MedicalQueryRouter = Depends(get_medical_query_router),
    obs_service: MedicalObservationService = Depends(get_medical_observation_service),
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

    routed = query_router.route_query(
        query=effective_query,
        conversation_context=conversation_context,
    )

    # 1. CASE: STRUCTURED STREAM
    if routed.route == MedicalQueryRoute.STRUCTURED:
        ans, citations = await _handle_structured_chat(
            session=session,
            patient_id=clean_patient_id,
            routed_query=routed,
            obs_service=obs_service,
        )

        async def structured_event_stream():
            yield f"event: token\ndata: {json.dumps({'text': ans})}\n\n"
            citations_payload = [c.model_dump(by_alias=True) for c in citations]
            yield f"event: citations\ndata: {json.dumps({'citations': citations_payload})}\n\n"
            yield f"event: done\ndata: {json.dumps({'answer': ans, 'citations': citations_payload, 'mode': 'STRUCTURED'})}\n\n"

        return StreamingResponse(
            structured_event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 2. CASE: HYBRID STREAM
    if routed.route == MedicalQueryRoute.HYBRID:
        observations = await obs_service.query_observations(
            session=session,
            patient_id=clean_patient_id,
            observation_types=routed.target_observation_types or None,
            from_date=routed.time_filter.from_date,
            to_date=routed.time_filter.to_date,
            limit=10,
            sort_order="asc",
        )
        ready_chunks, ready_docs = await _retrieve_patient_ready_chunks(
            patient_id=clean_patient_id,
            query=effective_query,
            limit=body.limit,
            document_type=body.document_type,
            session=session,
            retriever=retriever,
        )

        async def hybrid_event_stream():
            if not observations and not ready_chunks:
                yield f"event: token\ndata: {json.dumps({'text': MEDICAL_NO_RESULTS_ANSWER})}\n\n"
                yield f"event: citations\ndata: {json.dumps({'citations': []})}\n\n"
                yield f"event: done\ndata: {json.dumps({'answer': MEDICAL_NO_RESULTS_ANSWER, 'citations': [], 'mode': 'HYBRID'})}\n\n"
                return

            hybrid_items: list[Any] = []
            for obs in observations:
                doc_meta = ready_docs.get(obs.external_document_id)
                hybrid_items.append(
                    HybridSourceItem(
                        source_type="OBSERVATION",
                        observation=obs,
                        doc_meta=doc_meta,
                    )
                )
            for chunk in ready_chunks:
                hybrid_items.append(
                    HybridSourceItem(
                        source_type="DOCUMENT_CHUNK",
                        chunk=chunk,
                        doc_meta=ready_docs.get(chunk.document_id),
                    )
                )

            accumulated_tokens: list[str] = []
            try:
                token_iter, used_items = await answer_generator.generate_answer_stream(
                    query=effective_query,
                    chunks=hybrid_items,
                    conversation_context=conversation_context,
                    preamble=MEDICAL_HYBRID_SYSTEM_PREAMBLE,
                    context_formatter=format_hybrid_context_chunks,
                    no_results_answer=MEDICAL_NO_RESULTS_ANSWER,
                )
                async for token in token_iter:
                    accumulated_tokens.append(token)
                    yield f"event: token\ndata: {json.dumps({'text': token})}\n\n"

                raw_answer = "".join(accumulated_tokens).strip()
                if not raw_answer:
                    raw_answer = MEDICAL_NO_RESULTS_ANSWER
                    yield f"event: token\ndata: {json.dumps({'text': raw_answer})}\n\n"

                clean_answer = sanitize_citations(raw_answer, max_source_id=len(used_items))
                citations = _build_hybrid_citations(clean_answer, used_items, ready_docs)
                citations_payload = [c.model_dump(by_alias=True) for c in citations]

                yield f"event: citations\ndata: {json.dumps({'citations': citations_payload})}\n\n"
                yield f"event: done\ndata: {json.dumps({'answer': clean_answer, 'citations': citations_payload, 'mode': 'HYBRID'})}\n\n"
            except Exception as exc:
                logger.exception("Medical chat hybrid streaming failed", error=str(exc))
                yield f"event: error\ndata: {json.dumps({'message': 'Unable to generate answer'})}\n\n"

        return StreamingResponse(
            hybrid_event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 3. CASE: RAG STREAM
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
            yield f"event: done\ndata: {json.dumps({'answer': MEDICAL_NO_RESULTS_ANSWER, 'citations': [], 'mode': 'RAG'})}\n\n"
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
            yield f"event: done\ndata: {json.dumps({'answer': clean_answer, 'citations': citations_payload, 'mode': 'RAG'})}\n\n"

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(
                "Medical chat stream completed successfully",
                patient_id=clean_patient_id,
                route_mode="RAG",
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
