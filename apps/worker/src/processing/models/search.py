from dataclasses import dataclass


@dataclass(frozen=True)
class DenseSearchResult:
    """
    Application-level representation of a retrieved document chunk from
    dense ANN search.
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
