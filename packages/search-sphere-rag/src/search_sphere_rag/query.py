"""Query classification, history-dependency detection, and query rewriting."""

import re
from collections.abc import Awaitable, Callable, Sequence

from .contracts import ConversationTurn, QueryIntentResult, QueryIntentRoute, QueryRewriteResult

_DIRECT_GREETING = re.compile(
    r"^(?:hi|hello|hey|good (?:morning|afternoon|evening)|thanks?|thank you|bye|ok(?:ay)?|got it)[!.\s]*$",
    re.I,
)
_REFERENCE_PATTERN = re.compile(
    r"\b(?:it|its|that|this|those|these|they|them|the report|the lab|the scan|the prescription|"
    r"what about|how about|and for|also for|previous|earlier|last answer|above|summarize that|explain that)\b",
    re.I,
)
_AMBIGUOUS = re.compile(
    r"^(?:help|do it|do that|yes|no|this|that|it|which|continue|please)[!.?\s]*$",
    re.I,
)
_STATUS_PATTERN = re.compile(
    r"\b(?:status|progress|processing)\b.*?\b(?:document|file|upload|report)\b|"
    r"\b(?:document|file|upload|report)\b.*?\b(?:status|progress|processing)\b",
    re.I,
)

LlmRewriteFn = Callable[[str, Sequence[ConversationTurn]], Awaitable[str | None]]


def requires_history(query: str) -> bool:
    """Return True when the query references earlier conversation turns."""
    clean = query.strip()
    if not clean or _DIRECT_GREETING.fullmatch(clean):
        return False
    return bool(
        _REFERENCE_PATTERN.search(clean)
        or re.match(r"^(?:and|but|also)\b", clean, re.I)
    )


def preserves_query_identifiers(
    query: str,
    candidate: str,
    history: Sequence[ConversationTurn],
) -> bool:
    """Guard against rewritten queries dropping IDs/dates/negations or inventing numbers."""
    ignored = {
        "What", "Which", "When", "Where", "Who", "Why", "How", "Explain", "Tell",
        "Please", "Summarize", "Summarise", "And", "Also", "But", "Is", "Are",
        "Can", "Does", "Do", "The", "It", "I", "My",
    }
    tokens = re.findall(r"[\w]+(?:[-./][\w]+)*", query)
    protected = {
        tok.casefold()
        for tok in tokens
        if any(ch.isdigit() for ch in tok)
        or tok.casefold() in {"not", "no", "never", "without"}
        or (tok[:1].isupper() and tok not in ignored)
    }
    candidate_tokens = {tok.casefold() for tok in re.findall(r"[\w]+(?:[-./][\w]+)*", candidate)}
    available = query + " " + " ".join(turn.content for turn in history)
    known_numbers = {
        tok.casefold()
        for tok in re.findall(r"[\w]+(?:[-./][\w]+)*", available)
        if any(ch.isdigit() for ch in tok)
    }
    new_numbers = {tok for tok in candidate_tokens if any(ch.isdigit() for ch in tok)}
    return protected <= candidate_tokens and new_numbers <= known_numbers


def classify_query_intent(query: str, *, history_needed: bool | None = None) -> QueryIntentResult:
    """Classify a user query deterministically without leaking untrusted instructions."""
    clean = query.strip()
    needed = requires_history(clean) if history_needed is None else history_needed

    if _DIRECT_GREETING.fullmatch(clean):
        return QueryIntentResult(
            route=QueryIntentRoute.DIRECT,
            confidence=1.0,
            requires_history=False,
            reason="greeting_or_ack",
        )
    if not clean or _AMBIGUOUS.fullmatch(clean):
        return QueryIntentResult(
            route=QueryIntentRoute.CLARIFICATION,
            confidence=0.2,
            requires_history=needed,
            reason="ambiguous_query",
        )
    if _STATUS_PATTERN.search(clean):
        return QueryIntentResult(
            route=QueryIntentRoute.TOOL_API,
            confidence=0.9,
            requires_history=needed,
            reason="document_status_query",
        )
    return QueryIntentResult(
        route=QueryIntentRoute.RAG,
        confidence=0.9,
        requires_history=needed,
        reason="document_retrieval_default",
    )


async def rewrite_query(
    query: str,
    history: Sequence[ConversationTurn] | None = None,
    *,
    llm_rewriter: LlmRewriteFn | None = None,
) -> QueryRewriteResult:
    """Rewrite a context-dependent query into a standalone search query safely."""
    clean = query.strip()
    needed = requires_history(clean)
    turns = list(history or [])

    if not clean or not needed or not turns:
        return QueryRewriteResult(
            original_query=query,
            rewritten_query=clean,
            was_rewritten=False,
            requires_history=needed,
            reason="standalone_or_no_history",
        )

    if llm_rewriter is not None:
        try:
            candidate = await llm_rewriter(clean, turns[-6:])
            if (
                isinstance(candidate, str)
                and 3 <= len(candidate.strip()) <= 300
                and preserves_query_identifiers(clean, candidate.strip(), turns)
            ):
                return QueryRewriteResult(
                    original_query=query,
                    rewritten_query=candidate.strip(),
                    was_rewritten=candidate.strip() != clean,
                    requires_history=True,
                    reason="llm_rewrite",
                )
        except Exception:
            pass

    prior_user = [
        turn.content.strip()
        for turn in reversed(turns)
        if turn.role.upper() == "USER" and turn.content.strip()
    ]
    if not prior_user:
        return QueryRewriteResult(
            original_query=query,
            rewritten_query=clean,
            was_rewritten=False,
            requires_history=True,
            reason="no_prior_user_turn",
        )

    snippet = prior_user[0][:200]
    rewritten = f"{clean} (Context: {snippet})"
    return QueryRewriteResult(
        original_query=query,
        rewritten_query=rewritten,
        was_rewritten=True,
        requires_history=True,
        reason="context_fallback_rewrite",
    )
