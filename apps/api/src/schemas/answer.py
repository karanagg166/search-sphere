from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.schemas.search import SearchRequest


class AnswerRequest(SearchRequest):
    """
    Incoming request payload for grounded RAG answer generation.

    Inherits all validated fields from SearchRequest:
    - query: non-empty, non-whitespace string
    - conversation_context: optional list of ConversationMessage items
    - top_k: positive integer <= RERANKER_MAX_TOP_K
    - candidate_k: positive integer >= top_k
    - document_id: optional non-empty string filter
    """

    pass


class AnswerSource(BaseModel):
    """Represents a supporting source document chunk used in the generated answer."""

    source_id: int = Field(
        ...,
        description="1-based identifier corresponding to citation references (e.g. [1]) in the answer.",
    )
    document_id: str = Field(..., description="ID of source document.")
    chunk_index: int = Field(..., description="Index of chunk within document.")
    start_page: int = Field(..., description="Starting page of chunk.")
    end_page: int = Field(..., description="Ending page of chunk.")
    content: str = Field(..., description="Text content of chunk.")
    rerank_score: float = Field(
        ...,
        description="Relevance score assigned by cross-encoder reranker.",
    )


class AnswerResponse(BaseModel):
    """Top-level grounded RAG answer API response."""

    query: str = Field(..., description="Original user query.")
    retrieval_query: str = Field(
        default="",
        description="Standalone search query used for retrieval after optional reformulation.",
    )
    rewritten: bool = Field(
        default=False,
        description="Whether the query was reformulated.",
    )
    answer: str = Field(
        ...,
        description="Synthesized grounded answer with source citations.",
    )
    sources: list[AnswerSource] = Field(
        default_factory=list,
        description="Attributed source document chunks supporting the answer.",
    )

    @model_validator(mode="before")
    @classmethod
    def populate_retrieval_query(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("retrieval_query") and data.get("query"):
                data["retrieval_query"] = data["query"]
        return data
