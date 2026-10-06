import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.v1.search import SearchRequest, SearchResponse
from src.security.service_auth import get_service_context
from src.security.service_context import ServiceContext
from src.services.answer_generator import AnswerGenerator, get_answer_generator
from src.services.generic_rag_service import GenericRagService
from src.services.search_service import get_retriever

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/search", tags=["V1 Semantic Retrieval"])


@router.post(
    "",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Multi-tenant two-stage reranked hybrid search",
    description="Executes dense + BM25 sparse retrieval with Qdrant server-side tenant isolation, followed by cross-encoder reranking.",
)
async def execute_search(
    body: SearchRequest,
    context: ServiceContext = Depends(get_service_context(required_scopes={"search:execute"})),
    db: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> SearchResponse:
    service = GenericRagService(db, retriever, answer_generator)
    return await service.search(context, body)
