import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.v1.answers import AnswerRequest, AnswerResponse
from src.security.query_access import delegated_query_inference
from src.security.service_auth import get_service_context
from src.security.service_context import ServiceContext
from src.services.answer_generator import AnswerGenerator, get_answer_generator
from src.services.generic_rag_service import GenericRagService
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/answers", tags=["V1 Grounded Answers"], dependencies=[Depends(delegated_query_inference)])


@router.post(
    "",
    response_model=AnswerResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate grounded RAG answer with verified citations",
    description="Retrieves relevant documents within authorized tenant, constructs verified context, and synthesizes answer with bracketed citations.",
)
async def generate_answer(
    body: AnswerRequest,
    context: ServiceContext = Depends(get_service_context(required_scopes={"answers:generate"})),
    db: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
) -> AnswerResponse:
    service = GenericRagService(db, retriever, answer_generator, query_rewriter)
    return await service.answer(context, body)


@router.post(
    "/stream",
    status_code=status.HTTP_200_OK,
    summary="Stream grounded RAG answer via Server-Sent Events (SSE)",
)
async def stream_answer(
    body: AnswerRequest,
    context: ServiceContext = Depends(get_service_context(required_scopes={"answers:generate"})),
    db: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
) -> StreamingResponse:
    service = GenericRagService(db, retriever, answer_generator, query_rewriter)
    return StreamingResponse(
        service.answer_stream(context, body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
