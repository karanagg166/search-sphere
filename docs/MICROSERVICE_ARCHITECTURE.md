# Search-Sphere Microservice Architecture

Search-Sphere is a unified, multi-tenant RAG (Retrieval-Augmented Generation) and semantic-search microservice platform designed to serve multiple external client applications alongside its native standalone web application.

---

## 1. System Overview & Conceptual Architecture

```text
                                CLIENTS
      Search-Sphere Web UI     Quick-Clinic      ExamArena      Future Apps
              │                     │                │               │
              └──────────────┬──────┴────────────────┴───────────────┘
                             │
                    API & Authentication
                             │
                  Authoritative ServiceContext
                (Client, Tenant, Subject, Scopes)
                             │
                   RAG Service Interface
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
      Ingestion          Retrieval          Generation
          │                  │                  │
    PDF/Text/OCR        Dense + BM25       Cohere Chat
    Chunking            Qdrant Filters     Strict Grounding
    FastEmbed           RRF + Rerank       Citations / SSE
          │                  │                  │
          └──────────────────┼──────────────────┘
                             │
                   Persistence & Storage
          PostgreSQL (Metadata) · Qdrant (Vectors)
          Supabase Storage (Files) · RabbitMQ (Jobs)
```

---

## 2. Multi-Tenant Identity Model

Search-Sphere cleanly decouples three distinct identity tiers:

| Tier | Concept | Examples | Isolation Boundary |
| :--- | :--- | :--- | :--- |
| **Client Application** | The integrating software system | `quick_clinic`, `exam_arena`, `search_sphere` | Dedicated credential, rate limit, allowed scopes, authorized tenants list |
| **Tenant** | An organizational workspace | `clinic_1`, `school_north_101`, `workspace_alpha` | Strict vector payload isolation in Qdrant; unique DB constraint scoping |
| **Subject / User** | An individual entity | `patient_402`, `teacher_bob`, `student_99` | Optional access filter; cannot cross tenant boundaries |

### Authoritative `ServiceContext`

Every microservice request produces an authoritative `ServiceContext`:

```python
class ServiceContext:
    client_id: str
    tenant_id: str
    subject_id: str | None
    collection_id: str | None
    scopes: set[str]
    client_name: str | None
    is_service_client: bool
```

Security Rules Enforced:
1. **Authenticated Credentials**: API keys (`ss_live_...`) are verified using constant-time comparisons against database SHA-256 hashes (`ServiceClient.api_key_hash`). Raw keys are never stored or logged.
2. **Header Matching**: If an `X-Client-ID` header is passed, it must match the authenticated token's client identity; forged client IDs produce `403 Forbidden`.
3. **Tenant Authorization**: The client must be authorized for the specified `X-Tenant-ID` (either in `authorized_tenants` or `["*"]`).
4. **Non-Overridable Vector Filters**: Client-supplied metadata filters can never override `client_id`, `tenant_id`, `collection_id`, or `owner_subject_id`.
5. **Database Constraint Scoping**: `ExternalDocument` uniqueness is enforced at `(source_system, tenant_id, external_document_id)`. Two different tenants can safely use the same document ID without collision.

---

## 3. Storage & Collections Lifecycle

- **Collections (`DocumentCollection`)**: Logical groupings within a tenant (e.g. `patient_100_records`, `class10_physics`).
- **Object Storage**: Files are placed in isolated hierarchical storage keys:
  `documents/{client_id}/{tenant_id}/{collection_id}/{document_id}/{filename}`
- **Signed URLs**: Short-lived (5-60 min) signed URLs are generated only after verifying the requesting client/tenant owns the document in PostgreSQL.

---

## 4. Two-Stage Reranked Hybrid Retrieval & RAG

1. **Stage 1 (Hybrid Prefetch in Qdrant)**:
   - Dense semantic vector search (FastEmbed `all-MiniLM-L6-v2`) with mandatory Qdrant server-side filter:
     `{"client_id": ctx.client_id, "tenant_id": ctx.tenant_id, "collection_id": ...}`
   - Sparse lexical search (BM25) with the exact same server-side filter.
   - Reciprocal Rank Fusion (RRF) combines candidate chunks.
   - Dense, sparse and hybrid result mapping preserve `client_id`, `tenant_id`, `collection_id` and `owner_subject_id`, along with legacy medical aliases. Reranking must retain these fields so the subsequent scope checks can authorize results. Legacy and generic search use one vector set.
2. **Stage 2 (Cross-Encoder Reranking)**:
   - Candidates are reranked using `cross-encoder/ms-marco-MiniLM-L-6-v2`.
   - Worker scoring rejects malformed, mismatched or non-finite model outputs. If model loading or scoring fails, its public reranker logs the failure and returns candidates in RRF order; input validation errors still propagate.
3. **Post-Retrieval Verification**:
   - Chunks are verified against PostgreSQL to ensure the parent document is in `READY` status and belongs to the authorized client/tenant before exposure.
4. **Grounded Generation & Citations**:
   - Verified chunks are formatted into numbered `[SOURCE N]` context blocks.
   - Cohere Chat LLM synthesizes answers with strict grounding instructions and defense against prompt injections embedded in user documents.
   - Brackets `[N]` in the output are validated against actually provided context chunks.

---

## 5. Domain Extension Pattern: Clinical Observations

Domain-specific capabilities (such as clinical observation extraction for Quick-Clinic) exist as modular domain extensions without polluting generic pipelines:
- In `document_tasks.py`, `_process_generic_document` handles standard document extraction, chunking, embedding, and vector indexing for all clients.
- If a document is flagged as medical (`is_medical=True` or `client_id=="quick_clinic"`), the clinical observation extractor runs and populates the separate `medical_observations` table.
- Non-medical clients (e.g. ExamArena, legal tools) execute pure generic document indexing without clinical overhead.
- Existing Quick-Clinic endpoints (`/internal/medical-*`) remain 100% backward-compatible.
