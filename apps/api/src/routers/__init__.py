from src.routers.answer import router as answer_router
from src.routers.auth import router as auth_router
from src.routers.conversations import router as conversations_router
from src.routers.documents import router as documents_router
from src.routers.feedback import router as feedback_router
from src.routers.search import router as search_router

__all__ = [
    "auth_router",
    "documents_router",
    "search_router",
    "answer_router",
    "conversations_router",
    "feedback_router",
]


