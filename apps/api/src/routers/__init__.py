from src.routers.auth import router as auth_router
from src.routers.documents import router as documents_router
from src.routers.search import router as search_router

__all__ = ["auth_router", "documents_router", "search_router"]
