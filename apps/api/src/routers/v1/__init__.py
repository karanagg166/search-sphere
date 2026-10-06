from src.routers.v1.answers import router as answers_router
from src.routers.v1.clients import router as clients_router
from src.routers.v1.collections import router as collections_router
from src.routers.v1.documents import router as documents_router
from src.routers.v1.search import router as search_router

__all__ = [
    "clients_router",
    "collections_router",
    "documents_router",
    "search_router",
    "answers_router",
]
