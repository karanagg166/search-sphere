# Search Sphere repair and rollout ledger

This is the active acceptance ledger for the backend repair requested on 2026-11-06.
Statuses describe observed code and execution separately. A unit test or healthy
endpoint is not proof of a completed live journey. No phase is complete until its
required production checks pass. All fixtures must be synthetic or public.

## Baseline recorded before implementation

Fetched `origin/main` in all repositories; existing worktrees were clean:

| Repository | Baseline main |
| --- | --- |
| Search Sphere | `3a9dfb6c36af2c4daf53d273d7d308d51418aa8f` |
| Quick-Clinic | `ca5eef0b91b1321c88641ac6322535958dda645c` |
| ExamArena | `3356287df05b95bf305d4d2c7d0d99e444dc750b` |

Read the three AGENTS.md files and integration guides. The requested staged rollout
supersedes Search Sphere's old upload-only phase restriction. This request expressly
includes PR delivery, review, merge and deployment. Preserve unrelated Quick-Clinic
PR #107 (`vercel/react-flight-nextjs-rce-adviso-3mshp7`). No open Search Sphere or
ExamArena PR was found. Search Sphere main has no legacy branch protection configured;
this is not evidence that checks can be skipped.

| Requirement | Baseline | Existing implementation / gap |
| --- | --- | --- |
| Deny anonymous and unintended authenticated database access | Missing | All 13 public relations have RLS disabled and SELECT grants for both roles; HEAD-only production probes confirm row visibility. `apps/api/alembic/versions/001_initial_schema.py` through `009_add_pgvector_chunks.py` contain no lock-down. |
| Inspect Data API dependencies, grants, schemas, access history | Partial | App paths use backend SQL (`apps/api/src/db.py`, `vector_store/supabase_vector_store.py`) and Storage HTTP (`storage/object_storage.py`), no table REST/RPC consumers found across the three apps. Platform API settings and historical logs need management access. |
| Preserve private original files | Partial | Server-side service key required in production (`storage/object_storage.py`); live `documents` bucket is PUBLIC with an ALL policy for PUBLIC. This must be repaired with the database exposure. |
| PostgreSQL / pgvector authoritative vector store | Working | Default backend `supabase` in `config.py`, migration 009, parameterized lexical and dense retrieval plus RRF in `vector_store/supabase_vector_store.py`. Live capacity and isolation still need release verification. |
| Durable standalone ingestion and restart recovery | Partial | `services/durable_processor.py` has status-encoded CAS leases, heartbeats and bounded retries; local-model memory pressure can indefinitely prevent claiming jobs. |
| Durable medical and generic ingestion | Missing | `tasks/document_tasks.py`, `routers/internal_medical_ingestion.py`, `services/generic_document_service.py` still dispatch through RabbitMQ. |
| Idempotent registration and scope delegation | Partial | Unique client/tenant/document identity in `models/external_document.py`; scope context and DB rechecks exist in `security/service_context.py`, `services/generic_rag_service.py`. Registration resets existing status; inspect duplicate/reindex behavior before reuse. |
| Queue progress, age and terminal reasons | Partial | Standalone lease state encoded in `Document.status`; medical error/status fields exist. No unified durable job telemetry. |
| Consumer status propagation, including FAILED | Partial | Quick-Clinic client exposes status but route mappings need verification; ExamArena upload maps any non-READY upstream state to PROCESSING (`backend/app/course_materials/service.py:173`), refresh handles FAILED. |
| Doctor/patient and school/class authorization | Partial | Quick-Clinic `src/lib/medical-record-access.ts`; ExamArena `backend/app/course_materials/permissions.py`; Search Sphere scoped service credentials and filters exist. End-to-end revocation/isolation needs seeded live checks. |
| Future-client versioned integration | Partial | `/api/v1` routers, service registration script and integration docs exist; docs still describe retired Qdrant/local model stack. |
| Real PostgreSQL/pgvector pre-deploy migrations and tests | Partial | `.github/workflows/ci.yml` runs migrations on pgvector Postgres; relevant tests mix SQLite and mocked paths. Required duplicate/recovery/reindex/consumer failure coverage must exercise real PostgreSQL. |
| Three authenticated post-deploy journeys | Missing | Existing workflows smoke health/contracts; optional provider assertions and missing credentials can allow incomplete checks. Search Sphere run [42094111951](https://github.com/karanagg166/search-sphere/actions/runs/42094111951) failed: uploaded for 120s. |
| Trusted release events, protected secrets, exact serving SHA | Partial | Search Sphere workflow_run checks main/success and checks out release SHA, smoke asserts health revision. Protected environment and all three mandatory journeys remain to implement/verify. |
| Correct pagination, strict delete verification, physical cleanup | Partial | `scripts/ci_production_smoke.py` contains fixture manifest and cleanup, but sweep/deletion and interrupted-run persistence need repair. |
| Native PDF layout and prose cleanup | Partial | PyMuPDF extraction retains pages, bounding boxes and text/image order (`processing/extraction/document_extractor.py`); cleaners and semantic chunking exist. Column ordering and exclusions need labeled fixtures. |
| Hosted image OCR/captioning | Superseded | Current `ocr_processor.py` / `image_captioner.py` use Tesseract/local models. Replace inference with eligible hosted providers; deterministic preprocessing can remain. |
| Structured tables and charts | Missing | No dedicated persistent table/chart representation, verified-value handling, cross-page continuation or chart extraction contract. |
| Hosted batched/versioned embeddings, hosted reranking | Superseded | `processing/embedding/dense_embedder.py`, `retrieval/reranker.py`, `processing/chunking/semantic_splitter.py` load local models. Production Dockerfile downloads weights. Preserve retrieval behavior via hosted APIs and bounded reindex/cutover. |
| Provider $0 quotas, availability, data eligibility | Missing | Cohere generation exists but no enforced provider approval/classification boundary or quota budget ledger. Sensitive data must fail closed. Provider tests must use synthetic/public data. |
| Context dependence separate from intent | Partial | `services/query_rewriter.py` rewrites recent history; no complete independent context classification or selective older-turn retrieval. Add maternity-leave/interns and exact-prior-response regressions. |
| Validated DIRECT/RAG/GENERAL_LLM/TOOL_API/CLARIFICATION routing | Missing | Medical structured router exists; shared policy-controlled route enum and low-confidence handling absent. |
| Hybrid search, bounded RRF, reranking, adjacent chunks | Partial | Dense/lexical/RRF in pgvector store, local reranker, DB rechecks in generic RAG. Hosted rerank, adjacency by version, exact IDs and evaluation remain. |
| Grounding, source permissions, medical structured values | Partial | `services/answer_generator.py`, `generic_rag_service.py`, `medical_query_router.py`, `medical_observation_service.py`; server grounding and citation models exist. Streaming validation, provider eligibility, injection and revocation need broader coverage. |
| RBAC/ABAC and narrow authorized tools | Partial | User ownership and delegated client/tenant/collection/subject scope exist. Department/position/grants and registered tools must map to actual client policy; arbitrary tools must stay disabled. |
| Conversation isolation and compact memory | Partial | `models/conversation.py` ties native conversation to user; `models/message.py` separates messages and stores JSON sources. Tenant/client-scoped compact memory and versioned evidence references absent. |
| Permission-aware result/answer cache | Missing | No shared authoritative permission/version-aware cache implementation found; avoid enabling cross-scope cache. |
| Atomic version replacement and resilient deletion | Partial | Document/chunk deletion methods exist, but `SupabaseVectorStore.index_document` deletes old vectors before new insertion. Version staging, tombstone and verified cleanup retry required. |
| Historical missing-vector reconciliation | Partial | `scripts/backfill_pgvector_chunks.py` and maintenance scope backfill have safety checks; bounded dry-run recovery needs verification against hosted index version. |
| Quality, latency, capacity, cold-start and quota evaluation | Partial | `evaluation/` and tests contain metrics; no measured hosted baseline, P95/free-tier budget or three-app cold-start evidence. |

## Delivery sequence

1. Security PR: close public database/Storage access, prove backend SQL and denial,
   preserve Auth, record available historical-access evidence without contents.
2. Ingestion/consumer PRs: one PostgreSQL queue for all paths; hosted inference boundary,
   status propagation and compatibility; release gates accompany these repairs.
3. Layout/index PRs: four block contracts, hosted extraction, benchmark, staged reindex.
4. Query PRs: authorized context/routing, hybrid retrieval, grounding and guarded streaming.
5. Memory/change PRs: scoped compact memory, versions, cache invalidation, deletion/recovery.
6. Release evidence: review/CI/merge, exact revisions and all three authenticated synthetic
   journeys, negative isolation, cleanup, failure bounds and verified $0 budget.

## Access and verification ledger

- Existing SQL credentials connect as backend `postgres` (RLS bypass), PostgreSQL 17.6;
  current Alembic revision 009. Metadata-only inspection and HTTP HEAD probes only.
- Connected Supabase accounts do not list project `jyzjgoniuwexvipbbpwa`; management
  settings/logs/advisors are unavailable until an authorized account is connected.
- Render connector requires explicit workspace selection; confirmation requested.
- Docker was stopped at task start; starting the installed OrbStack runtime for isolated
  PostgreSQL/pgvector checks. Never run tests against a production DATABASE_URL.
- Baseline CI: Search Sphere [42096536829](https://github.com/karanagg166/search-sphere/actions/runs/42096536829)
  passed; this does not prove ingestion. ExamArena [41691828845](https://github.com/karanagg166/ExamArena/actions/runs/41691828845)
  reported smoke success; full required RAG journey is not established by that result.

## Rollout results

Open. No repair PR merged yet. Production Alembic migration
`010_backend_data_api_lockdown` was applied successfully after isolated PostgreSQL 16
and pgvector migration tests. Executed verification:

- Six security/private-link regression tests passed; four existing document API tests
  passed against isolated PostgreSQL. Ruff and diff whitespace checks passed.
- Metadata inspection: zero public backend tables without RLS; zero browser-readable
  public relations. Backend SQL remained operational. No sensitive contents fetched.
- Publishable-key HEAD requests for users, service clients (including populated-hash
  predicates), and medical observations denied with 401. A newly created synthetic
  Supabase Auth user logged in successfully; its HEAD probes for users, service
  clients, medical observations and chunks denied with 403. The Auth fixture was deleted.
- `documents` bucket is private and its audited legacy PUBLIC policy is removed.
  Synthetic secret-key upload/download, signed download, anonymous public/direct denial,
  and deletion passed. Other buckets and Auth schemas were not modified.
- Deployed API `/health` and `/ready` remain 200. The serving code is still baseline
  `3a9dfb6c36af2c4daf53d273d7d308d51418aa8f` until the security PR deploys.
- The original standalone stall is superseded on current main: existing smoke
  [42097054938](https://github.com/karanagg166/search-sphere/actions/runs/42097054938)
  passed upload/index/retrieval/answer/citations/SSE after #29. This does not satisfy
  the requested hosted-only stack, physical cleanup or consumer journeys.
- Confirmed exposure is distinct from historical misuse: available probes establish
  that rows were reachable. No evidence of historical third-party access has been
  established. Management logs/settings review remains open.
- Project-scoped Codex and AGY MCP files were added locally. Codex recognizes the
  project URL and OAuth login succeeded. Installed AGY CLI 1.4.12 lists global
  servers only; workspace configuration recognition still needs verification.

Append PR links, exact serving revisions and workflow runs as phases progress;
do not convert pending checks to PASS.
