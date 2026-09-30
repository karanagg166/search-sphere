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
