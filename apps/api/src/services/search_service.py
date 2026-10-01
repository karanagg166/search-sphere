import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User
from src.repositories.document_repository import DocumentRepository
from src.retrieval.hybrid_retriever import (
    HybridQueryValidationError,
    HybridRetrievalError,
)
from src.retrieval.reranked_retriever import (
    RerankedHybridRetriever,
    RerankedQueryValidationError,
    RerankedRetrievalError,
)
from src.schemas.search import (
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
)
from src.services.query_rewriter import QueryRewriter, get_query_rewriter

logger = structlog.get_logger()

# Global cached retriever instance for dependency injection
_retriever_instance: RerankedHybridRetriever | None = None


def get_retriever() -> RerankedHybridRetriever:
    """
    FastAPI dependency returning a shared RerankedHybridRetriever instance.
    Avoids re-instantiating heavy retrieval components across HTTP requests.
    Can be overridden in tests via app.dependency_overrides.
    """
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = RerankedHybridRetriever()
    return _retriever_instance


class SearchService:
    """
    Coordinates authenticated semantic search workflows.

    Responsibilities:
    - Reformulate contextual queries into standalone queries via QueryRewriter;
    - Enforce tenant isolation and document authorization at application layer;
    - Resolve document-scoped vs all-user-documents search filters;
    - Delegate execution to RerankedHybridRetriever (Dense + Sparse RRF + Cross-Encoder);
    - Enforce defense-in-depth output filtering against user document boundaries;
    - Translate internal retrieval exceptions to clean HTTP responses;
    - Format results into API Pydantic response models.
    """

    def __init__(
        self,
        db: AsyncSession,
        retriever: RerankedHybridRetriever | None = None,
        query_rewriter: QueryRewriter | None = None,
    ) -> None:
        self.db = db
        self.document_repo = DocumentRepository(db)
        self.retriever = retriever or get_retriever()
        self.query_rewriter = query_rewriter or get_query_rewriter()

    async def search(self, request: SearchRequest, user: User) -> SearchResponse:
        """
        Execute authenticated semantic search for the requesting user.

        Args:
            request: Validated SearchRequest.
            user: Authenticated user performing the search.

        Returns:
            SearchResponse containing ranked results with cross-encoder scores.

        Raises:
            HTTPException: 404 if document_id is specified but not owned by user.
            HTTPException: 400 if search parameters fail query validation.
            HTTPException: 500 if internal vector store or retrieval error occurs.
        """
        target_document_id: str | None = None
        target_document_ids: list[str] | None = None

        # 1. Authorization & Scoping
        if request.document_id is not None:
            # Verify document exists and belongs to current user in PostgreSQL
            doc = await self.document_repo.get_by_id(
                document_id=request.document_id,
                user_id=user.id,
            )
            if not doc:
                logger.warning(
                    "User attempted to search non-existent or unowned document",
                    user_id=user.id,
                    document_id=request.document_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found.",
                )
            target_document_id = request.document_id
        else:
            # Multi-document search: restrict to documents owned by authenticated user
            user_doc_ids = await self.document_repo.get_document_ids_by_user(user.id)
            if not user_doc_ids:
                logger.info(
                    "User has 0 documents; returning empty search results immediately",
                    user_id=user.id,
                )
                return SearchResponse(
                    query=request.query,
                    retrieval_query=request.query,
                    rewritten=False,
                    total=0,
                    results=[],
                )
            target_document_ids = user_doc_ids

        # 2. Query Reformulation (standalone query generation before retrieval)
        rewrite_result = await self.query_rewriter.rewrite(
            query=request.query,
            conversation_context=request.conversation_context,
        )
        retrieval_query = rewrite_result.retrieval_query

        # 3. Execute two-stage reranked retrieval pipeline
        try:
            raw_results = await self.retriever.search(
                query=retrieval_query,
                top_k=request.top_k,
                candidate_k=request.candidate_k,
                document_id=target_document_id,
                document_ids=target_document_ids,
            )
        except (RerankedQueryValidationError, HybridQueryValidationError) as exc:
            logger.warning(
                "Search query validation failed",
                user_id=user.id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except (RerankedRetrievalError, HybridRetrievalError) as exc:
            logger.error(
                "Reranked retrieval error during search",
                user_id=user.id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Search service encountered an internal retrieval error.",
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during search execution",
                user_id=user.id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred during search.",
            ) from exc

        # 4. Defense-in-depth isolation check: ensure no unauthorized documents leak
        if target_document_id is not None:
            filtered_results = [
                r for r in raw_results if r.document_id == target_document_id
            ]
        else:
            allowed_ids = set(target_document_ids) if target_document_ids else set()
            filtered_results = [r for r in raw_results if r.document_id in allowed_ids]

        # 5. Map domain results to API response schema
        result_responses = [
            SearchResultResponse(
                document_id=r.document_id,
                chunk_index=r.chunk_index,
                content=r.content,
                start_page=r.start_page,
                end_page=r.end_page,
                page_numbers=list(r.page_numbers),
                block_types=list(r.block_types),
                rrf_score=r.rrf_score,
                rerank_score=r.rerank_score,
                rank=idx + 1,
            )
            for idx, r in enumerate(filtered_results)
        ]

        logger.info(
            "Semantic search completed successfully",
            user_id=user.id,
            original_query=request.query,
            retrieval_query=retrieval_query,
            rewritten=rewrite_result.rewritten,
            results_count=len(result_responses),
            document_scoped=target_document_id is not None,
        )

        return SearchResponse(
            query=request.query,
            retrieval_query=retrieval_query,
            rewritten=rewrite_result.rewritten,
            total=len(result_responses),
            results=result_responses,
        )
