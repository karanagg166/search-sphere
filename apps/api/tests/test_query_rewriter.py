import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.search import ConversationMessage
from src.services.query_rewriter import (
    BaseQueryRewriteProvider,
    CohereQueryRewriteProvider,
    QueryRewriter,
    parse_llm_json_response,
)

# ==============================================================================
# 1. JSON Parsing Helper Unit Tests
# ==============================================================================


def test_parse_llm_json_clean() -> None:
    raw = '{"rewrite_needed": true, "query": "Redis eviction policies", "reason": "added Redis"}'
    data = parse_llm_json_response(raw)
    assert data is not None
    assert data["rewrite_needed"] is True
    assert data["query"] == "Redis eviction policies"


def test_parse_llm_json_with_markdown_fences() -> None:
    raw = """```json
    {
      "rewrite_needed": false,
      "query": "What is consistent hashing?",
      "reason": "already clear"
    }
    ```"""
    data = parse_llm_json_response(raw)
    assert data is not None
    assert data["rewrite_needed"] is False
    assert data["query"] == "What is consistent hashing?"


def test_parse_llm_json_with_surrounding_text() -> None:
    raw = 'Here is the reformulated query:\n{"rewrite_needed": true, "query": "How does Redis LRU work?"}\nHope this helps!'
    data = parse_llm_json_response(raw)
    assert data is not None
    assert data["query"] == "How does Redis LRU work?"


def test_parse_llm_json_invalid() -> None:
    raw = "I cannot rewrite this query."
    data = parse_llm_json_response(raw)
    assert data is None


# ==============================================================================
# 2. QueryRewriter Unit & Behavior Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_clear_query_preserved_without_rewrite() -> None:
    """
    Clear, self-contained queries must not be rewritten (rewritten=False).
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "rewrite_needed": False,
            "query": "What is consistent hashing?",
            "reason": "Query is already self-contained.",
        }
    )
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    context = [
        ConversationMessage(role="user", content="Tell me about distributed systems."),
        ConversationMessage(
            role="assistant",
            content="Distributed systems are composed of multiple autonomous nodes.",
        ),
    ]
    result = await rewriter.rewrite(
        query="What is consistent hashing?",
        conversation_context=context,
    )

    assert result.rewritten is False
    assert result.original_query == "What is consistent hashing?"
    assert result.retrieval_query == "What is consistent hashing?"


@pytest.mark.asyncio
async def test_pronoun_reference_query_rewritten_with_context() -> None:
    """
    Context-dependent query with pronoun ('how does that work?') must be resolved
    using conversation history.
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "rewrite_needed": True,
            "query": "How does Redis LRU cache eviction work?",
            "reason": "Resolved pronoun 'that' using Redis LRU eviction context.",
        }
    )
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    context = [
        ConversationMessage(role="user", content="Explain Redis LRU cache eviction."),
        ConversationMessage(
            role="assistant",
            content="Redis approximates LRU by sampling a small number of keys.",
        ),
    ]
    result = await rewriter.rewrite(
        query="how does that work?",
        conversation_context=context,
    )

    assert result.rewritten is True
    assert result.original_query == "how does that work?"
    assert result.retrieval_query == "How does Redis LRU cache eviction work?"
    assert result.reason is not None


@pytest.mark.asyncio
async def test_incomplete_query_with_context_includes_topic() -> None:
    """
    Incomplete follow-up query ('what about eviction?') with Redis context must
    include Redis in the standalone query.
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "rewrite_needed": True,
            "query": "What are Redis eviction policies and mechanisms?",
            "reason": "Added Redis context to incomplete query.",
        }
    )
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    context = [
        ConversationMessage(role="user", content="Explain Redis memory management."),
    ]
    result = await rewriter.rewrite(
        query="what about eviction?",
        conversation_context=context,
    )

    assert result.rewritten is True
    assert result.original_query == "what about eviction?"
    assert "Redis" in result.retrieval_query
    assert "eviction" in result.retrieval_query


@pytest.mark.asyncio
async def test_no_useful_context_preserves_query() -> None:
    """
    Ambiguous query ('what about that?') with no relevant history must NOT invent facts,
    and must preserve the original query with rewritten=False.
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "rewrite_needed": False,
            "query": "what about that?",
            "reason": "No relevant context to resolve 'that'.",
        }
    )
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    # Context that is completely unrelated
    context = [
        ConversationMessage(role="user", content="Hello, good morning!"),
        ConversationMessage(
            role="assistant", content="Good morning! How can I assist you today?"
        ),
    ]
    result = await rewriter.rewrite(
        query="what about that?",
        conversation_context=context,
    )

    assert result.rewritten is False
    assert result.original_query == "what about that?"
    assert result.retrieval_query == "what about that?"


@pytest.mark.asyncio
async def test_no_conversation_context_skips_llm() -> None:
    """
    When no conversation context is provided at all, rewriting is skipped immediately
    without calling the LLM provider, saving latency and cost.
    """
    mock_provider = AsyncMock(spec=BaseQueryRewriteProvider)
    rewriter = QueryRewriter(provider=mock_provider)

    result = await rewriter.rewrite(
        query="What is consistent hashing?",
        conversation_context=None,
    )

    assert result.rewritten is False
    assert result.original_query == "What is consistent hashing?"
    assert result.retrieval_query == "What is consistent hashing?"
    mock_provider.rewrite.assert_not_called()


@pytest.mark.asyncio
async def test_empty_conversation_context_list_skips_llm() -> None:
    """
    When an empty list is passed as conversation_context, provider is not called.
    """
    mock_provider = AsyncMock(spec=BaseQueryRewriteProvider)
    rewriter = QueryRewriter(provider=mock_provider)

    result = await rewriter.rewrite(
        query="what about that?",
        conversation_context=[],
    )

    assert result.rewritten is False
    assert result.retrieval_query == "what about that?"
    mock_provider.rewrite.assert_not_called()


@pytest.mark.asyncio
async def test_provider_timeout_fallback() -> None:
    """
    When Cohere times out, service falls back safely to original query without crashing.
    """
    mock_client = MagicMock()
    mock_client.chat = AsyncMock(side_effect=TimeoutError("Request timed out"))

    provider = CohereQueryRewriteProvider(
        api_key="test-api-key",
        client=mock_client,
        timeout_seconds=0.1,
    )
    rewriter = QueryRewriter(provider=provider)

    context = [ConversationMessage(role="user", content="Explain Redis.")]
    result = await rewriter.rewrite(
        query="how does eviction work?",
        conversation_context=context,
    )

    assert result.rewritten is False
    assert result.original_query == "how does eviction work?"
    assert result.retrieval_query == "how does eviction work?"
    assert "timed out" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_provider_api_error_fallback() -> None:
    """
    When Cohere raises an API exception (rate limit, network error, 500), service falls back
    gracefully to original query.
    """
    mock_client = MagicMock()
    mock_client.chat = AsyncMock(
        side_effect=RuntimeError("Cohere API rate limit exceeded")
    )

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    context = [ConversationMessage(role="user", content="Explain Redis.")]
    result = await rewriter.rewrite(
        query="which one is better?",
        conversation_context=context,
    )

    assert result.rewritten is False
    assert result.original_query == "which one is better?"
    assert result.retrieval_query == "which one is better?"


@pytest.mark.asyncio
async def test_invalid_provider_response_fallback() -> None:
    """
    When Cohere returns malformed non-JSON text, service safely falls back to original query.
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Sorry, I am unable to process this request at the moment."
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    context = [ConversationMessage(role="user", content="Explain Redis.")]
    result = await rewriter.rewrite(
        query="which one is best?",
        conversation_context=context,
    )

    assert result.rewritten is False
    assert result.retrieval_query == "which one is best?"
    assert result.original_query == "which one is best?"


@pytest.mark.asyncio
async def test_context_limiting_respects_max_messages() -> None:
    """
    Ensure only the configured recent messages are passed to the provider,
    truncating older history.
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "rewrite_needed": True,
            "query": "Which eviction policy is best?",
            "reason": "rewritten",
        }
    )
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    # Set max_context_messages to 2
    rewriter = QueryRewriter(provider=provider, max_context_messages=2)

    # Pass 5 messages
    context = [
        ConversationMessage(role="user", content="Message 1 (old)"),
        ConversationMessage(role="assistant", content="Message 2 (old)"),
        ConversationMessage(role="user", content="Message 3 (old)"),
        ConversationMessage(role="user", content="Message 4 (recent)"),
        ConversationMessage(role="assistant", content="Message 5 (recent)"),
    ]

    await rewriter.rewrite(
        query="which one is best?",
        conversation_context=context,
    )

    # Inspect the message passed to mock_client.chat
    called_message = mock_client.chat.call_args[1]["message"]
    assert "Message 4 (recent)" in called_message
    assert "Message 5 (recent)" in called_message
    assert "Message 1 (old)" not in called_message
    assert "Message 2 (old)" not in called_message
    assert "Message 3 (old)" not in called_message


@pytest.mark.asyncio
async def test_original_query_preserved_in_result() -> None:
    """
    Verify original user query remains completely intact and accessible
    even after a successful rewrite.
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "rewrite_needed": True,
            "query": "Which Redis eviction policy is best for caching workloads?",
            "reason": "expanded reference",
        }
    )
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    raw_user_query = "  which one is best for cache?  "
    context = [
        ConversationMessage(role="user", content="Explain Redis eviction policies")
    ]

    result = await rewriter.rewrite(query=raw_user_query, conversation_context=context)

    # Clean stripped original query is preserved
    assert result.original_query == "which one is best for cache?"
    assert (
        result.retrieval_query
        == "Which Redis eviction policy is best for caching workloads?"
    )
    assert result.rewritten is True


@pytest.mark.asyncio
async def test_disabled_query_rewriting_setting() -> None:
    """
    When QUERY_REWRITE_ENABLED is False, rewriter immediately returns original query.
    """
    mock_provider = AsyncMock(spec=BaseQueryRewriteProvider)
    rewriter = QueryRewriter(provider=mock_provider, enabled=False)

    context = [ConversationMessage(role="user", content="Explain Redis.")]
    result = await rewriter.rewrite(
        query="which one is better?",
        conversation_context=context,
    )

    assert result.rewritten is False
    assert result.original_query == "which one is better?"
    assert result.retrieval_query == "which one is better?"
    mock_provider.rewrite.assert_not_called()


@pytest.mark.asyncio
async def test_missing_or_dummy_cohere_key_fallback() -> None:
    """
    When COHERE_API_KEY is not configured or is a placeholder, provider safely falls back.
    """
    provider = CohereQueryRewriteProvider(api_key="your_cohere_api_key_here")
    rewriter = QueryRewriter(provider=provider)

    context = [ConversationMessage(role="user", content="Explain Redis.")]
    result = await rewriter.rewrite(
        query="which one is best?",
        conversation_context=context,
    )

    assert result.rewritten is False
    assert result.retrieval_query == "which one is best?"
    assert "not configured" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_paragraph_hallucination_guard() -> None:
    """
    If the LLM generates a long paragraph (>35 words) instead of a search query,
    the guard falls back to the original query to prevent answering the question.
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    # 40+ word answer
    long_answer = (
        "In Redis, allkeys-lru evicts the least recently used keys out of all keys regardless "
        "of whether an expire was set, whereas volatile-lru only evicts keys with an expiration date. "
        "Therefore, allkeys-lru is recommended when you want to use Redis as a general LRU cache."
    )
    mock_resp.text = json.dumps(
        {
            "rewrite_needed": True,
            "query": long_answer,
            "reason": "answered question",
        }
    )
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    context = [ConversationMessage(role="user", content="Explain Redis.")]
    result = await rewriter.rewrite(
        query="which one is best?",
        conversation_context=context,
    )

    assert result.rewritten is False
    assert result.retrieval_query == "which one is best?"
    assert "length limit" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_accepts_dict_conversation_context() -> None:
    """
    QueryRewriter should gracefully accept plain dictionaries in conversation_context.
    """
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "rewrite_needed": True,
            "query": "Which Redis eviction policy is best?",
            "reason": "resolved",
        }
    )
    mock_client.chat = AsyncMock(return_value=mock_resp)

    provider = CohereQueryRewriteProvider(api_key="test-api-key", client=mock_client)
    rewriter = QueryRewriter(provider=provider)

    dict_context = [{"role": "user", "content": "Explain Redis eviction policies"}]
    result = await rewriter.rewrite(
        query="which one is best?",
        conversation_context=dict_context,
    )

    assert result.rewritten is True
    assert result.retrieval_query == "Which Redis eviction policy is best?"
