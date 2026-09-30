import asyncio

import dramatiq
import structlog

from src.processing.chunking import DocumentChunker
from src.processing.cleaning import TextCleaner
from src.processing.embedding import (
    DenseEmbedder,
    DenseEmbeddingError,
)
from src.processing.extraction import (
    DocumentExtractionError,
    DocumentExtractor,
)
from src.processing.models.document import EmbeddedDocument
from src.processing.sparse_embedding import (
    BM25Embedder,
    SparseEmbeddingError,
)
from src.services.document_fetcher import DocumentFetcher
from src.vector_store import QdrantVectorStore, QdrantVectorStoreError

logger = structlog.get_logger()


@dramatiq.actor(
    queue_name="default",
    actor_name="process_document_task",
    max_retries=3,
)
def process_document_task(document_id: str) -> None:
    """
    Background job responsible for processing a single document.

    Current pipeline stages:
    1. Load document metadata from PostgreSQL.
    2. Download original PDF from object storage.
    3. Extract document content (native text, OCR text, and BLIP image captions).
    4. Deterministically clean and normalize document text while preserving structure.
    5. Structure-aware semantic chunking into ChunkedDocument.
    6. Batch dense embedding generation into EmbeddedDocument.
    7. Qdrant vector indexing and stale chunk synchronization.

    Future pipeline stages:
    - Query embedding
    - Dense ANN retrieval / search
    - Sparse / BM25 hybrid search fusion (RRF)
    - Cross-encoder reranking
    - RAG answer generation
    """
    asyncio.run(_process_document(document_id))


async def _process_document(
    document_id: str,
    fetcher: DocumentFetcher | None = None,
    extractor: DocumentExtractor | None = None,
    cleaner: TextCleaner | None = None,
    chunker: DocumentChunker | None = None,
    embedder: DenseEmbedder | None = None,
    sparse_embedder: BM25Embedder | None = None,
    vector_store: QdrantVectorStore | None = None,
) -> EmbeddedDocument:
    logger.info(
        "Document processing started",
        document_id=document_id,
    )

    doc_fetcher = fetcher or DocumentFetcher()
    doc_extractor = extractor or DocumentExtractor()
    doc_cleaner = cleaner or TextCleaner()
    doc_chunker = chunker or DocumentChunker()
    doc_embedder = embedder or DenseEmbedder()
    doc_sparse_embedder = sparse_embedder or BM25Embedder()
    doc_vector_store = vector_store or QdrantVectorStore(
        sparse_embedder=doc_sparse_embedder
    )

    try:
        fetched_document = await doc_fetcher.fetch(document_id)

        logger.info(
            "Document fetched successfully",
            document_id=document_id,
            storage_key=fetched_document.storage_key,
            size=len(fetched_document.content),
        )

        extracted_document = doc_extractor.extract(fetched_document.content)

        raw_text = extracted_document.combined_text()

        logger.info(
            "Document extraction completed",
            document_id=document_id,
            pages=len(extracted_document.pages),
            extracted_characters=len(raw_text),
            preview=raw_text[:500],
        )

        cleaned_document = doc_cleaner.clean_document(extracted_document)

        cleaned_text = cleaned_document.combined_text()

        logger.info(
            "Document text cleaning completed",
            document_id=document_id,
            pages=len(cleaned_document.pages),
            raw_characters=len(raw_text),
            cleaned_characters=len(cleaned_text),
            cleaned_preview=cleaned_text[:500],
        )

        chunked_document = doc_chunker.chunk_document(cleaned_document)

        total_chunks = len(chunked_document.chunks)
        total_tokens = chunked_document.total_tokens()
        avg_tokens = (total_tokens / total_chunks) if total_chunks > 0 else 0
        min_tokens = (
            min(c.token_count for c in chunked_document.chunks)
            if total_chunks > 0
            else 0
        )
        max_tokens = (
            max(c.token_count for c in chunked_document.chunks)
            if total_chunks > 0
            else 0
        )
        first_chunk_preview = (
            chunked_document.chunks[0].content[:200] if total_chunks > 0 else ""
        )
        cleaned_blocks_count = sum(len(p.blocks) for p in cleaned_document.pages)

        logger.info(
            "Document chunking completed",
            document_id=document_id,
            pages=len(cleaned_document.pages),
            cleaned_blocks=cleaned_blocks_count,
            chunks=total_chunks,
            total_tokens=total_tokens,
            avg_chunk_tokens=round(avg_tokens, 1),
            min_chunk_tokens=min_tokens,
            max_chunk_tokens=max_tokens,
            first_chunk_preview=first_chunk_preview,
        )

        embedded_document = doc_embedder.embed_document(chunked_document)

        logger.info(
            "Document embedding completed",
            document_id=document_id,
            chunks=embedded_document.total_chunks(),
            total_embedded_chunks=embedded_document.total_chunks(),
            embedding_model=doc_embedder.model_name,
            embedding_dimension=doc_embedder.dimension,
            batch_size=doc_embedder.batch_size,
            first_chunk_preview=(
                embedded_document.chunks[0].content[:200]
                if embedded_document.total_chunks() > 0
                else ""
            ),
        )

        sparse_vectors = doc_sparse_embedder.embed_chunks(chunked_document.chunks)

        logger.info(
            "Document sparse BM25 embedding completed",
            document_id=document_id,
            chunks=len(sparse_vectors),
            sparse_model=doc_sparse_embedder.model_name,
        )

        points_written = await doc_vector_store.index_document(
            document_id,
            embedded_document,
            sparse_vectors=sparse_vectors,
        )

        logger.info(
            "Document vector indexing completed",
            document_id=document_id,
            collection=doc_vector_store.collection_name,
            points_written=points_written,
        )

        return embedded_document

    except DocumentExtractionError as exc:
        logger.error(
            "PDF extraction failed",
            document_id=document_id,
            error=str(exc),
        )
        raise

    except DenseEmbeddingError as exc:
        logger.error(
            "Document dense embedding generation failed",
            document_id=document_id,
            error=str(exc),
        )
        raise

    except SparseEmbeddingError as exc:
        logger.error(
            "Document sparse embedding generation failed",
            document_id=document_id,
            error=str(exc),
        )
        raise

    except QdrantVectorStoreError as exc:
        logger.error(
            "Document vector indexing failed",
            document_id=document_id,
            error=str(exc),
        )
        raise

    except Exception as exc:
        logger.exception(
            "Document processing failed",
            document_id=document_id,
            error=str(exc),
        )
        raise

    finally:
        if vector_store is None:
            await doc_vector_store.close()
