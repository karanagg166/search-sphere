import json
from collections.abc import AsyncIterator

import structlog
from fastapi import HTTPException, status

from src.models.user import User
from src.schemas.answer import AnswerRequest, AnswerResponse, AnswerSource
from src.schemas.search import SearchRequest
from src.services.answer_generator import (
    NO_RESULTS_ANSWER,
    AnswerGenerationConfigError,
    AnswerGenerationError,
    AnswerGenerationTimeoutError,
    AnswerGenerationUnavailableError,
    AnswerGenerator,
    get_answer_generator,
    sanitize_citations,
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
            conversation_context=request.conversation_context,
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
                query=request.query,
            )
            return AnswerResponse(
                query=search_response.query,
                retrieval_query=search_response.retrieval_query,
                rewritten=search_response.rewritten,
                answer=NO_RESULTS_ANSWER,
                sources=[],
            )

        # 3. Generate grounded answer from retrieved top chunks
        try:
            answer_text, used_chunks = (
                await self.answer_generator.generate_answer(
                    query=search_response.query,
                    chunks=search_response.results,
                    conversation_context=request.conversation_context,
                )
            )
        except AnswerGenerationTimeoutError as exc:
            logger.error(
                "Answer generation timed out",
                user_id=user.id,
                query=request.query,
                error=str(exc),
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
                query=request.query,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Answer generation service is currently unavailable.",
            ) from exc
        except AnswerGenerationError as exc:
            logger.error(
                "Answer generation failed",
                user_id=user.id,
                query=request.query,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate answer from document context.",
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during answer generation",
                user_id=user.id,
                query=request.query,
                error=str(exc),
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
            query=request.query,
            sources_count=len(sources),
            answer_length=len(answer_text),
        )

        return AnswerResponse(
            query=search_response.query,
            retrieval_query=search_response.retrieval_query,
            rewritten=search_response.rewritten,
            answer=answer_text,
            sources=sources,
        )

    async def stream_answer(
        self,
        request: AnswerRequest,
        user: User,
    ) -> AsyncIterator[str]:
        """
        Stream SSE formatted events: metadata, source, token, done, error.
        """
        search_req = SearchRequest(
            query=request.query,
            conversation_context=request.conversation_context,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            document_id=request.document_id,
            score_threshold=request.score_threshold,
        )
        search_response = await self.search_service.search(
            request=search_req,
            user=user,
        )

        metadata_payload = {
            "query": search_response.query,
            "retrieval_query": search_response.retrieval_query,
            "rewritten": search_response.rewritten,
        }
        yield f"event: metadata\ndata: {json.dumps(metadata_payload)}\n\n"

        if not search_response.results:
            yield f"event: source\ndata: {json.dumps({'sources': []})}\n\n"
            yield f"event: token\ndata: {json.dumps({'token': NO_RESULTS_ANSWER})}\n\n"
            yield f"event: done\ndata: {json.dumps({'answer': NO_RESULTS_ANSWER, 'sources': []})}\n\n"
            return

        usable_chunks = search_response.results[: self.answer_generator.max_context_chunks]
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
            for idx, chunk in enumerate(usable_chunks)
        ]
        sources_payload = [s.model_dump() for s in sources]
        yield f"event: source\ndata: {json.dumps({'sources': sources_payload})}\n\n"

        accumulated_tokens: list[str] = []
        try:
            token_iter, _ = await self.answer_generator.generate_answer_stream(
                query=search_response.query,
                chunks=search_response.results,
                conversation_context=request.conversation_context,
            )
            async for token in token_iter:
                accumulated_tokens.append(token)
                yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"

            raw_answer = "".join(accumulated_tokens).strip()
            if not raw_answer:
                logger.warning(
                    "Stream completed with empty tokens; invoking non-streaming fallback",
                    user_id=user.id,
                )
                try:
                    fallback_answer, _ = await self.answer_generator.generate_answer(
                        query=search_response.query,
                        chunks=usable_chunks,
                        conversation_context=request.conversation_context,
                    )
                    raw_answer = fallback_answer.strip() if fallback_answer else ""
                    if raw_answer:
                        yield f"event: token\ndata: {json.dumps({'token': raw_answer})}\n\n"
                except Exception as fb_exc:
                    logger.error("Fallback generation failed", error=str(fb_exc))

            if not raw_answer:
                raw_answer = NO_RESULTS_ANSWER
                yield f"event: token\ndata: {json.dumps({'token': raw_answer})}\n\n"

            clean_answer = sanitize_citations(raw_answer, max_source_id=len(sources))
            yield f"event: done\ndata: {json.dumps({'answer': clean_answer, 'sources': sources_payload})}\n\n"
        except Exception as exc:
            logger.error("Streaming answer failed", error=str(exc), user_id=user.id)
            try:
                fallback_answer, _ = await self.answer_generator.generate_answer(
                    query=search_response.query,
                    chunks=usable_chunks,
                    conversation_context=request.conversation_context,
                )
                raw_answer = fallback_answer.strip() if fallback_answer else NO_RESULTS_ANSWER
                clean_answer = sanitize_citations(raw_answer, max_source_id=len(sources))
                yield f"event: token\ndata: {json.dumps({'token': clean_answer})}\n\n"
                yield f"event: done\ndata: {json.dumps({'answer': clean_answer, 'sources': sources_payload})}\n\n"
            except Exception as fb_exc:
                logger.error("Non-streaming fallback failed", error=str(fb_exc))
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

