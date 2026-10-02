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


@dataclass(frozen=True)
class SparseSearchResult:
    """
    Application-level representation of a retrieved document chunk from
    sparse lexical (BM25) search.

    The score is the raw BM25 lexical relevance score calculated by
    Qdrant (IDF modifier).
    It is NOT a probability or confidence value, and must NOT be compared directly to
    dense cosine scores due to differing score distributions and scales. Later hybrid
    search phases will fuse these results using rank-based reciprocal rank fusion (RRF).

    Fields:
    - point_id: deterministic UUID string of the point in Qdrant
    - score: raw BM25 lexical similarity score
    - document_id: ID of the source document
    - chunk_index: 0-indexed position within the document
    - content: clean text content of the chunk
    - token_count: token count of the chunk
    - start_page: 1-indexed first page number spanned by the chunk
    - end_page: 1-indexed last page number spanned by the chunk
    - page_numbers: list of pages spanned
    - block_types: source block types contained
    - rank: optional 1-indexed ranking position in search results
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


@dataclass(frozen=True)
class HybridSearchResult:
    """
    Application-level representation of a retrieved document chunk from
    hybrid retrieval combining dense semantic and sparse BM25 lexical search
    using Reciprocal Rank Fusion (RRF).

    The score is the RRF fusion ranking score calculated by Qdrant.
    It represents fused rank positions rather than a raw vector cosine distance
    or raw BM25 score. It is NOT a probability or confidence value.

    Fields:
    - point_id: deterministic UUID string of the point in Qdrant
    - score: RRF fusion ranking score from Qdrant
    - document_id: ID of the source document
    - chunk_index: 0-indexed position within the document
    - content: clean text content of the chunk
    - token_count: token count of the chunk
    - start_page: 1-indexed first page number spanned by the chunk
    - end_page: 1-indexed last page number spanned by the chunk
    - page_numbers: list of pages spanned
    - block_types: source block types contained
    - rank: optional 1-indexed ranking position in fused search results
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


@dataclass(frozen=True)
class RerankedSearchResult:
    """
    Application-level representation of a retrieved document chunk after
    cross-encoder reranking.

    The rerank_score is the cross-encoder relevance score output by the model.
    The rrf_score (if candidate was from hybrid RRF retrieval) is preserved
    separately without being overwritten.

    Fields:
    - point_id: deterministic UUID string of the point in Qdrant
    - document_id: ID of the source document
    - chunk_index: 0-indexed position within the document
    - content: clean text content of the chunk
    - token_count: token count of the chunk
    - start_page: 1-indexed first page number spanned by the chunk
    - end_page: 1-indexed last page number spanned by the chunk
    - page_numbers: list of pages spanned
    - block_types: source block types contained
    - rerank_score: cross-encoder relevance score
    - rrf_score: optional original RRF score from earlier hybrid retrieval
    - score: float matching rerank_score for polymorphic consistency
    - rank: optional 1-indexed ranking position after cross-encoder reranking
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

    def __post_init__(self) -> None:
        if self.score is None:
            object.__setattr__(self, "score", float(self.rerank_score))

