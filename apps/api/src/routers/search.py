from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.user import User
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.search import SearchRequest, SearchResponse
from src.security.jwt import get_current_user
from src.security.rate_limiter import rate_limiter
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import SearchService, get_retriever

router = APIRouter(
    prefix="/search",
    tags=["Search"],
    dependencies=[Depends(rate_limiter.check)],
)


@router.post(
    "",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Perform semantic search",
    description=(
        "Execute two-stage reranked hybrid search: dense ANN semantic retrieval and "
        "sparse BM25 lexical retrieval fused via Qdrant server-side RRF, then reranked "
        "with a Cross-Encoder model. Automatically resolves conversational queries to "
        "standalone retrieval queries using Cohere. Enforces strict tenant isolation."
    ),
)
async def search_documents(
    request: SearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
) -> SearchResponse:
    """Handle authenticated semantic search requests."""
    service = SearchService(
        db=db,
        retriever=retriever,
        query_rewriter=query_rewriter,
    )
    return await service.search(request=request, user=current_user)
