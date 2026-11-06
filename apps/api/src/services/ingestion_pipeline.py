"""Hosted processing shared by standalone and delegated document ingestion."""

import asyncio
from dataclasses import dataclass

from src.config import settings
from src.processing.chunking import DocumentChunker
from src.processing.chunking.hosted_token_limits import fit_hosted_chunks
from src.processing.cleaning import TextCleaner
from src.processing.embedding.dense_embedder import DenseEmbedder
from src.processing.extraction.document_extractor import DocumentExtractor
from src.processing.extraction.medical_observation_extractor import (
    ExtractedObservationData,
    MedicalObservationExtractor,
)
from src.processing.models.document import (
    EmbeddedDocument,
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)
from src.services.ingestion_source import IngestionSourceError


@dataclass
class ProcessedIngestion:
    extracted: ExtractedDocument
    embedded: EmbeddedDocument
    observations: list[ExtractedObservationData]


async def process_ingestion(source, storage, stage):
    await stage("downloading")
    try:
        content = await storage.download(source.storage_key)
    except Exception:
        raise IngestionSourceError("storage_unavailable", retryable=True) from None
    maximum = settings.MAX_UPLOAD_SIZE_BYTES if source.kind == "standalone" else (10 if source.client_id == "quick_clinic" else 50) * 1024 * 1024
    if not content or len(content) != source.file_size or len(content) > maximum:
        raise IngestionSourceError("invalid_document")
    await stage("extracting")
    if source.mime_type in {"text/plain", "text/markdown", "text/csv"}:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise IngestionSourceError("invalid_document") from None
        extracted = ExtractedDocument([ExtractedPage(1, [ExtractedBlock("text", text)])])
    else:
        extracted = await asyncio.to_thread(DocumentExtractor().extract, content, source.mime_type)
    del content
    if not extracted.combined_text().strip():
        raise IngestionSourceError("empty_document")
    if len(extracted.pages) > 100:
        raise IngestionSourceError("invalid_document")
    cleaned = TextCleaner().clean_document(extracted)
    await stage("chunking")
    # Public-fixture benchmark: 400/480 keeps retrieval quality with fewer hard
    # splits than 350/400. The actual Cohere tokenizer remains authoritative.
    chunker = DocumentChunker(target_tokens=min(settings.CHUNK_TARGET_TOKENS, 400), max_tokens=min(settings.CHUNK_MAX_TOKENS, 480))
    chunked = await asyncio.to_thread(chunker.chunk_document, cleaned)
    try:
        chunked = await fit_hosted_chunks(chunked)
    except ValueError:
        raise IngestionSourceError("invalid_document") from None
    if not chunked.chunks or len(chunked.chunks) > 1000:
        raise IngestionSourceError("empty_document" if not chunked.chunks else "invalid_document")
    await stage("embedding")
    embedded = await asyncio.to_thread(DenseEmbedder().embed_document, chunked)
    observations = []
    if source.client_id == "quick_clinic":
        observations = MedicalObservationExtractor().extract_from_extracted_document(
            extracted_doc=extracted, report_date=source.report_date,
            default_extraction_method="OCR" if source.mime_type.startswith("image/") else "REGEX",
        )
    return ProcessedIngestion(extracted, embedded, observations)
