# Architecture Audit & Refactoring Plan: Search-Sphere Multi-Tenant RAG Microservice

## Executive Summary

Search-Sphere is being refactored from a partially medical-coupled hybrid system into a **general-purpose, production-grade, multi-tenant RAG and semantic-search microservice**. The refactored platform will independently serve:
1. **Search-Sphere Standalone UI** (Next.js native web app for personal knowledge management)
2. **Quick-Clinic** (Medical record retrieval, clinical observations, and doctor Q&A)
3. **ExamArena** (Educational document search, textbook grounding, and student Q&A)
4. **Future client applications** (Legal, customer support, enterprise intranets)

---

## 1. Actual Implementation Audit & Coupling Classification

A comprehensive audit was performed across `Search-Sphere` (`feature/medical-ai-hardening` at commit `9a1f5ee`) and `Quick-Clinic` (`main` at commit `eb93700`).

### Target Identifiers & Coupling Classification Table

| Identifier / Pattern | Location in Search-Sphere | Classification | Target Architecture Remediation |
| :--- | :--- | :--- | :--- |
| `QUICK_CLINIC_SERVICE_SECRET` | `apps/api/src/config.py`, `src/security/service_auth.py`, test fixtures | **1. Generic functionality incorrectly coupled to Quick-Clinic** | Replace with database-backed `ServiceClient` model supporting independent client credentials, hashed tokens (`api_key_hash`), tenant scopes, and credential rotation. Retain `QUICK_CLINIC_SERVICE_SECRET` as a backward-compatibility fallback. |
| `source_system = "quick_clinic"` | `models/external_document.py`, `models/medical_observation.py`, `qdrant_store.py`, `document_tasks.py` | **1. Generic functionality incorrectly coupled to Quick-Clinic** | Generalize `source_system` to `client_id` (e.g. `quick_clinic`, `exam_arena`, `search_sphere`). Retain `source_system` as a database column/synonym for full zero-downtime backward compatibility. |
| `external_patient_id` / `patient_id` (in document tables & vector payloads) | `models/external_document.py`, `qdrant_store.py`, `DenseSearchResult`, `SparseSearchResult`, `reranker.py` | **1. Generic functionality incorrectly coupled to Quick-Clinic** | Generalize to `owner_subject_id` and introduce `tenant_id` and `collection_id`. Map `external_patient_id` -> `owner_subject_id` and `collection_id` (`patient_<id>_records`). Retain `patient_id` payload field in Qdrant and model fields for Quick-Clinic compatibility. |
| `patient_id` (in clinical observations & medical query routing) | `models/medical_observation.py`, `services/medical_observation_service.py`, `services/medical_query_router.py` | **2. Legitimately medical-specific functionality** | Keep preserved in `domains/medical/` domain adapter. Quick-Clinic clinical observations (e.g. A1C, Blood Pressure, LDL) are genuine medical domain entities. |
| `medical-documents/` storage prefix | `routers/internal_medical_documents.py`, `storage/object_storage.py` | **3. Backward-compatibility code** | Generic storage keys follow `documents/{client_id}/{tenant_id}/{document_id}/{filename}`. Keep `/internal/medical-documents` routes using `medical-documents/{patient_id}/...` as an adapter. |
| `internal_medical_retrieval.py` & `internal_medical_rag.py` | `apps/api/src/routers/internal_medical_*` | **3. Backward-compatibility code** (wrapping generic RAG with medical prompts & observation merge) | Keep existing endpoints functional for Quick-Clinic doctors/patients. Have them delegate internally to the generic retrieval and generation engines, passing the medical prompt preamble and observation context as domain extensions. |
| Test files (`test_medical_*.py`) | `apps/api/tests/`, `apps/worker/tests/` | **4. Documentation/tests only** | Maintain all existing tests green (350+ tests), and add new test suites for generic client authentication, tenant isolation, cross-tenant leak prevention, collections, and non-medical client end-to-end flows. |

---

## 2. Before vs. After Architecture

### Before Refactoring (Coupled Architecture)
```
Quick-Clinic Client (Next.js)
       │ (Hardcoded QUICK_CLINIC_SERVICE_SECRET)
       ▼
/internal/medical-* Endpoints (FastAPI)
       │ (Fixed "quick_clinic" & "patient_id" filters)
       ▼
Qdrant Store (hardcoded patient_id payload schema & conditions)
       ▲
Standalone UI (separate /documents & /search endpoints with separate User auth)
```

### Target Multi-Tenant Microservice Architecture
```
                         CLIENT APPLICATIONS
   Search-Sphere UI       Quick-Clinic       ExamArena       Future Enterprise
          │                    │                 │                   │
  (Session JWT)        (Client Bearer)   (Client Bearer)     (Client Bearer)
          │                    │                 │                   │
          ▼                    ▼                 ▼                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        API GATEWAY & AUTH                              │
│  - JWT Auth (Native Users) -> ServiceContext                           │
│  - ServiceClient Auth (Hashed Token + Tenant Scope + Client Scopes)    │
│  - Backward-Compat Adapter (QUICK_CLINIC_SERVICE_SECRET -> Context)   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     UNIFIED SERVICE CONTEXT                            │
│  ServiceContext(client_id, tenant_id, collection_id, subject_id, scopes)│
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
       ┌─────────────────────────┐      ┌─────────────────────────┐
       │   GENERIC V1 API        │      │ COMPATIBILITY ADAPTERS  │
       │  /api/v1/collections    │      │ /internal/medical-*     │
       │  /api/v1/documents      │      │ (delegates to generic   │
       │  /api/v1/search         │      │  RAG + medical plugins) │
       │  /api/v1/answers        │      │                         │
       │  /api/v1/conversations  │      └────────────┬────────────┘
       └────────────┬────────────┘                   │
                    │                                │
                    └────────────────┬───────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      REUSABLE RAG ENGINE CORE                          │
│  - Ingestion & Object Storage Abstraction                              │
│  - Document Extractor (PDF / OCR / Clean / Chunk)                      │
│  - FastEmbed Dense + Sparse BM25 Embeddings                            │
│  - Qdrant Vector Store with Mandatory (client_id, tenant_id) Filtering │
│  - Reciprocal Rank Fusion (RRF) & Cross-Encoder Reranker               │
│  - Query Rewriter & Cohere Grounded Generation with Citations          │
│  - SSE Streaming & Conversation State Management                       │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
    PostgreSQL (Metadata)                       Qdrant Cloud (Vectors)
    - service_clients                           - dense (384-dim)
    - document_collections                      - sparse (bm25)
    - external_documents                        - indexed payloads:
    - users, conversations                        client_id, tenant_id,
                                                  collection_id, subject_id
```

---

## 3. Database & Migration Strategy

### New Tables (Alembic Migration `005_multi_tenant_core.py`):
1. **`service_clients`**:
   - `id`: UUID primary key
   - `client_id`: VARCHAR(64) UNIQUE NOT NULL (e.g. `quick_clinic`, `exam_arena`)
   - `name`: VARCHAR(255) NOT NULL
   - `api_key_hash`: VARCHAR(128) NOT NULL (SHA-256)
   - `api_key_prefix`: VARCHAR(16) NOT NULL
   - `status`: VARCHAR(32) NOT NULL DEFAULT 'active'
   - `allowed_scopes`: JSON NOT NULL
   - `authorized_tenants`: JSON NOT NULL (e.g. `["*"]`)
   - `rate_limit_per_minute`: INTEGER NOT NULL DEFAULT 120
   - `created_at`, `updated_at`, `revoked_at`
2. **`document_collections`**:
   - `id`: UUID primary key
   - `client_id`: VARCHAR(64) NOT NULL
   - `tenant_id`: VARCHAR(128) NOT NULL
   - `collection_id`: VARCHAR(128) NOT NULL
   - `name`: VARCHAR(255) NOT NULL
   - `description`: TEXT NULL
   - `metadata_json`: JSON NULL
   - `created_at`, `updated_at`
   - Unique constraint: `(client_id, tenant_id, collection_id)`

### Existing Table Evolution:
1. **`external_documents`**:
   - Add column `tenant_id`: VARCHAR(128) NOT NULL DEFAULT 'default'
   - Add column `collection_id`: VARCHAR(128) NULL
   - Add column `owner_subject_id`: VARCHAR(128) NULL
   - Add column `metadata_json`: JSON NULL
   - Backfill: Set `tenant_id = 'quick_clinic_default'`, `owner_subject_id = external_patient_id`, `collection_id = external_patient_id` for existing records where `source_system = 'quick_clinic'`.
   - Add index on `(source_system, tenant_id, collection_id)`.

---

## 4. Work Phases & Verification Milestones

- **Phase A**: Audit & Architecture Contract (Complete).
- **Phase B**: Service Client Authentication, Tenancy, Collections, and Security Context.
- **Phase C**: Generic Document Ingestion, Storage Abstraction, and Background Worker Tasks.
- **Phase D**: Multi-Tenant Qdrant Retrieval, Dense + BM25 RRF, Cross-Encoder Reranking, Grounded RAG, and SSE Streaming.
- **Phase E**: Stable `/api/v1/` Versioned Endpoints, Standalone Route Adapters, and Medical Compatibility.
- **Phase F**: Python SDK, TypeScript Client, ExamArena Blueprint, Full Test Suites (unit, integration, isolation, benchmarks), and Documentation.
