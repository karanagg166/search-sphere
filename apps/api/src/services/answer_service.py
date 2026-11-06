import json
from collections.abc import AsyncIterator

import structlog
from fastapi import HTTPException, status

from src.inference.policy import ProviderError
from src.models.user import User
from src.schemas.answer import AnswerRequest, AnswerResponse, AnswerSource
from src.schemas.search import SearchRequest
from src.security.query_access import current_query_access
from src.security.response_stream import authorized_frames
from src.services.answer_generator import (
    NO_RESULTS_ANSWER,
    AnswerGenerationConfigError,
    AnswerGenerationError,
    AnswerGenerationTimeoutError,
    AnswerGenerationUnavailableError,
    AnswerGenerator,
    get_answer_generator,
)
from src.services.search_service import SearchService

logger = structlog.get_logger()


class AnswerService:
    """
    Coordinates grounded RAG answer generation workflows.

    Responsibilities:
    - Reuse SearchService for authorization, query rewriting, hybrid retrieval,
      cross-encoder reranking, and defense-in-depth tenant isolation;
    - Handle empty retrieval results safely without calling the LLM;
    - Delegate grounded answer generation to AnswerGenerator;
    - Map used context chunks to attributed AnswerSource response items;
    - Translate provider failures to appropriate HTTP status codes;
    - Provide structured logging and defense against citation hallucination.
    """

    def __init__(
        self,
        search_service: SearchService,
        answer_generator: AnswerGenerator | None = None,
    ) -> None:
        self.search_service = search_service
        self.answer_generator = answer_generator or get_answer_generator()

    async def generate_answer(
        self,
        request: AnswerRequest,
        user: User,
        *, buffer_stream: bool = False,
    ) -> AnswerResponse:
        """
        Execute grounded RAG workflow for the authenticated user.

        Args:
            request: Validated AnswerRequest.
            user: Authenticated user issuing the request.

        Returns:
            AnswerResponse containing grounded answer and attributed source chunks.

        Raises:
            HTTPException: 404 if document_id is not found or not owned by user.
            HTTPException: 400 if search parameters fail query validation.
            HTTPException: 504 if LLM generation request times out.
            HTTPException: 503 if LLM provider or configuration is unavailable.
            HTTPException: 500 if internal retrieval or generation error occurs.
        """
        # 1. Execute authenticated retrieval via existing SearchService
        search_req = SearchRequest(
            query=request.query,
            conversation_id=request.conversation_id,
            conversation_context=None,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            document_id=request.document_id,
            score_threshold=request.score_threshold,
        )
        search_response = await self.search_service.search(
            request=search_req,
            user=user,
        )

        # 2. Guard: if retrieval returned 0 results, do NOT call LLM
        if not search_response.results:
            logger.info(
                "Search returned 0 results; returning safe no-results answer",
                user_id=user.id,
                query_length=len(request.query),
            )
            return AnswerResponse(
                query=search_response.query,
                retrieval_query=search_response.retrieval_query,
                rewritten=search_response.rewritten,
                answer=search_response.response or search_response.clarification or NO_RESULTS_ANSWER,
                sources=[],
                requires_history=search_response.requires_history,
                route=search_response.route,
                context_message_ids=search_response.context_message_ids,
            )

        # 3. Generate grounded answer from retrieved top chunks
        try:
            if buffer_stream:
                tokens, used_chunks = await self.answer_generator.generate_answer_stream(
                    query=search_response.query,chunks=search_response.results,
                    conversation_context=current_query_access().history if current_query_access() else None)
                pieces = []
                size = 0
                async for token in tokens:
                    if not isinstance(token,str):
                        raise AnswerGenerationError("Invalid streaming token.")
                    size += len(token)
                    if size > 16000:
                        raise AnswerGenerationError("Answer exceeds the bounded output limit.")
                    pieces.append(token)
                answer_text = "".join(pieces)
                if not answer_text.strip():
                    raise AnswerGenerationError("Provider returned an empty answer.")
            else:
                answer_text, used_chunks = await self.answer_generator.generate_answer(
                    query=search_response.query,chunks=search_response.results,
                    conversation_context=current_query_access().history if current_query_access() else None)
            access = current_query_access()
            if access is not None:
                await access.validate()
        except AnswerGenerationTimeoutError as exc:
            logger.error(
                "Answer generation timed out",
                user_id=user.id,
                query_length=len(request.query),
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Answer generation request timed out. Please try again.",
            ) from exc
        except (
            AnswerGenerationUnavailableError,
            AnswerGenerationConfigError,
        ) as exc:
            logger.error(
                "Answer generation provider unavailable or unconfigured",
                user_id=user.id,
                query_length=len(request.query),
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Answer generation service is currently unavailable.",
            ) from exc
        except AnswerGenerationError as exc:
            logger.error(
                "Answer generation failed",
                user_id=user.id,
                query_length=len(request.query),
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate answer from document context.",
            ) from exc
        except ProviderError:
            raise
        except Exception as exc:
            logger.error(
                "Unexpected error during answer generation",
                user_id=user.id,
                query_length=len(request.query),
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred during answer generation.",
            ) from exc

        # 4. Map attributed sources
        sources = [
            AnswerSource(
                source_id=idx + 1,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                start_page=chunk.start_page,
                end_page=chunk.end_page,
                content=chunk.content,
                rerank_score=chunk.rerank_score,
            )
            for idx, chunk in enumerate(used_chunks)
        ]

        logger.info(
            "Grounded answer generated successfully",
            user_id=user.id,
            query_length=len(request.query),
            sources_count=len(sources),
            answer_length=len(answer_text),
        )

        return AnswerResponse(
            query=search_response.query,
            retrieval_query=search_response.retrieval_query,
            rewritten=search_response.rewritten,
            answer=answer_text,
            sources=sources,
            requires_history=search_response.requires_history,
            route=search_response.route,
            context_message_ids=search_response.context_message_ids,
        )

    async def stream_answer(
        self,
        request: AnswerRequest,
        user: User,
    ) -> AsyncIterator[str]:
        """
        Stream SSE formatted events: metadata, source, token, done, error.
        """
        try:
            result = await self.generate_answer(request,user,buffer_stream=True)
            access = current_query_access()
            if access is not None:
                await access.validate()
        except (ProviderError,HTTPException) as exc:
            code = exc.code if isinstance(exc,ProviderError) else "answer_unavailable"
            yield f"event: error\ndata: {json.dumps({'code':code,'error':'Unable to generate answer.'})}\n\n"
            return
        payload=result.model_dump()
        metadata={key:payload[key] for key in ['query','retrieval_query','rewritten','requires_history','route','context_message_ids']}
        frames=[("metadata",metadata),("source",{"sources":payload["sources"]}),("token",{"token":result.answer}),
                ("done",{"answer":result.answer,"sources":payload["sources"]})]
        async for frame in authorized_frames(frames):
            yield frame
