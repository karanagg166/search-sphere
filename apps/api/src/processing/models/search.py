from dataclasses import dataclass


@dataclass(frozen=True)
class DenseSearchResult:
    """
    Application-level representation of a retrieved document chunk from
    dense ANN search.

    Fields:
    - point_id: deterministic UUID string of the point in Qdrant
    - score: dense similarity score (Cosine distance ranking)
    - document_id: ID of the source document
    - chunk_index: 0-indexed position within the document
    - content: clean text content of the chunk
    - token_count: token count of the chunk
    - start_page: 1-indexed first page number spanned by the chunk
    - end_page: 1-indexed last page number spanned by the chunk
    - page_numbers: list of pages spanned
    - block_types: source block types contained
    - rank: optional 1-indexed ranking position in search results
    - client_id: optional client application identifier
    - tenant_id: optional tenant identifier
    - collection_id: optional collection identifier
    - owner_subject_id: optional subject identifier
    - patient_id: backward-compatible patient identifier alias
    - source_system: backward-compatible source system alias
    - document_type: optional source document type
    - report_date: optional report date ISO string
    - file_name: optional source file name
    """

    point_id: str
    score: float
    document_id: str
    chunk_index: int
    content: str
    token_count: int
    start_page: int
    end_page: int
    page_numbers: list[int]
    block_types: list[str]
    rank: int | None = None
    client_id: str | None = None
    tenant_id: str | None = None
    collection_id: str | None = None
    owner_subject_id: str | None = None
    patient_id: str | None = None
    source_system: str | None = None
    document_type: str | None = None
    report_date: str | None = None
    file_name: str | None = None


@dataclass(frozen=True)
class SparseSearchResult:
    """
    Application-level representation of a retrieved document chunk from
    sparse lexical (BM25) search.
    """

    point_id: str
    score: float
    document_id: str
    chunk_index: int
    content: str
    token_count: int
    start_page: int
    end_page: int
    page_numbers: list[int]
    block_types: list[str]
    rank: int | None = None
    client_id: str | None = None
    tenant_id: str | None = None
    collection_id: str | None = None
    owner_subject_id: str | None = None
    patient_id: str | None = None
    source_system: str | None = None
    document_type: str | None = None
    report_date: str | None = None
    file_name: str | None = None


@dataclass(frozen=True)
class HybridSearchResult:
    """
    Application-level representation of a retrieved document chunk from
    hybrid retrieval combining dense semantic and sparse BM25 lexical search
    using Reciprocal Rank Fusion (RRF).
    """

    point_id: str
    score: float
    document_id: str
    chunk_index: int
    content: str
    token_count: int
    start_page: int
    end_page: int
    page_numbers: list[int]
    block_types: list[str]
    rank: int | None = None
    client_id: str | None = None
    tenant_id: str | None = None
    collection_id: str | None = None
    owner_subject_id: str | None = None
    patient_id: str | None = None
    source_system: str | None = None
    document_type: str | None = None
    report_date: str | None = None
    file_name: str | None = None


@dataclass(frozen=True)
class RerankedSearchResult:
    """
    Application-level representation of a retrieved document chunk after
    cross-encoder reranking.
    """

    point_id: str
    document_id: str
    chunk_index: int
    content: str
    token_count: int
    start_page: int
    end_page: int
    page_numbers: list[int]
    block_types: list[str]
    rerank_score: float
    rrf_score: float | None = None
    score: float | None = None
    rank: int | None = None
    client_id: str | None = None
    tenant_id: str | None = None
    collection_id: str | None = None
    owner_subject_id: str | None = None
    patient_id: str | None = None
    source_system: str | None = None
    document_type: str | None = None
    report_date: str | None = None
    file_name: str | None = None

    def __post_init__(self) -> None:
        if self.score is None:
            object.__setattr__(self, "score", float(self.rerank_score))
