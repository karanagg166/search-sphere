import uuid
from typing import Any

import structlog
from qdrant_client import AsyncQdrantClient, models

from src.config import settings
from src.processing.embedding import DEFAULT_EMBEDDING_DIMENSION
from src.processing.models.document import EmbeddedDocument
from src.processing.models.search import DenseSearchResult

logger = structlog.get_logger()

# Deterministic namespace UUID for generating reproducible Qdrant point IDs
QDRANT_POINT_NAMESPACE = uuid.UUID("7b6f3f0e-54d9-4f71-a477-0d5885f8cfbb")


class QdrantVectorStoreError(Exception):
    """Raised when an operation against Qdrant fails."""


# Domain-consistent alias
VectorStoreError = QdrantVectorStoreError


def generate_point_id(document_id: str, chunk_index: int) -> str:
    """
    Generate a deterministic UUID string for a document chunk.

    Ensures that processing the same document_id and chunk_index always yields
    the identical point ID across retries and processes, avoiding duplicates.
    """
    name = f"{document_id}:{chunk_index}"
    return str(uuid.uuid5(QDRANT_POINT_NAMESPACE, name))


class QdrantVectorStore:
    """
    Manages vector storage and indexing in Qdrant for EmbeddedDocuments.

    Responsibilities:
    - Manage AsyncQdrantClient connection;
    - Ensure target collection exists with expected dense vector parameters;
    - Validate existing collection configuration to prevent silent corruption;
    - Ensure keyword payload index on document_id;
    - Convert EmbeddedChunk items to Qdrant PointStructs with deterministic UUID5 IDs;
    - Synchronize document state: delete stale chunks before batch upserting;
    - Batch upsert points to minimize network roundtrips;
    - Surface structured vector store errors with underlying context.
    """

    def __init__(
        self,
        client: AsyncQdrantClient | None = None,
        collection_name: str | None = None,
        vector_dimension: int | None = None,
        batch_size: int | None = None,
        url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.collection_name = collection_name or settings.QDRANT_COLLECTION_NAME
        self.vector_dimension = (
            vector_dimension
            if vector_dimension is not None
            else DEFAULT_EMBEDDING_DIMENSION
        )
        self.batch_size = (
            batch_size if batch_size is not None else settings.QDRANT_UPSERT_BATCH_SIZE
        )

        self._owns_client = client is None
        if client is not None:
            self.client = client
        else:
            resolved_url = url or settings.QDRANT_URL
            resolved_key = api_key or settings.QDRANT_API_KEY
            self.client = AsyncQdrantClient(
                url=resolved_url,
                api_key=resolved_key if resolved_key else None,
            )

        self._collection_ensured = False

    async def ensure_collection(self, force_check: bool = False) -> None:
        """
        Verify configured Qdrant collection exists and has matching vector parameters.
        Creates the collection if missing, or validates existing dimensions and metric.
        """
        if self._collection_ensured and not force_check:
            return

        try:
            exists = await self.client.collection_exists(self.collection_name)
        except Exception as exc:
            logger.exception(
                "Failed to check Qdrant collection existence",
                collection=self.collection_name,
                error=str(exc),
            )
            raise QdrantVectorStoreError(
                f"Failed to check existence of collection "
                f"'{self.collection_name}': {exc}"
            ) from exc

        if not exists:
            try:
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=self.vector_dimension,
                        distance=models.Distance.COSINE,
                    ),
                )
                logger.info(
                    "Created Qdrant collection",
                    collection=self.collection_name,
                    vector_size=self.vector_dimension,
                    distance="Cosine",
                )
            except Exception as exc:
                logger.exception(
                    "Failed to create Qdrant collection",
                    collection=self.collection_name,
                    error=str(exc),
                )
                raise QdrantVectorStoreError(
                    f"Failed to create collection '{self.collection_name}': {exc}"
                ) from exc

            await self._ensure_payload_indexes()
        else:
            try:
                info = await self.client.get_collection(self.collection_name)
            except Exception as exc:
                logger.exception(
                    "Failed to inspect Qdrant collection",
                    collection=self.collection_name,
                    error=str(exc),
                )
                raise QdrantVectorStoreError(
                    f"Failed to inspect collection '{self.collection_name}': {exc}"
                ) from exc

            self._validate_collection_vectors(info)
            await self._ensure_payload_indexes(info)

        self._collection_ensured = True

    def _validate_collection_vectors(self, info: Any) -> None:
        """Validate that an existing collection's vector configuration is compatible."""
        params = (
            getattr(info.config, "params", None) if hasattr(info, "config") else None
        )
        vectors = getattr(params, "vectors", None) if params else None

        target_vector_params: Any = None
        if isinstance(vectors, models.VectorParams):
            target_vector_params = vectors
        elif isinstance(vectors, dict):
            if "" in vectors:
                target_vector_params = vectors[""]
            elif "default" in vectors:
                target_vector_params = vectors["default"]
            elif len(vectors) == 1:
                target_vector_params = next(iter(vectors.values()))
            else:
                raise QdrantVectorStoreError(
                    f"Collection '{self.collection_name}' has unrecognized "
                    f"multi-vector configuration: {list(vectors.keys())}."
                )
        elif hasattr(vectors, "size") and hasattr(vectors, "distance"):
            target_vector_params = vectors
        else:
            raise QdrantVectorStoreError(
                f"Collection '{self.collection_name}' vector configuration "
                f"is missing or unrecognized."
            )

        actual_size = getattr(target_vector_params, "size", None)
        if actual_size != self.vector_dimension:
            raise QdrantVectorStoreError(
                f"Incompatible vector dimension in collection "
                f"'{self.collection_name}': expected {self.vector_dimension}, "
                f"but collection is configured with {actual_size}. "
                f"Manual migration required; existing vectors will not be "
                f"silently modified."
            )

        actual_distance = getattr(target_vector_params, "distance", None)
        dist_val = (
            getattr(actual_distance, "value", None)
            if actual_distance is not None
            else None
        )
        is_cosine = (
            actual_distance == models.Distance.COSINE
            or (dist_val is not None and str(dist_val).lower() == "cosine")
            or (
                actual_distance is not None
                and str(actual_distance).lower() in ("cosine", "distance.cosine")
            )
        )
        if not is_cosine:
            raise QdrantVectorStoreError(
                f"Incompatible distance metric in collection "
                f"'{self.collection_name}': expected {models.Distance.COSINE}, "
                f"but collection is configured with {actual_distance}."
            )

    async def _ensure_payload_indexes(self, info: Any = None) -> None:
        """Ensure keyword payload index exists for document_id field."""
        payload_schema = getattr(info, "payload_schema", None) if info else None
        if isinstance(payload_schema, dict) and "document_id" in payload_schema:
            return

        try:
            await self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="document_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            logger.info(
                "Ensured payload index on document_id",
                collection=self.collection_name,
                field="document_id",
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "already exists" in err_msg:
                return
            logger.exception(
                "Failed to create payload index for document_id",
                collection=self.collection_name,
                error=str(exc),
            )
            raise QdrantVectorStoreError(
                f"Failed to create payload index for 'document_id' in "
                f"collection '{self.collection_name}': {exc}"
            ) from exc

    async def delete_document_points(self, document_id: str) -> None:
        """Delete all points belonging to a specific document_id."""
        delete_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id),
                )
            ]
        )
        try:
            await self.client.delete(
                collection_name=self.collection_name,
                points_selector=delete_filter,
                wait=True,
            )
            logger.info(
                "Deleted existing Qdrant points for document",
                document_id=document_id,
                collection=self.collection_name,
            )
        except Exception as exc:
            logger.exception(
                "Failed to delete existing points for document",
                document_id=document_id,
                collection=self.collection_name,
                error=str(exc),
            )
            raise QdrantVectorStoreError(
                f"Failed to delete points for document '{document_id}' from "
                f"collection '{self.collection_name}': {exc}"
            ) from exc

    def convert_to_points(
        self,
        document_id: str,
        document: EmbeddedDocument,
    ) -> list[models.PointStruct]:
        """Convert all EmbeddedChunk objects to Qdrant PointStruct instances."""
        points: list[models.PointStruct] = []
        for chunk in document.chunks:
            point_id = generate_point_id(document_id, chunk.chunk_index)
            payload = {
                "document_id": document_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "page_numbers": list(chunk.page_numbers),
                "block_types": list(chunk.block_types),
            }
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=chunk.embedding,
                    payload=payload,
                )
            )
        return points

    async def _upsert_batch(self, batch: list[models.PointStruct]) -> None:
        """Upsert a single batch of points into Qdrant."""
        try:
            await self.client.upsert(
                collection_name=self.collection_name,
                points=batch,
                wait=True,
            )
        except Exception as exc:
            logger.exception(
                "Failed to upsert points batch to Qdrant",
                collection=self.collection_name,
                batch_size=len(batch),
                error=str(exc),
            )
            raise QdrantVectorStoreError(
                f"Failed to upsert batch of {len(batch)} points into "
                f"collection '{self.collection_name}': {exc}"
            ) from exc

    async def index_document(
        self,
        document_id: str,
        document: EmbeddedDocument,
    ) -> int:
        """
        Synchronize Qdrant vector index with an EmbeddedDocument.

        Steps:
        1. Ensure collection exists and validate vector configuration.
        2. Delete any existing points for document_id (stale chunks cleanup).
        3. If document.chunks is empty, return 0.
        4. Convert chunks to deterministic PointStructs.
        5. Batch upsert points according to batch_size.
        6. Return total points written.
        """
        await self.ensure_collection()
        await self.delete_document_points(document_id)

        if not document.chunks:
            logger.info(
                "Document has no chunks to index; cleaned up existing points",
                document_id=document_id,
                collection=self.collection_name,
                points_written=0,
            )
            return 0

        points = self.convert_to_points(document_id, document)
        total_points = len(points)

        for i in range(0, total_points, self.batch_size):
            batch = points[i : i + self.batch_size]
            await self._upsert_batch(batch)

        logger.info(
            "Document vector indexing completed",
            document_id=document_id,
            collection=self.collection_name,
            points_written=total_points,
            embedding_dimension=self.vector_dimension,
        )
        return total_points

    def convert_scored_point(
        self,
        point: Any,
        rank: int | None = None,
    ) -> DenseSearchResult:
        """
        Convert a raw Qdrant ScoredPoint into an application-level DenseSearchResult.

        Validates all required payload fields and preserves the exact Qdrant
        similarity score.

        Raises:
            QdrantVectorStoreError: If point payload is missing, incomplete,
                or malformed.
        """
        point_id = getattr(point, "id", None)
        payload = getattr(point, "payload", None)
        score = getattr(point, "score", None)

        if not isinstance(payload, dict):
            raise QdrantVectorStoreError(
                f"Missing or invalid payload dictionary in Qdrant point '{point_id}'."
            )

        if score is None:
            raise QdrantVectorStoreError(
                f"Missing similarity score in Qdrant point '{point_id}'."
            )

        required_fields = (
            "document_id",
            "chunk_index",
            "content",
            "token_count",
            "start_page",
            "end_page",
            "page_numbers",
            "block_types",
        )
        for field in required_fields:
            if field not in payload or payload[field] is None:
                raise QdrantVectorStoreError(
                    f"Missing required payload field '{field}' in Qdrant "
                    f"point '{point_id}'."
                )

        try:
            return DenseSearchResult(
                point_id=str(point_id),
                score=float(score),
                document_id=str(payload["document_id"]),
                chunk_index=int(payload["chunk_index"]),
                content=str(payload["content"]),
                token_count=int(payload["token_count"]),
                start_page=int(payload["start_page"]),
                end_page=int(payload["end_page"]),
                page_numbers=list(payload["page_numbers"]),
                block_types=list(payload["block_types"]),
                rank=rank,
            )
        except (ValueError, TypeError) as exc:
            raise QdrantVectorStoreError(
                f"Invalid payload field types in Qdrant point '{point_id}': {exc}"
            ) from exc

    async def search_dense(
        self,
        query_vector: list[float],
        limit: int,
        document_id: str | None = None,
        score_threshold: float | None = None,
    ) -> list[DenseSearchResult]:
        """
        Execute approximate nearest neighbor (ANN) search on dense vectors in Qdrant.

        Args:
            query_vector: Dense vector representing the search query.
            limit: Maximum number of Top-K results to return (must be > 0).
            document_id: Optional document ID to filter points server-side.
            score_threshold: Optional similarity score threshold.

        Returns:
            Ordered list of DenseSearchResult items preserving Qdrant ranking.

        Raises:
            QdrantVectorStoreError: If parameters are invalid, the search request fails,
                                    or result payloads are missing/malformed.
        """
        if not query_vector:
            raise QdrantVectorStoreError("query_vector must not be empty.")

        if (
            self.vector_dimension is not None
            and len(query_vector) != self.vector_dimension
        ):
            raise QdrantVectorStoreError(
                f"Query vector dimension mismatch: expected {self.vector_dimension}, "
                f"got {len(query_vector)}."
            )

        if limit <= 0:
            raise QdrantVectorStoreError(f"limit must be positive, got {limit}.")

        query_filter: models.Filter | None = None
        if document_id is not None:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=document_id),
                    )
                ]
            )

        try:
            response = await self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit,
                query_filter=query_filter,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            logger.exception(
                "Failed to execute dense search query in Qdrant",
                collection=self.collection_name,
                limit=limit,
                document_id=document_id,
                error=str(exc),
            )
            raise QdrantVectorStoreError(
                f"Failed to query dense vectors from collection "
                f"'{self.collection_name}': {exc}"
            ) from exc

        points = getattr(response, "points", None)
        if points is None:
            return []

        return [
            self.convert_scored_point(point, rank=idx + 1)
            for idx, point in enumerate(points)
        ]

    async def close(self) -> None:
        """Close client connection if initialized and owned by this instance."""
        if self._owns_client and self.client is not None:
            await self.client.close()

    async def __aenter__(self) -> "QdrantVectorStore":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
