"""Context dependence and selective, reauthorized PostgreSQL conversation history."""

import hashlib
import re
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import or_, select

from src.models.conversation import Conversation
from src.models.document_chunk import DocumentChunk
from src.models.medical_observation import MedicalObservation
from src.models.message import Message
from src.processing.models.search import RerankedSearchResult
from src.schemas.query_route import QueryRoute
from src.schemas.search import ConversationMessage, RewriteResult
from src.security.conversation_access import conversation_query
from src.security.structured_access import observation_fingerprint, observation_query
from src.services.compact_memory import relevant_memory_messages
from src.services.query_intent import QueryIntent, classify_intent

_DIRECT = re.compile(r"^(?:hi|hello|hey|good (?:morning|evening|afternoon)|thanks?|thank you|bye|you['’]?re welcome|happy to help|ok(?:ay)?|got it)[!.\s]*$", re.I)
_REFERENCE = re.compile(r"\b(?:what about|how about|and for|also for|previous (?:answer|response|message)|last (?:answer|response)|summari[sz]e (?:that|it)|explain (?:that|it)|those|that policy|this policy|their eligibility|what does it|what is it|what are they|which one|how does (?:it|that)|how do they|when was it|when were they|why did it|what did they)\b", re.I)
_MESSAGE_ID = re.compile(r"\b(?:message|response)\s+([0-9a-f-]{36})\b", re.I)
_STOP = {"what", "about", "does", "have", "with", "this", "that", "from", "your", "which", "where", "when", "please", "tell", "more", "previous", "answer", "response", "summarize", "summarise"}


def requires_history(query):
    if _DIRECT.fullmatch(query.strip()):
        return False
    return bool(_REFERENCE.search(query) or _MESSAGE_ID.search(query) or re.match(r"^(?:and|but|also)\b", query.strip(), re.I)
        or re.match(r"^(?:only\s+)?(?:the\s+)?(?:(?:last|past)\s+\d+\s+(?:days?|weeks?|months?)|today|yesterday)[.!?\s]*$", query.strip(), re.I))


def terms(text):
    return {word for word in re.findall(r"[\w-]+", text.casefold()) if len(word) > 3 and word not in _STOP}


@dataclass
class ContextSelection:
    requires_history: bool
    history: list[ConversationMessage] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    exact_previous_answer: str | None = None
    clarification: str | None = None


async def message_evidence(db, access, message):
    if not message.sources:
        return {}
    verified = {}
    observed = {}
    if not isinstance(message.sources, list) or len(message.sources) > 20:
        return None
    for source in message.sources:
        if isinstance(source, dict) and source.get("source_type") == "OBSERVATION":
            row = (await db.execute(observation_query(access.context).where(
                MedicalObservation.id == source.get("observation_id"),
                MedicalObservation.external_document_id == source.get("document_id"),
            ))).scalar_one_or_none()
            if row is None or observation_fingerprint(row) != source.get("content_sha256"):
                return None
            observed[row.id] = source["content_sha256"]
            continue
        if not isinstance(source, dict) or not source.get("document_id") or type(source.get("chunk_index")) is not int:
            return None
        row = (await db.execute(access.chunk_query().where(
            DocumentChunk.document_id == source["document_id"], DocumentChunk.chunk_index == source["chunk_index"],
        ))).scalar_one_or_none()
        if row is None:
            return None
        digest = hashlib.sha256(row.content.encode()).hexdigest()
        # New delegated references retain IDs/hashes instead of copying passages.
        # Legacy native references must still match their exact source text.
        if source.get("content_sha256"):
            if source["content_sha256"] != digest or source.get("chunk_id") != row.point_id or source.get("chunk_row_id") != str(row.id):
                return None
        elif not source.get("content") or source["content"] != row.content:
            return None
        verified[row.point_id] = digest
    access.observation_evidence.update(observed)
    return verified


async def select_conversation_context(access, query):
    needed = requires_history(query)
    selection = ContextSelection(needed)
    if not needed:
        return selection  # No message query for greetings or self-contained questions.
    if access is None or not access.conversation_id:
        selection.clarification = "Which earlier topic or answer are you referring to?"
        return selection
    async with access.sessions() as db:
        conversation = (await db.execute(conversation_query(access.context).where(
            Conversation.id == access.conversation_id,
        ))).scalar_one_or_none()
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        base = select(Message).where(Message.conversation_id == access.conversation_id)
        recent = list((await db.execute(base.order_by(Message.created_at.desc(), Message.id.desc()).limit(64))).scalars().all())
        matching_questions = {}
        explicit = _MESSAGE_ID.search(query)
        if not explicit:
            identifiers=await relevant_memory_messages(db,access,query)
            remembered=list((await db.execute(base.where(Message.id.in_(identifiers)))).scalars().all()) if identifiers else []
            known={message.id for message in recent}
            recent += [message for message in remembered if message.id not in known]
        if explicit:
            referenced = (await db.execute(base.where(Message.id == explicit[1]))).scalar_one_or_none()
            if referenced is None:
                selection.clarification = "Which authorized message should I use?"
                return selection
            if referenced.role != "assistant":
                selection.clarification = "Please reference the assistant answer you want me to use."
                return selection
            recent = [referenced]
        elif terms(query):
            # Bounded lexical matching finds older topic-bearing messages without
            # embedding or transmitting unrelated conversation contents.
            filters = [Message.content.ilike("%" + word.replace("%", "").replace("_", "") + "%") for word in sorted(terms(query))[:8]]
            older = list((await db.execute(base.where(or_(*filters)).order_by(Message.created_at.desc()).limit(8))).scalars().all())
            known = {message.id for message in recent}
            recent += [message for message in older if message.id not in known]
            # A topic often appears only in the question. Include its following
            # answer even when that turn falls outside the recent window.
            for question in [message for message in older if message.role == "user"]:
                following = (await db.execute(base.where(
                    Message.created_at > question.created_at,
                ).order_by(Message.created_at, Message.id).limit(1))).scalar_one_or_none()
                if following and following.role == "assistant" and following.id not in {message.id for message in recent}:
                    recent.append(following)
                if following and following.role == "assistant":
                    matching_questions[following.id] = question.content
        exact = bool(re.search(r"(?:summari[sz]e|quote|repeat|explain).*?(?:previous|last) (?:answer|response)", query, re.I) or explicit)
        assistants = [message for message in recent if message.role == "assistant" and (exact or not _DIRECT.fullmatch(message.content.strip()))]
        assistants.sort(key=lambda message: (
            0 if exact else len(terms(query) & terms(message.content + " " + matching_questions.get(message.id, ""))),
            message.created_at, message.id,
        ), reverse=True)
        pairs = []
        # Exact references always target the specified/latest response, including
        # when its evidence was revoked; never silently substitute another turn.
        for assistant in assistants[:1 if exact else 8]:
            preceding = (await db.execute(base.where(
                Message.role == "user", Message.created_at <= assistant.created_at,
            ).order_by(Message.created_at.desc(), Message.id.desc()).limit(1))).scalar_one_or_none()
            if exact or (preceding and not _DIRECT.fullmatch(preceding.content.strip())):
                score = len(terms(query) & terms(assistant.content + " " + (preceding.content if preceding else "")))
                pairs.append((score, assistant.created_at, assistant.id, preceding, assistant))
        if not pairs:
            selection.clarification = "Which earlier topic or answer are you referring to?"
            return selection
        _, _, _, preceding, assistant = max(pairs, key=lambda pair: pair[:3])
        references = await message_evidence(db, access, assistant)
        if references is None or len(assistant.content) > 12000 or (preceding and len(preceding.content) > 6000):
            selection.clarification = "I cannot use that earlier answer with your current source access. Which topic should I use?"
            return selection
        selected = ([preceding] if preceding and not _DIRECT.fullmatch(preceding.content.strip()) else []) + [assistant]
        if exact:
            selection.exact_previous_answer = assistant.content
        selected.sort(key=lambda message: (message.created_at, message.id))
        selection.history = [ConversationMessage(role=message.role, content=message.content) for message in selected]
        selection.message_ids = [message.id for message in selected]
        access.history_hashes = {message.id: hashlib.sha256(message.content.encode()).hexdigest() for message in selected}
        access.evidence.update(references)
    await access.validate()
    return selection


async def resolve_query_context(access, query, rewriter, *, deterministic_rewrite=None):
    """Shared context-first preparation; callers never supply provider history."""
    selection = await select_conversation_context(access, query)
    if access is not None:
        access.history = selection.history
        access.selected_message_ids = selection.message_ids
        access.requires_history = selection.requires_history
        access.exact_previous_answer = selection.exact_previous_answer
    unchanged = RewriteResult(original_query=query, retrieval_query=query, rewritten=False)
    if selection.clarification:
        if access is not None:
            access.intent = QueryIntent(QueryRoute.CLARIFICATION, response=selection.clarification)
        return selection, unchanged
    if selection.exact_previous_answer is not None:
        if access is not None:
            access.intent = await classify_intent(query, access, requires_history=True, exact_previous=True)
        return selection, unchanged
    result = deterministic_rewrite(query, selection.history) if selection.requires_history and deterministic_rewrite else None
    if result is None:
        result = await rewriter.rewrite(query=query, conversation_context=selection.history)
    if selection.requires_history and not result.rewritten:
        selection.clarification = "Please specify the topic or policy for this follow-up."
    if access is not None:
        access.intent = QueryIntent(QueryRoute.CLARIFICATION, response=selection.clarification) if selection.clarification else await classify_intent(
            result.retrieval_query, access, requires_history=selection.requires_history, selected_document=access.document_id)
    return selection, result


async def prior_answer_chunks(access):
    """Refetch exact prior evidence in its original citation order, without search."""
    if access is None or not access.evidence:
        return []
    await access.validate()
    async with access.sessions() as db:
        rows = (await db.execute(access.chunk_query().where(DocumentChunk.point_id.in_(list(access.evidence))))).scalars().all()
    by_id = {row.point_id: row for row in rows}
    return [RerankedSearchResult(point_id=row.point_id, document_id=row.document_id, chunk_index=row.chunk_index,
        content=row.content, token_count=row.token_count, start_page=row.start_page, end_page=row.end_page,
        page_numbers=row.page_numbers, block_types=row.block_types, rerank_score=1.0, rank=index + 1,
        client_id=row.client_id, tenant_id=row.tenant_id, collection_id=row.collection_id, owner_subject_id=row.owner_subject_id,
        patient_id=row.patient_id, source_system=row.source_system, file_name=row.file_name, document_type=row.document_type)
        for index, point_id in enumerate(access.evidence) if (row := by_id.get(point_id)) is not None]
