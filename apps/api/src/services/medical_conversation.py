"""Medical compatibility conversation preparation and immutable source references."""

import hashlib

from sqlalchemy import select

from src.inference.policy import ProviderNotApprovedError
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.medical_observation import MedicalObservation
from src.schemas.search import RewriteResult
from src.security.data_screening import contains_secrets
from src.security.query_access import current_query_access
from src.security.structured_access import observation_fingerprint, observation_query
from src.services.conversation_context import resolve_query_context
from src.services.delegated_conversations import append_turn
from src.services.medical_query_router import ELLIPTICAL_FOLLOWUP_PATTERN


async def prepare_medical_context(query, rewriter, router):
    def temporal_reference(query, history):
        if not ELLIPTICAL_FOLLOWUP_PATTERN.search(query):
            return None
        routed = router.route_query(query, conversation_context=history)
        if routed.effective_query != query and routed.target_observation_types:
            return RewriteResult(original_query=query, retrieval_query=routed.effective_query, rewritten=True)
        return None
    return await resolve_query_context(current_query_access(), query, rewriter, deterministic_rewrite=temporal_reference)


async def complete_medical_turn(db, query, result):
    access = current_query_access()
    if access is None:
        raise ProviderNotApprovedError("Medical query authorization is required.")
    await access.validate()
    if contains_secrets(result.answer):
        raise ProviderNotApprovedError("Medical response failed credential screening.")
    sources = []
    for citation in result.citations:
        if citation.source_type == "OBSERVATION":
            row = (await db.execute(observation_query(access.context).where(
                MedicalObservation.id == citation.observation_id,
                MedicalObservation.external_document_id == citation.document_id,
            ))).scalar_one_or_none()
            if row is None:
                raise ProviderNotApprovedError("Medical observation access changed.")
            digest = observation_fingerprint(row)
            access.observation_evidence[row.id] = digest
            citation.version_id = await db.scalar(select(ExternalDocument.current_version_id).where(
                ExternalDocument.source_system == row.source_system, ExternalDocument.tenant_id == row.tenant_id,
                ExternalDocument.external_document_id == row.external_document_id))
            sources.append({"source_type": "OBSERVATION", "observation_id": row.id,
                            "document_id": row.external_document_id, "version_id": citation.version_id, "content_sha256": digest})
        else:
            row = (await db.execute(access.chunk_query().where(
                DocumentChunk.document_id == citation.document_id, DocumentChunk.chunk_index == citation.chunk_index,
            ))).scalar_one_or_none()
            if row is None or row.content != citation.content:
                raise ProviderNotApprovedError("Medical source access or version changed.")
            digest = hashlib.sha256(row.content.encode()).hexdigest()
            access.evidence[row.point_id] = digest
            access.evidence_versions[row.point_id] = row.metadata_json.get("version_id")
            citation.version_id = row.metadata_json.get("version_id")
            citation.chunk_id = row.point_id
            sources.append({"document_id": row.document_id, "chunk_index": row.chunk_index, "chunk_id": row.point_id,
                            "chunk_row_id": str(row.id), "version_id": row.metadata_json.get("version_id"),
                            "source_sha256": row.metadata_json.get("source_sha256"), "content_sha256": digest})
    await access.validate()
    result.requires_history = access.requires_history
    result.context_message_ids = access.selected_message_ids
    if access.conversation_id:
        message = await append_turn(db, access, query, result.answer, sources, result.retrieval_query, result.rewritten)
        await db.commit()
        result.message_id = message.id
    return result
