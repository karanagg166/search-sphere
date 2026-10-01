import asyncio
import json
import re
from abc import ABC, abstractmethod
from typing import Any

import cohere
import structlog

from src.config import settings
from src.schemas.search import ConversationMessage, RewriteResult

logger = structlog.get_logger()

REWRITE_SYSTEM_PREAMBLE = """You are an expert search query reformulator for a semantic search engine.
Analyze the user's latest query along with recent conversation history and decide whether it needs to be rewritten into a standalone retrieval query.

Rules:
1. PRESERVE CLEAR QUERIES: If the query is already clear, specific, self-contained, and understandable without conversation history (e.g. "What are the advantages of consistent hashing?"), do NOT rewrite it. Set "rewrite_needed" to false and return the query unchanged.
2. RESOLVE REFERENCES & PRONOUNS: If the query is ambiguous, incomplete, or contains pronouns/references (e.g. "which one is better?", "how does that work?", "what about eviction?") referring to topics in the conversation context, reformulate it into a single, concise standalone search query.
3. DO NOT INVENT CONTEXT: If the query contains pronouns or missing context but the conversation history does NOT contain relevant information to resolve it, do NOT guess or hallucinate facts. Set "rewrite_needed" to false and return the query unchanged.
4. NEVER ANSWER THE QUERY: You are a search query reformulator, NOT an AI assistant or answer generator. Do not provide answers, explanations, or facts.
5. NO PARAGRAPHS: The rewritten query must be a concise, natural search query (typically 3-15 words).
6. JSON OUTPUT FORMAT: Return ONLY a JSON object with this exact structure:
{"rewrite_needed": boolean, "query": "standalone query string", "reason": "short explanation"}"""


def parse_llm_json_response(raw_text: str) -> dict[str, Any] | None:
    """Safely extract and parse JSON object from LLM response text."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return None


class BaseQueryRewriteProvider(ABC):
    """Abstract base class for query rewriting LLM providers."""

    @abstractmethod
    async def rewrite(
        self,
        query: str,
        conversation_context: list[ConversationMessage] | None = None,
    ) -> RewriteResult:
        """Reformulate query into a standalone retrieval query."""
        pass


class CohereQueryRewriteProvider(BaseQueryRewriteProvider):
    """
    Cohere-backed query rewriting provider using the Command family of models.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.COHERE_API_KEY
        self.model = model or getattr(
            settings, "QUERY_REWRITE_MODEL", "command-r-08-2024"
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "QUERY_REWRITE_TIMEOUT_SECONDS", 5.0)
        )
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        return cohere.AsyncClient(
            api_key=self.api_key,
            timeout=self.timeout_seconds,
        )

    async def rewrite(
        self,
        query: str,
        conversation_context: list[ConversationMessage] | None = None,
    ) -> RewriteResult:
        """Call Cohere Chat endpoint to rewrite query into standalone search query."""
        clean_query = query.strip()

        # Handle missing or dummy API key safely
        if not self.api_key or self.api_key.strip() in (
            "",
            "your_cohere_api_key_here",
        ):
            logger.debug(
                "Cohere API key not configured or is placeholder; skipping LLM rewrite",
                query=clean_query,
            )
            return RewriteResult(
                original_query=clean_query,
                retrieval_query=clean_query,
                rewritten=False,
                reason="Cohere API key not configured",
            )

        # Build formatted prompt input
        if conversation_context:
            context_lines = [
                f"[{msg.role.capitalize()}]: {msg.content}"
                for msg in conversation_context
            ]
            history_text = "\n".join(context_lines)
        else:
            history_text = "None"

        user_message = (
            f"Conversation History:\n{history_text}\n\nUser Query: {clean_query}"
        )

        try:
            client = self._get_client()
            response = await asyncio.wait_for(
                client.chat(
                    message=user_message,
                    preamble=REWRITE_SYSTEM_PREAMBLE,
                    model=self.model,
                    temperature=0.0,
                    response_format={"type": "json_object"},
                ),
                timeout=self.timeout_seconds,
            )

            raw_text = getattr(response, "text", "")
            data = parse_llm_json_response(raw_text)
            if not data or not isinstance(data, dict):
                logger.warning(
                    "Cohere returned invalid or non-JSON response for query rewrite",
                    raw_text=raw_text[:200] if raw_text else "",
                    query=clean_query,
                )
                return RewriteResult(
                    original_query=clean_query,
                    retrieval_query=clean_query,
                    rewritten=False,
                    reason="Invalid provider response format",
                )

            rewrite_needed = bool(data.get("rewrite_needed", False))
            candidate_query = str(data.get("query", "")).strip()
            reason = str(data.get("reason", "")).strip() if data.get("reason") else None

            if not candidate_query:
                return RewriteResult(
                    original_query=clean_query,
                    retrieval_query=clean_query,
                    rewritten=False,
                    reason=reason or "Empty rewritten query returned",
                )

            # Guard against long paragraph hallucinations (answer generation instead of search query)
            if len(candidate_query.split()) > 35:
                logger.warning(
                    "Rewritten query exceeded word count limit; falling back to original query",
                    candidate_query=candidate_query,
                    query=clean_query,
                )
                return RewriteResult(
                    original_query=clean_query,
                    retrieval_query=clean_query,
                    rewritten=False,
                    reason="Rewritten query exceeded length limit",
                )

            # If unchanged in substance, mark rewritten=False
            if candidate_query.lower() == clean_query.lower():
                return RewriteResult(
                    original_query=clean_query,
                    retrieval_query=clean_query,
                    rewritten=False,
                    reason=reason or "Query is already self-contained",
                )

            return RewriteResult(
                original_query=clean_query,
                retrieval_query=candidate_query,
                rewritten=rewrite_needed,
                reason=reason,
            )

        except TimeoutError:
            logger.warning(
                "Cohere query rewrite timed out; falling back to original query",
                timeout=self.timeout_seconds,
                query=clean_query,
            )
            return RewriteResult(
                original_query=clean_query,
                retrieval_query=clean_query,
                rewritten=False,
                reason="Cohere request timed out",
            )
        except Exception as exc:
            logger.warning(
                "Cohere query rewrite failed; falling back to original query",
                error=str(exc),
                query=clean_query,
            )
            return RewriteResult(
                original_query=clean_query,
                retrieval_query=clean_query,
                rewritten=False,
                reason=f"Cohere error: {exc}",
            )


class QueryRewriter:
    """
    Coordinates standalone query reformulation before semantic retrieval.

    Responsibilities:
    - Validate query input and normalize conversation context;
    - Apply context limiting (respecting QUERY_REWRITE_MAX_CONTEXT_MESSAGES);
    - Skip rewrite when context is absent or query is already standalone;
    - Delegate execution to BaseQueryRewriteProvider;
    - Fall back safely to original query on any provider error or timeout.
    """

    def __init__(
        self,
        provider: BaseQueryRewriteProvider | None = None,
        enabled: bool | None = None,
        max_context_messages: int | None = None,
    ) -> None:
        self.provider = provider or CohereQueryRewriteProvider()
        self.enabled = (
            enabled
            if enabled is not None
            else getattr(settings, "QUERY_REWRITE_ENABLED", True)
        )
        self.max_context_messages = (
            max_context_messages
            if max_context_messages is not None
            else getattr(settings, "QUERY_REWRITE_MAX_CONTEXT_MESSAGES", 5)
        )

    async def rewrite(
        self,
        query: str,
        conversation_context: (
            list[ConversationMessage] | list[dict[str, Any]] | None
        ) = None,
    ) -> RewriteResult:
        """
        Rewrite query into standalone retrieval query if needed.
        """
        if not isinstance(query, str) or not query.strip():
            clean = query.strip() if isinstance(query, str) else ""
            return RewriteResult(
                original_query=clean,
                retrieval_query=clean,
                rewritten=False,
                reason="Empty query",
            )

        clean_query = query.strip()

        if not self.enabled:
            logger.debug(
                "Query rewriting disabled by configuration",
                query=clean_query,
            )
            return RewriteResult(
                original_query=clean_query,
                retrieval_query=clean_query,
                rewritten=False,
                reason="Query rewriting disabled",
            )

        # Normalize conversation context
        normalized_context: list[ConversationMessage] = []
        if conversation_context:
            for item in conversation_context:
                if isinstance(item, ConversationMessage):
                    normalized_context.append(item)
                elif isinstance(item, dict):
                    role = str(item.get("role", "user")).strip()
                    content = str(item.get("content", "")).strip()
                    if content:
                        normalized_context.append(
                            ConversationMessage(
                                role=role
                                if role in ("user", "assistant", "system")
                                else "user",
                                content=content,
                            )
                        )

        # Apply context window limit
        trimmed_context = (
            normalized_context[-self.max_context_messages :]
            if normalized_context
            else []
        )

        # Pre-check: If no conversation context exists, references cannot be resolved
        # from history. Avoid unnecessary LLM calls and preserve original query.
        if not trimmed_context:
            logger.debug(
                "No conversation context available; preserving original query",
                query=clean_query,
            )
            return RewriteResult(
                original_query=clean_query,
                retrieval_query=clean_query,
                rewritten=False,
                reason="No conversation context to resolve references",
            )

        try:
            result = await self.provider.rewrite(
                query=clean_query,
                conversation_context=trimmed_context,
            )

            # Defensive validation: ensure non-empty retrieval query
            if not result.retrieval_query or not result.retrieval_query.strip():
                return RewriteResult(
                    original_query=clean_query,
                    retrieval_query=clean_query,
                    rewritten=False,
                    reason="Provider returned empty retrieval query",
                )

            if result.retrieval_query.strip().lower() == clean_query.lower():
                return RewriteResult(
                    original_query=clean_query,
                    retrieval_query=clean_query,
                    rewritten=False,
                    reason=result.reason or "Query is already self-contained",
                )

            logger.info(
                "Query reformulation completed",
                original_query=clean_query,
                retrieval_query=result.retrieval_query,
                rewritten=result.rewritten,
                reason=result.reason,
            )
            return result

        except Exception as exc:
            logger.exception(
                "Unexpected error during query rewriting; falling back to original query",
                error=str(exc),
                query=clean_query,
            )
            return RewriteResult(
                original_query=clean_query,
                retrieval_query=clean_query,
                rewritten=False,
                reason=f"Unexpected error: {exc}",
            )


# Global cached query rewriter instance for dependency injection
_query_rewriter_instance: QueryRewriter | None = None


def get_query_rewriter() -> QueryRewriter:
    """
    FastAPI dependency returning shared QueryRewriter instance.
    Can be overridden in tests via app.dependency_overrides.
    """
    global _query_rewriter_instance
    if _query_rewriter_instance is None:
        _query_rewriter_instance = QueryRewriter()
    return _query_rewriter_instance
