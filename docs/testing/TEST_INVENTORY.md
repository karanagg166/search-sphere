# Search-Sphere Test Inventory & Coverage Matrix

## Architecture & Technology Stack
- **API Runtime**: FastAPI (Python 3.12), Uvicorn, Pydantic v2 Settings
- **Task Worker**: Dramatiq with RabbitMQ message broker
- **Relational Metadata**: PostgreSQL (asyncpg, SQLAlchemy 2.0, Alembic)
- **Object Storage**: Supabase Storage REST API (`documents` bucket) with local filesystem fallback for isolated unit tests
- **Vector Database**: Qdrant (Dense 384-d vectors + BM25 sparse vectors + multi-tenant payload indexing)
- **Embedding Models**: FastEmbed (`sentence-transformers/all-MiniLM-L6-v2`, `Qdrant/bm25`)
- **Reranker**: Cross-Encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`)
- **RAG Generation**: Cohere (`command-a-03-2025`) with strict citation grounding

---

## Feature-to-Test Mapping

| Functional Domain | Feature / Business Flow | Test Level | Implementation Test File | Execution Harness | Real or Mocked |
|---|---|---|---|---|---|
| **Document Ingestion** | PDF & Image binary validation, MIME sniffing | Unit / Security | `apps/api/tests/test_file_content_validation.py` | Pytest / Docker | Real binary fixtures |
| **Document Ingestion** | Private document upload & storage metadata | Integration | `apps/api/tests/test_medical_documents_storage.py` | Pytest / Docker | Isolated storage & PostgreSQL |
| **Document Ingestion** | OCR fallback & poppler page rendering | Unit / Functional | `apps/worker/tests/test_ocr_processor.py` | Pytest / Docker | Real Tesseract engine |
| **Document Extraction**| Structured text & table extraction | Functional | `apps/worker/tests/test_document_extractor.py` | Pytest / Docker | Real PDF parsing |
| **Text Processing** | Cleaning (noise, whitespace, hyphenation) | Unit | `apps/worker/tests/test_text_cleaner.py`, `test_noise_cleaner.py` | Pytest | Real deterministic |
| **Chunking** | Semantic & structure-aware window chunking | Unit / Functional | `apps/worker/tests/test_chunking.py` | Pytest / FastEmbed | Real FastEmbed splitter |
| **Dense Embeddings** | Deterministic 384-d vector generation & batching | Unit / Functional | `apps/worker/tests/test_dense_embedder.py` | Pytest / FastEmbed | Real ONNX runtime |
| **Sparse Embeddings** | BM25 term weighting & token extraction | Unit / Functional | `apps/worker/tests/test_bm25_embedder.py` | Pytest / FastEmbed | Real BM25 model |
| **Vector Indexing** | Qdrant collection lifecycle & point upsertion | Integration | `apps/worker/tests/test_qdrant_store.py`, `test_scoped_qdrant_lifecycle.py` | Pytest / Qdrant | In-memory / Server Qdrant |
| **Payload Indexing** | Multi-tenant tenant/subject filter indexes | Contract / DB | `apps/api/tests/test_payload_indexes.py`, `apps/worker/tests/test_payload_indexes.py` | Pytest / Qdrant | Real schema verification |
| **Dense Retrieval** | Cosine similarity vector search | Functional | `apps/worker/tests/test_dense_retriever.py` | Pytest / Qdrant | Real vector matching |
| **Sparse Retrieval** | BM25 keyword matching | Functional | `apps/worker/tests/test_sparse_retriever.py` | Pytest / Qdrant | Real sparse index |
| **Hybrid RRF** | Reciprocal Rank Fusion hybrid retrieval | Functional | `apps/worker/tests/test_hybrid_retriever.py` | Pytest / Qdrant | Real RRF algorithm |
| **Cross-Reranking** | Cross-encoder contextual reranking & thresholding | Functional | `apps/worker/tests/test_reranker.py`, `test_reranked_retriever.py` | Pytest / FastEmbed | Real Cross-Encoder ONNX |
| **Tenant Filtering** | Tenant isolation and tenant cross-access guards | Security / Negative | `apps/worker/tests/test_retrieval_filters.py`, `apps/api/tests/test_v1_tenant_isolation.py` | Pytest / ASGI | Real payload filters |
| **Grounded RAG** | Question answering with verified citations | AI / Functional | `apps/api/tests/test_answer.py`, `apps/api/tests/test_medical_rag.py` | Pytest / ASGI | Stubbed / Provider opt-in |
| **Query Rewriting** | Multi-turn contextual query rewriting | Unit / AI | `apps/api/tests/test_query_rewriter.py` | Pytest / ASGI | Deterministic rules |
| **Streaming RAG** | SSE chunked answer delivery & heartbeat | API / Functional | `apps/api/tests/test_streaming.py` | Pytest / ASGI | Real Async streaming |
| **Clinical Observation**| Structured observation extraction | Functional | `apps/api/tests/test_medical_observations.py` | Pytest / DB | Real PostgreSQL |
| **Query Router** | Medical observation vs unstructured router | Unit / Eval | `apps/api/tests/test_medical_router_eval.py` | Pytest | Deterministic fixtures |
| **Client Auth** | SHA-256 hashed API key authentication | Security | `apps/api/tests/test_v1_service_clients.py` | Pytest / DB | Real PostgreSQL crypto |
| **Scope Authorization** | RBAC permission matrix (`documents:read/write/delete`)| Security / RBAC | `apps/api/tests/test_auth.py`, `apps/api/tests/test_hybrid_scope_payload.py` | Pytest / ASGI | Real token auth |
| **Rate Limiting** | Sliding window per-minute rate limiting | Resilience | `apps/api/tests/test_hardening.py` | Pytest / ASGI | Real in-memory window |
| **Correlation IDs** | Request tracking headers (`X-Request-ID`) | Reliability | `apps/api/tests/test_hardening.py` | Pytest / ASGI | Real ASGI middleware |
| **Production Config** | Storage durability & secret enforcement | Security | `apps/api/tests/test_hardening.py` | Pytest / Pydantic | Real config validation |
| **Quick-Clinic Contract**| Medical compatibility adapter contract | Contract | `apps/api/tests/test_quick_clinic_contract.py` | Pytest / ASGI | Real schemas & auth |
| **Worker Ingestion** | Background OCR & indexing queue tasks | Integration | `apps/api/tests/test_tasks.py`, `apps/worker/tests/test_document_tasks.py` | Pytest / Dramatiq | Broker stub / RabbitMQ |
| **End-to-End Search** | Full document ingest to query lifecycle | E2E Integration | `apps/api/tests/test_e2e_flow.py`, `compose.integration.yml` | Docker Compose | Real Postgres + RabbitMQ + Qdrant |
| **Live Smoke Probe** | Render deployment liveness & database readiness | Live Monitoring | `.github/workflows/keep-alive.yml` | GitHub Actions | Live Render production URL |

---

## Missing / Unexecuted Test Gap Analysis

1. **Worker Tests in CI**: Previously, `apps/worker/tests` was never executed in GitHub Actions (`.github/workflows/ci.yml`). Both API and Worker suites are now independently executed.
2. **Real-Infrastructure Integration Suite**: Synthetic integration testing previously lacked automated container-orchestrated CI execution. Added multi-service container integration workflow job.
3. **Storage Fallback Vulnerability**: Fixed root cause of Render container crash by guaranteeing `STORAGE_BACKEND=supabase` in Dockerfile and config, accompanied by regression assertions.
