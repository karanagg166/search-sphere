from enum import StrEnum


class QueryRoute(StrEnum):
    DIRECT = "DIRECT"
    RAG = "RAG"
    GENERAL_LLM = "GENERAL_LLM"
    TOOL_API = "TOOL_API"
    CLARIFICATION = "CLARIFICATION"
