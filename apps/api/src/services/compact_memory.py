"""Bounded topic/intent/answer indexes; never copies source passages into memory."""

import hashlib
import json
import re

from sqlalchemy import select

from src.models.conversation import Conversation
from src.models.conversation_memory import ConversationMemory
from src.models.document_chunk import DocumentChunk
from src.schemas.query_route import QueryRoute
from src.security.conversation_access import conversation_query

_STOP = {"what","when","where","which","about","this","that","their","there","they","with","from","your","have","does","were",
         "please","explain","tell","describe","recorded","answer","previous","policy","show","summary","summarize","summarise"}


def keywords(text,maximum=16):
    output=[]
    for word in re.findall(r"[\w]+(?:[-./][\w]+)*",text.casefold()):
        if len(word)>2 and word not in _STOP and word not in output:
            output.append(word[:100])
        if len(output)==maximum:
            break
    return output


async def compact_sources(db,access,sources,message_id):
    references=[]
    for source in sources[:20]:
        if not isinstance(source,dict):
            continue
        allowed={key:source[key] for key in ["source_type","observation_id","document_id","chunk_index","chunk_id","chunk_row_id",
                 "version_id","source_sha256","content_sha256"] if key in source}
        if source.get("content") and not source.get("content_sha256"):
            row=(await db.execute(access.chunk_query().where(DocumentChunk.document_id==source.get("document_id"),
                DocumentChunk.chunk_index==source.get("chunk_index")))).scalar_one_or_none()
            if row is None or row.content!=source["content"]:
                continue
            allowed.update(chunk_id=row.point_id,chunk_row_id=str(row.id),content_sha256=hashlib.sha256(row.content.encode()).hexdigest())
            if row.metadata_json.get("version_id"):
                allowed["version_id"]=row.metadata_json["version_id"]
            provenance=row.metadata_json.get("provenance") or []
            if provenance and provenance[0].get("document_sha256"):
                allowed["source_sha256"]=provenance[0]["document_sha256"]
        allowed["source_message_id"]=message_id
        references.append(allowed)
    return references


async def update_memory(db,access,question,response):
    """Caller holds the conversation row lock and commits messages+memory together."""
    row=await db.get(ConversationMemory,question.conversation_id)
    if row is None:
        row=ConversationMemory(conversation_id=question.conversation_id,state={})
        db.add(row)
    previous=row.state or {}
    intent=access.intent.route.value if access.intent else QueryRoute.RAG.value
    intents=list(previous.get("recent_intents",[]))[-7:]+[intent]
    topics=list(previous.get("topic_turns",[]))
    if intent not in {QueryRoute.DIRECT.value,QueryRoute.CLARIFICATION.value}:
        topic=keywords(response.retrieval_query or question.content)
        if topic:
            entry={"topics":topic,"intent":intent,"user_message_id":question.id,"assistant_message_id":response.id,
                "requires_history":access.requires_history,"answer_points":keywords(response.content,12),
                "sources":await compact_sources(db,access,response.sources or [],response.id)}
            # Retain distinct topics across acknowledgment turns instead of
            # copying a rolling transcript into another JSON blob.
            topics=[item for item in topics if set(item.get("topics",[]))!=set(topic)]
            topics=(topics+[entry])[-8:]
    state={"format_version":1,"recent_intents":intents,"topic_turns":topics,"latest_message_id":response.id}
    while len(json.dumps(state,ensure_ascii=False).encode())>32768 and topics:
        if len(topics)>1:
            topics.pop(0)
        elif topics[0]["sources"]:
            topics[0]["sources"].pop(0)
        else:
            topics.pop(0)
    row.state=state
    await db.flush()


async def relevant_memory_messages(db,access,query):
    """Memory contributes IDs only; all selected messages/evidence are reauthorized."""
    memory=(await db.execute(select(ConversationMemory).join(Conversation,Conversation.id==ConversationMemory.conversation_id)
        .where(ConversationMemory.conversation_id==access.conversation_id,
               Conversation.id.in_(conversation_query(access.context).with_only_columns(Conversation.id))))).scalar_one_or_none()
    if memory is None:
        return []
    wanted=set(keywords(query))
    scored=[(len(wanted & (set(entry.get("topics",[])) | set(entry.get("answer_points",[])))),index,entry.get("assistant_message_id"))
            for index,entry in enumerate(memory.state.get("topic_turns",[]))]
    scored.sort(reverse=True)
    matches=[identifier for score,_index,identifier in scored[:8] if identifier and score>0]
    return matches or [identifier for _score,_index,identifier in sorted(scored,key=lambda item:item[1],reverse=True)[:1] if identifier]
