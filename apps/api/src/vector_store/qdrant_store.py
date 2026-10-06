import json
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from qdrant_client import AsyncQdrantClient, models

from src.config import settings
from src.processing.embedding import DEFAULT_EMBEDDING_DIMENSION
from src.processing.models.document import EmbeddedDocument
from src.processing.models.search import (
    DenseSearchResult,
    HybridSearchResult,
    SparseSearchResult,
)
from src.processing.models.sparse_vector import SparseVector

if TYPE_CHECKING:
    pass

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
    - Ensure target collection exists with dense and named sparse vector params;
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
        sparse_vector_name: str | None = None,
        sparse_embedder: Any = None,
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
        self.sparse_vector_name: str = str(
            sparse_vector_name or getattr(settings, "QDRANT_SPARSE_VECTOR_NAME", "bm25")
        )
        self.sparse_embedder = sparse_embedder

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
                    sparse_vectors_config={
                        self.sparse_vector_name: models.SparseVectorParams(
                            modifier=models.Modifier.IDF,
                        )
                    },
                )
                logger.info(
                    "Created Qdrant collection with dense and sparse vectors",
                    collection=self.collection_name,
                    vector_size=self.vector_dimension,
                    distance="Cosine",
                    sparse_vector=self.sparse_vector_name,
                    modifier="IDF",
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
            await self._ensure_sparse_vectors_config(info)
            await self._ensure_payload_indexes(info)

        self._collection_ensured = True

    async def _ensure_sparse_vectors_config(self, info: Any) -> None:
        """
        Validate sparse vector configuration in an existing collection.

        If the configured sparse vector is missing:
        - Attempt safe schema addition using Qdrant's update_collection API.
        - Never delete or destroy existing collection data.
        - If the Qdrant server/client does not support dynamic sparse vector addition,
          fail clearly with a descriptive QdrantVectorStoreError.
        """
        params = (
            getattr(info.config, "params", None) if hasattr(info, "config") else None
        )
        sparse_vectors = getattr(params, "sparse_vectors", None) if params else None

        if (
            isinstance(sparse_vectors, dict)
            and self.sparse_vector_name in sparse_vectors
        ):
            target_sparse = sparse_vectors[self.sparse_vector_name]
            modifier = getattr(target_sparse, "modifier", None)
            logger.info(
                "Validated existing sparse vector schema",
                collection=self.collection_name,
                sparse_vector=self.sparse_vector_name,
                modifier=str(modifier),
            )
            return

        logger.info(
            "Sparse vector missing from collection; attempting safe addition",
            collection=self.collection_name,
            sparse_vector=self.sparse_vector_name,
        )
        try:
            await self.client.update_collection(
                collection_name=self.collection_name,
                sparse_vectors_config={
                    self.sparse_vector_name: models.SparseVectorParams(
                        modifier=models.Modifier.IDF,
                    )
                },
            )
            logger.info(
                "Successfully added sparse vector schema to existing collection",
                collection=self.collection_name,
                sparse_vector=self.sparse_vector_name,
            )
        except Exception as exc:
            logger.exception(
                "Failed to dynamically add sparse vector schema to existing collection",
                collection=self.collection_name,
                sparse_vector=self.sparse_vector_name,
                error=str(exc),
            )
            raise QdrantVectorStoreError(
                f"Collection '{self.collection_name}' is missing sparse vector "
                f"'{self.sparse_vector_name}', and updating collection schema "
                f"failed: {exc}. Existing collection data has been preserved; "
                f"please ensure Qdrant server version supports dynamic sparse "
                f"vector addition or run manual migration."
            ) from exc

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
        """Ensure keyword payload index exists for document_id, source_system, patient_id, and document_type fields."""
        # CollectionInfo.payload_schema is a mapping of field names to index info.
        # Newly created collections and older client responses may omit it.
        payload_schema = getattr(info, "payload_schema", None) or {}
        fields_to_index = [
            "document_id",
            "client_id",
            "tenant_id",
            "collection_id",
            "owner_subject_id",
            "source_system",
            "patient_id",
            "document_type",
        ]
        for field in fields_to_index:
            if isinstance(payload_schema, dict) and field in payload_schema:
                continue

            try:
                await self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                logger.info(
                    f"Ensured payload index on {field}",
                    collection=self.collection_name,
                    field=field,
                )
            except Exception as exc:
                err_msg = str(exc).lower()
                if "already exists" in err_msg:
                    continue
                logger.warning(
                    f"Failed to create payload index for {field}",
                    collection=self.collection_name,
                    error=str(exc),
                )

    async def delete_document_points(self, document_id: str, filters: dict[str, Any] | None = None) -> None:
        """Delete all points belonging to a specific document_id."""
        scope = dict(filters or {})
        scope["document_id"] = document_id
        delete_filter = models.Filter(must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in scope.items() if value is not None
        ])
        if not filters:
            # Native documents have no external client/source payload.
            delete_filter.must.extend([
                models.IsEmptyCondition(is_empty=models.PayloadField(key="client_id")),
                models.IsEmptyCondition(is_empty=models.PayloadField(key="source_system")),
            ])
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
        sparse_vectors: list[SparseVector] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> list[models.PointStruct]:
        """
        Convert all EmbeddedChunk objects to Qdrant PointStruct instances.
        Supports both dense-only points (for backward compatibility) and hybrid
        points containing both unnamed dense and named sparse BM25 vectors.
        Merges optional extra_payload for external patient metadata.
        """
        if sparse_vectors is not None and len(sparse_vectors) != len(document.chunks):
            raise QdrantVectorStoreError(
                f"Mismatch between chunks ({len(document.chunks)}) and sparse vectors "
                f"({len(sparse_vectors)}) for document '{document_id}'."
            )

        points: list[models.PointStruct] = []
        for idx, chunk in enumerate(document.chunks):
            # External IDs are only unique within the authenticated client/tenant.
            identity = document_id
            if extra_payload and extra_payload.get("client_id"):
                identity = json.dumps([extra_payload["client_id"], extra_payload.get("tenant_id"), document_id], separators=(",", ":"))
            point_id = generate_point_id(identity, chunk.chunk_index)
            payload: dict[str, Any] = {
                "document_id": document_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "page_numbers": list(chunk.page_numbers),
                "block_types": list(chunk.block_types),
            }
            if extra_payload:
                payload.update(extra_payload)

            if sparse_vectors is not None:
                sv = sparse_vectors[idx]
                qdrant_sparse = sv.to_qdrant() if isinstance(sv, SparseVector) else sv
                vector_dict: dict[str, Any] = {
                    "": chunk.embedding,
                    self.sparse_vector_name: qdrant_sparse,
                }
                points.append(
                    models.PointStruct(
                        id=point_id,
                        vector=vector_dict,
                        payload=payload,
                    )
                )
            else:
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
        sparse_vectors: list[SparseVector] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> int:
        """
        Synchronize Qdrant vector index with an EmbeddedDocument.

        Steps:
        1. Ensure collection exists and validate vector configuration.
        2. Delete any existing points for document_id (stale chunks cleanup).
        3. If document.chunks is empty, return 0.
        4. Resolve sparse vectors (parameter or self.sparse_embedder if set).
        5. Convert chunks to deterministic PointStructs with extra_payload if provided.
        6. Batch upsert points according to batch_size.
        7. Return total points written.
        """
        await self.ensure_collection()
        if extra_payload and extra_payload.get("client_id"):
            await self.delete_document_points(document_id, filters={
                key: extra_payload[key] for key in ("client_id", "tenant_id")
            })
        else:
            await self.delete_document_points(document_id)

        if not document.chunks:
            logger.info(
                "Document has no chunks to index; cleaned up existing points",
                document_id=document_id,
                collection=self.collection_name,
                points_written=0,
            )
            return 0

        resolved_sparse = sparse_vectors
        if resolved_sparse is None and self.sparse_embedder is not None:
            try:
                resolved_sparse = self.sparse_embedder.embed_texts(
                    [chunk.content for chunk in document.chunks]
                )
            except Exception as exc:
                logger.exception(
                    "Failed to generate sparse vectors during indexing",
                    document_id=document_id,
                    error=str(exc),
                )
                raise QdrantVectorStoreError(
                    f"Failed to generate sparse vectors for doc '{document_id}': {exc}"
                ) from exc

        points = self.convert_to_points(
            document_id,
            document,
            sparse_vectors=resolved_sparse,
            extra_payload=extra_payload,
        )
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
                client_id=payload.get("client_id") or payload.get("source_system"),
                tenant_id=payload.get("tenant_id"),
                collection_id=payload.get("collection_id"),
                owner_subject_id=payload.get("owner_subject_id") or payload.get("patient_id"),
                patient_id=payload.get("patient_id"),
                source_system=payload.get("source_system"),
                document_type=payload.get("document_type"),
                report_date=payload.get("report_date"),
                file_name=payload.get("file_name"),
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
        patient_id: str | None = None,
        source_system: str | None = None,
        query_filter: models.Filter | None = None,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DenseSearchResult]:
        """
        Execute approximate nearest neighbor (ANN) search on dense vectors in Qdrant.

        Args:
            query_vector: Dense vector representing the search query.
            limit: Maximum number of Top-K results to return (must be > 0).
            document_id: Optional document ID to filter points server-side.
            patient_id: Optional patient ID to filter points server-side.
            source_system: Optional source system to filter points server-side.
            query_filter: Optional existing Qdrant Filter.
            score_threshold: Optional similarity score threshold.
            filters: Optional metadata filters mapping (e.g. {"patient_id": "...", "source_system": "..."}).

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

        must_conditions: list[Any] = []
        if query_filter and hasattr(query_filter, "must") and query_filter.must:
            must_conditions.extend(query_filter.must)
        if document_id is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id),
                )
            )
        resolved_patient_id = patient_id or (filters.get("patient_id") if filters else None)
        if resolved_patient_id is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="patient_id",
                    match=models.MatchValue(value=resolved_patient_id),
                )
            )
        resolved_source_system = source_system or (filters.get("source_system") if filters else None)
        if resolved_source_system is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="source_system",
                    match=models.MatchValue(value=resolved_source_system),
                )
            )
        if filters:
            for k, v in filters.items():
                if k not in ("patient_id", "source_system", "document_id") and v is not None:
                    if isinstance(v, (list, tuple)):
                        must_conditions.append(models.FieldCondition(key=k, match=models.MatchAny(any=list(v))))
                    else:
                        must_conditions.append(models.FieldCondition(key=k, match=models.MatchValue(value=v)))

        effective_filter = models.Filter(must=must_conditions) if must_conditions else (query_filter or None)

        try:
            response = await self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit,
                query_filter=effective_filter,
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

    def convert_scored_sparse_point(
        self,
        point: Any,
        rank: int | None = None,
    ) -> SparseSearchResult:
        """
        Convert a raw Qdrant ScoredPoint into an application-level SparseSearchResult.

        Validates all required payload fields and preserves the exact Qdrant
        similarity score (BM25 IDF score).

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
            return SparseSearchResult(
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
                client_id=payload.get("client_id") or payload.get("source_system"),
                tenant_id=payload.get("tenant_id"),
                collection_id=payload.get("collection_id"),
                owner_subject_id=payload.get("owner_subject_id") or payload.get("patient_id"),
                patient_id=payload.get("patient_id"),
                source_system=payload.get("source_system"),
                document_type=payload.get("document_type"),
                report_date=payload.get("report_date"),
                file_name=payload.get("file_name"),
            )
        except (ValueError, TypeError) as exc:
            raise QdrantVectorStoreError(
                f"Invalid payload field types in Qdrant point '{point_id}': {exc}"
            ) from exc

    async def search_sparse(
        self,
        query_vector: SparseVector | models.SparseVector,
        limit: int,
        document_id: str | None = None,
        patient_id: str | None = None,
        source_system: str | None = None,
        query_filter: models.Filter | None = None,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SparseSearchResult]:
        """
        Execute sparse lexical search on named BM25 vectors in Qdrant.

        Args:
            query_vector: SparseVector or Qdrant models.SparseVector for query.
            limit: Maximum number of Top-K results to return (must be > 0).
            document_id: Optional document ID to filter points server-side.
            patient_id: Optional patient ID to filter points server-side.
            source_system: Optional source system to filter points server-side.
            query_filter: Optional existing Qdrant Filter.
            score_threshold: Optional similarity score threshold.
            filters: Optional metadata filters mapping (e.g. {"patient_id": "...", "source_system": "..."}).

        Returns:
            Ordered list of SparseSearchResult items preserving Qdrant ranking.

        Raises:
            QdrantVectorStoreError: If parameters are invalid, the search request fails,
                                    or result payloads are missing/malformed.
        """
        if query_vector is None:
            raise QdrantVectorStoreError("query_vector must not be None.")

        if isinstance(query_vector, SparseVector):
            qdrant_query = query_vector.to_qdrant()
        elif isinstance(query_vector, models.SparseVector):
            qdrant_query = query_vector
        else:
            raise QdrantVectorStoreError(
                f"query_vector must be a SparseVector or models.SparseVector, "
                f"got {type(query_vector)}."
            )

        # If query vector has no active indices (e.g. stopwords only), return empty
        if not qdrant_query.indices:
            return []

        if limit <= 0:
            raise QdrantVectorStoreError(f"limit must be positive, got {limit}.")

        must_conditions: list[Any] = []
        if query_filter and hasattr(query_filter, "must") and query_filter.must:
            must_conditions.extend(query_filter.must)
        if document_id is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id),
                )
            )
        resolved_patient_id = patient_id or (filters.get("patient_id") if filters else None)
        if resolved_patient_id is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="patient_id",
                    match=models.MatchValue(value=resolved_patient_id),
                )
            )
        resolved_source_system = source_system or (filters.get("source_system") if filters else None)
        if resolved_source_system is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="source_system",
                    match=models.MatchValue(value=resolved_source_system),
                )
            )
        if filters:
            for k, v in filters.items():
                if k not in ("patient_id", "source_system", "document_id") and v is not None:
                    if isinstance(v, (list, tuple)):
                        must_conditions.append(models.FieldCondition(key=k, match=models.MatchAny(any=list(v))))
                    else:
                        must_conditions.append(models.FieldCondition(key=k, match=models.MatchValue(value=v)))

        effective_filter = models.Filter(must=must_conditions) if must_conditions else (query_filter or None)

        try:
            response = await self.client.query_points(
                collection_name=self.collection_name,
                query=qdrant_query,
                using=self.sparse_vector_name,
                limit=limit,
                query_filter=effective_filter,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            logger.exception(
                "Failed to execute sparse search query in Qdrant",
                collection=self.collection_name,
                sparse_vector_name=self.sparse_vector_name,
                limit=limit,
                document_id=document_id,
                error=str(exc),
            )
            raise QdrantVectorStoreError(
                f"Failed to query sparse vectors from collection "
                f"'{self.collection_name}': {exc}"
            ) from exc

        points = getattr(response, "points", None)
        if points is None:
            return []

        return [
            self.convert_scored_sparse_point(point, rank=idx + 1)
            for idx, point in enumerate(points)
        ]

    def convert_scored_hybrid_point(
        self,
        point: Any,
        rank: int | None = None,
    ) -> HybridSearchResult:
        """
        Convert a raw Qdrant ScoredPoint from hybrid search into an application-level
        HybridSearchResult.

        Validates all required payload fields and preserves the exact Qdrant
        RRF fusion score.

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
            return HybridSearchResult(
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
                patient_id=payload.get("patient_id"),
                source_system=payload.get("source_system"),
                document_type=payload.get("document_type"),
                report_date=payload.get("report_date"),
                file_name=payload.get("file_name"),
            )
        except (ValueError, TypeError) as exc:
            raise QdrantVectorStoreError(
                f"Invalid payload field types in Qdrant point '{point_id}': {exc}"
            ) from exc

    async def search_hybrid(
        self,
        dense_query_vector: list[float],
        sparse_query_vector: SparseVector | models.SparseVector,
        limit: int,
        candidate_limit: int,
        document_id: str | None = None,
        document_ids: list[str] | None = None,
        patient_id: str | None = None,
        source_system: str | None = None,
        filter: models.Filter | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[HybridSearchResult]:
        """
        Execute hybrid search combining dense semantic retrieval and sparse BM25
        lexical retrieval using server-side Reciprocal Rank Fusion (RRF) in Qdrant.

        Flow:
        - Prefetch #1: Dense ANN candidate retrieval against default/unnamed vector
        - Prefetch #2: Sparse BM25 candidate retrieval against named sparse vector
        - Query: models.FusionQuery(fusion=models.Fusion.RRF)
        - Final Limit: limit (Top-K fused results)
        - Candidate Limit: candidate_limit for both prefetches

        Args:
            dense_query_vector: Dense vector representing query semantics.
            sparse_query_vector: SparseVector or models.SparseVector for BM25 match.
            limit: Maximum number of final Top-K fused results (must be > 0).
            candidate_limit: Candidate prefetch limit per branch (must be >= limit).
            document_id: Optional document ID to filter points server-side.
            document_ids: Optional list of document IDs to filter points server-side.
            patient_id: Optional patient ID to filter points server-side.
            source_system: Optional source system to filter points server-side.
            filter: Optional existing Qdrant Filter.
            filters: Optional metadata filters mapping (e.g. {"patient_id": "...", "source_system": "..."}).

        Returns:
            Ordered list of HybridSearchResult items preserving Qdrant fused ranking.

        Raises:
            QdrantVectorStoreError: If parameters are invalid, search request fails,
                                    or result payloads are missing/malformed.
        """
        if not dense_query_vector:
            raise QdrantVectorStoreError("dense_query_vector must not be empty.")

        if (
            self.vector_dimension is not None
            and len(dense_query_vector) != self.vector_dimension
        ):
            raise QdrantVectorStoreError(
                f"Dense query vector dimension mismatch: expected "
                f"{self.vector_dimension}, got {len(dense_query_vector)}."
            )

        if sparse_query_vector is None:
            raise QdrantVectorStoreError("sparse_query_vector must not be None.")

        if isinstance(sparse_query_vector, SparseVector):
            qdrant_sparse = sparse_query_vector.to_qdrant()
        elif isinstance(sparse_query_vector, models.SparseVector):
            qdrant_sparse = sparse_query_vector
        else:
            raise QdrantVectorStoreError(
                f"sparse_query_vector must be a SparseVector or models.SparseVector, "
                f"got {type(sparse_query_vector)}."
            )

        if limit <= 0:
            raise QdrantVectorStoreError(f"limit must be positive, got {limit}.")

        if candidate_limit <= 0:
            raise QdrantVectorStoreError(
                f"candidate_limit must be positive, got {candidate_limit}."
            )

        if candidate_limit < limit:
            raise QdrantVectorStoreError(
                f"candidate_limit ({candidate_limit}) cannot be less than "
                f"limit ({limit})."
            )

        must_conditions: list[Any] = []
        if filter and hasattr(filter, "must") and filter.must:
            must_conditions.extend(filter.must)
        if document_ids is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchAny(any=document_ids),
                )
            )
        elif document_id is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id),
                )
            )
        resolved_patient_id = patient_id or (filters.get("patient_id") if filters else None)
        if resolved_patient_id is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="patient_id",
                    match=models.MatchValue(value=resolved_patient_id),
                )
            )
        resolved_source_system = source_system or (filters.get("source_system") if filters else None)
        if resolved_source_system is not None:
            must_conditions.append(
                models.FieldCondition(
                    key="source_system",
                    match=models.MatchValue(value=resolved_source_system),
                )
            )
        if filters:
            for k, v in filters.items():
                if k not in ("patient_id", "source_system", "document_id", "document_ids") and v is not None:
                    if isinstance(v, (list, tuple)):
                        must_conditions.append(models.FieldCondition(key=k, match=models.MatchAny(any=list(v))))
                    else:
                        must_conditions.append(models.FieldCondition(key=k, match=models.MatchValue(value=v)))

        query_filter = models.Filter(must=must_conditions) if must_conditions else (filter or None)

        dense_prefetch = models.Prefetch(
            query=dense_query_vector,
            limit=candidate_limit,
            filter=query_filter,
        )
        sparse_prefetch = models.Prefetch(
            query=qdrant_sparse,
            using=self.sparse_vector_name,
            limit=candidate_limit,
            filter=query_filter,
        )

        try:
            response = await self.client.query_points(
                collection_name=self.collection_name,
                prefetch=[dense_prefetch, sparse_prefetch],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            logger.exception(
                "Failed to execute hybrid search query in Qdrant",
                collection=self.collection_name,
                sparse_vector_name=self.sparse_vector_name,
                limit=limit,
                candidate_limit=candidate_limit,
                document_id=document_id,
                error=str(exc),
            )
            raise QdrantVectorStoreError(
                f"Failed to query hybrid vectors from collection "
                f"'{self.collection_name}': {exc}"
            ) from exc

        points = getattr(response, "points", None)
        if points is None:
            return []

        return [
            self.convert_scored_hybrid_point(point, rank=idx + 1)
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
