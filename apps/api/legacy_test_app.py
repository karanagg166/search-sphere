"""Test-only archive of the retired consumer surface; never copied into images.

Search Sphere production serves src.main:app. These isolated historical regressions
retain coverage of stored schemas and adapters without remounting them there.
"""

from fastapi import FastAPI

from src.main import app as standalone_app
from src.routers.internal_medical_conversations import (
    router as internal_medical_conversations_router,
)
from src.routers.internal_medical_documents import (
    router as internal_medical_docs_router,
)
from src.routers.internal_medical_ingestion import (
    router as internal_medical_ingest_router,
)
from src.routers.internal_medical_observations import (
    router as internal_medical_obs_router,
)
from src.routers.internal_medical_rag import router as internal_medical_rag_router
from src.routers.internal_medical_retrieval import (
    router as internal_medical_retrieval_router,
)
from src.routers.v1 import (
    answers_router as v1_answers_router,
)
from src.routers.v1 import (
    clients_router as v1_clients_router,
)
from src.routers.v1 import (
    collections_router as v1_collections_router,
)
from src.routers.v1 import (
    documents_router as v1_documents_router,
)
from src.routers.v1 import (
    search_router as v1_search_router,
)
from src.routers.v1.conversations import router as v1_conversations_router

app = FastAPI(exception_handlers=standalone_app.exception_handlers,
              middleware=standalone_app.user_middleware)
app.include_router(standalone_app.router)
app.include_router(internal_medical_docs_router)
app.include_router(internal_medical_ingest_router)
app.include_router(internal_medical_retrieval_router)
app.include_router(internal_medical_obs_router)
app.include_router(internal_medical_rag_router)
app.include_router(internal_medical_conversations_router)

# Include Generic Multi-Tenant V1 Microservice Routes
app.include_router(v1_clients_router)
app.include_router(v1_collections_router)
app.include_router(v1_documents_router)
app.include_router(v1_search_router)
app.include_router(v1_answers_router)
app.include_router(v1_conversations_router)
