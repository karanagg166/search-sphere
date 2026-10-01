from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.user import User
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.search import SearchRequest, SearchResponse
from src.security.jwt import get_current_user
from src.services.search_service import SearchService, get_retriever

router = APIRouter(prefix="/search", tags=["Search"])


@router.post(
    "",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Perform semantic search",
    description=(
        "Execute two-stage reranked hybrid search: dense ANN semantic retrieval and "
        "sparse BM25 lexical retrieval fused via Qdrant server-side RRF, then reranked "
        "with a Cross-Encoder model. Enforces strict tenant isolation."
    ),
)
async def search_documents(
    request: SearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
) -> SearchResponse:
    """Handle authenticated semantic search requests."""
    service = SearchService(db=db, retriever=retriever)
    return await service.search(request=request, user=current_user)
