from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.user import User
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.answer import AnswerRequest, AnswerResponse
from src.security.jwt import get_current_user
from src.security.rate_limiter import rate_limiter
from src.services.answer_generator import AnswerGenerator, get_answer_generator
from src.services.answer_service import AnswerService
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import SearchService, get_retriever

router = APIRouter(
    prefix="/answer",
    tags=["Answer"],
    dependencies=[Depends(rate_limiter.check)],
)


@router.post(
    "",
    response_model=AnswerResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate grounded RAG answer",
    description=(
        "Execute grounded Question-Answering over user documents: rewrites queries "
        "if conversational context is provided, performs hybrid retrieval with cross-encoder "
        "reranking, and uses Cohere LLM to synthesize a strictly grounded answer with "
        "verifiable source attributions. Enforces strict tenant isolation."
    ),
)
async def generate_answer(
    request: AnswerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> AnswerResponse:
    """Handle authenticated grounded answer generation requests."""
    search_service = SearchService(
        db=db,
        retriever=retriever,
        query_rewriter=query_rewriter,
    )
    service = AnswerService(
        search_service=search_service,
        answer_generator=answer_generator,
    )
    return await service.generate_answer(request=request, user=current_user)


@router.post(
    "/stream",
    status_code=status.HTTP_200_OK,
    summary="Stream grounded RAG answer via Server-Sent Events (SSE)",
)
async def stream_answer(
    request: AnswerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> StreamingResponse:
    """Stream grounded answer chunks as Server-Sent Events (SSE)."""
    search_service = SearchService(
        db=db,
        retriever=retriever,
        query_rewriter=query_rewriter,
    )
    service = AnswerService(
        search_service=search_service,
        answer_generator=answer_generator,
    )
    return StreamingResponse(
        service.stream_answer(request=request, user=current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

