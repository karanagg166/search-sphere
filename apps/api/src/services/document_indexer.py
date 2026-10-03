import structlog
from sqlalchemy import select

from src.db import AsyncSessionLocal
from src.models.document import Document
from src.processing.chunking import DocumentChunker
from src.processing.cleaning import TextCleaner
from src.processing.embedding.dense_embedder import DenseEmbedder
from src.processing.extraction.document_extractor import DocumentExtractor
from src.processing.sparse_embedding.bm25_embedder import BM25Embedder
from src.storage.object_storage import get_object_storage
from src.vector_store.qdrant_store import QdrantVectorStore

logger = structlog.get_logger()


async def index_document_pipeline(document_id: str) -> bool:
    """
    In-process indexing pipeline for a document.

    Ensures that when background workers or RabbitMQ are unavailable
    (e.g., single-container web deployment on Render free tier), documents
    are fully extracted, cleaned, chunked, embedded, and indexed into Qdrant.
    """
    logger.info("Starting in-process document indexing", document_id=document_id)

    # 1. Fetch document metadata from DB
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.id == document_id)
        )
        document = result.scalar_one_or_none()
        if not document:
            logger.error("Document not found for indexing", document_id=document_id)
            return False

        storage_key = document.storage_key
        # Mark as processing
        document.status = "processing"
        await session.commit()

    storage = get_object_storage()
    vector_store = None

    try:
        # 2. Download raw PDF content
        pdf_bytes = await storage.download(storage_key)
        if not pdf_bytes:
            raise RuntimeError(f"Downloaded PDF content is empty for {storage_key}")

        logger.info(
            "PDF downloaded for in-process indexing",
            document_id=document_id,
            size_bytes=len(pdf_bytes),
        )

        # 3. Multimodal extraction
        extractor = DocumentExtractor()
        extracted_doc = extractor.extract(pdf_bytes)
        logger.info(
            "PDF text extraction completed",
            document_id=document_id,
            page_count=len(extracted_doc.pages),
            extracted_chars=len(extracted_doc.combined_text()),
        )

        # 4. Text cleaning
        cleaner = TextCleaner()
        cleaned_doc = cleaner.clean_document(extracted_doc)

        # 5. Semantic chunking
        chunker = DocumentChunker()
        chunked_doc = chunker.chunk_document(cleaned_doc)
        chunk_count = len(chunked_doc.chunks)
        logger.info(
            "Document chunking completed",
            document_id=document_id,
            chunk_count=chunk_count,
        )

        if chunk_count > 0:
            # 6. Dense embeddings (FastEmbed / SentenceTransformers)
            embedder = DenseEmbedder()
            embedded_doc = embedder.embed_document(chunked_doc)

            # 7. Sparse lexical BM25 embeddings
            sparse_embedder = BM25Embedder()
            sparse_vectors = sparse_embedder.embed_chunks(chunked_doc.chunks)

            # 8. Qdrant vector index synchronization
            vector_store = QdrantVectorStore(sparse_embedder=sparse_embedder)
            points_written = await vector_store.index_document(
                document_id=document_id,
                document=embedded_doc,
                sparse_vectors=sparse_vectors,
            )
            logger.info(
                "Document points successfully indexed in Qdrant",
                document_id=document_id,
                points_written=points_written,
            )

        # 9. Update DB record to indexed
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = "indexed"
                await session.commit()

        logger.info("Document successfully marked as indexed", document_id=document_id)
        return True

    except Exception as exc:
        logger.exception(
            "In-process document indexing failed",
            document_id=document_id,
            error=str(exc),
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = "failed"
                await session.commit()
        return False

    finally:
        if vector_store is not None:
            try:
                await vector_store.close()
            except Exception:
                pass


async def sync_unindexed_documents() -> list[str]:
    """
    Scans PostgreSQL for any documents that are in 'uploaded' status
    and triggers indexing for each.
    """
    logger.info("Scanning for unindexed uploaded documents...")
    unindexed_ids: list[str] = []

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document.id).where(Document.status.in_(["uploaded", "failed"]))
            )
            unindexed_ids = [row[0] for row in result.fetchall()]

        if not unindexed_ids:
            logger.info("No unindexed documents found")
            return []

        logger.info("Found unindexed documents to process", count=len(unindexed_ids), ids=unindexed_ids)
        successful_ids = []
        for doc_id in unindexed_ids:
            success = await index_document_pipeline(doc_id)
            if success:
                successful_ids.append(doc_id)

        return successful_ids

    except Exception as exc:
        logger.warning("Error while syncing unindexed documents", error=str(exc))
        return []
