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
| Native PDF layout and prose cleanup | Partial | PyMuPDF extraction uses bounding boxes for text/image order but discards coordinates from block data (`processing/extraction/document_extractor.py`); cleaners and semantic chunking exist. Column ordering and exclusions need labeled fixtures. |
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

Initial security checkpoint (subsequent delivery results follow below): production Alembic migration
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

### Security follow-up and consumer delivery

- Security PR [#30](https://github.com/karanagg166/search-sphere/pull/30) merged at
  `5800fa9d7cb6feb6b268ad91844e94d830f3d82f`. Render `/health` verified that exact
  revision. Main CI [42100955787](https://github.com/karanagg166/search-sphere/actions/runs/42100955787)
  and existing standalone smoke [42101418248](https://github.com/karanagg166/search-sphere/actions/runs/42101418248)
  passed. This existing smoke does not yet prove physical vector deletion or consumer journeys.
- Migration 011 restricts the effective Data API to empty `pgrst_no_exposed_schemas`,
  following the documented PostgreSQL configuration path. The platform toggle remains
  inaccessible because Supabase OAuth registration rejects REST configuration scopes.
- Eight security/configuration regression tests and fourteen native/medical document
  Storage API tests passed against isolated PostgreSQL. No local model weights were needed.
- Project OAuth now accesses the correct organization; its plan is verified `free`.
  Available 23-hour log sources were enumerated using counts only. Historical misuse
  has not been established; request metadata aggregation is still being reviewed.
- Render deployment on commit is disabled. Release orchestration must explicitly wait
  for pre-deploy CI before triggering the next backend revision.
- Quick-Clinic [#160](https://github.com/karanagg166/Quick-Clinic/pull/160) and ExamArena
  [#29](https://github.com/karanagg166/ExamArena/pull/29) preserve FAILED registration and
  return 503 on status outages. Local focused suites: 63 and 39 passed respectively,
  with type/lint checks. Both PRs are awaiting remaining CI jobs.
- Provider selection is Cohere only, per user instruction. Authenticated model metadata
  confirms embedding, reranking, text and vision availability. Trial/evaluation billing
  status still needs confirmation; model inference budget is not verified yet.

### Durable queue foundation

Migration 012 and `repositories/ingestion_queue.py` provide an atomic PostgreSQL
outbox/queue for both standalone and external document identities. Seven executed
PostgreSQL tests cover duplicate registration, rollback, concurrent SKIP LOCKED
claims, restart recovery, live-lease preservation, bounded delayed retries, explicit
reindex fencing and stale-worker rejection. The new table has RLS enabled and no
browser grants. CI requires these tests with an explicit synthetic database URL.

This PR supplies queue storage and concurrency primitives. API registration and
runtime worker binding are still pending; it does not yet restore medical/generic
processing. No migration 012 has been applied to production yet.

The user confirmed the configured Cohere key is a free trial key on 2026-11-06.
Only Cohere is authorized for model inference. Published trial limits are 1,000
API calls per month, with endpoint-specific rate limits. No paid fallback is allowed.
Sensitive-data eligibility must be enforced independently of free-tier billing.

### Current deployment and provider checkpoint

- Security follow-up [#31](https://github.com/karanagg166/search-sphere/pull/31)
  merged at `8288c9d0062fdf3de4b451b8e7b811c310027c36`; pinned Render deployment
  `dep-dbmel3jl550s738ujl00` and `/health` confirm this exact revision. Migration 011
  is applied. Backend table HEAD probes now return 404 through the empty Data API
  surface; Auth and private Storage continue to operate.
- Available 23-hour Data API metadata logs show HEAD requests, including successful
  probes before containment and denial afterward. No GET/POST record retrieval was
  observed in that window. This does not establish absence of historical access
  outside the available window or prove who made requests. No contents were fetched.
- Quick-Clinic #160 merged at `4ac08783d3366ae575cb1121e2bb450b2e15075b`;
  [CI 42102041583](https://github.com/karanagg166/Quick-Clinic/actions/runs/42102041583)
  passed, and Vercel production deployment `dpl_AacmgdgsJqRQB85HRiRVxLxJRZtQ`
  reports that SHA and READY.
- ExamArena #29 merged at `1f698d7f89a6a9c9d16f4b86996d14cfe10a0716`;
  [CI 42102046560](https://github.com/karanagg166/ExamArena/actions/runs/42102046560)
  passed, and Vercel production deployment `dpl_AunqTLgEgDBLP5fkw6A1tV4b12dC`
  reports that SHA and READY. Live `/api/health` confirms the revision.
- Queue foundation [#32](https://github.com/karanagg166/search-sphere/pull/32)
  merged at `c140ea2d7b9c96fc16af48ba1a3dd34f654309a0`; PR
  [CI 42106105000](https://github.com/karanagg166/search-sphere/actions/runs/42106105000)
  passed. API/worker binding and production migration 012 remain pending.
- Standalone smoke [42107881625](https://github.com/karanagg166/search-sphere/actions/runs/42107881625)
  confirmed revision 8288c9d, then failed the initial memory guard at 429.3 MiB.
  The hosted runtime/image repair is required; the guard has not been weakened.
- Cohere-only public synthetic embedding, rerank, generation and vision checks passed.
  [Provider evidence and limits](COHERE_INFERENCE.md) record timings, one recovered
  timeout, trial budget enforcement and the independent sensitive-data restriction.
- All three required authenticated synthetic release journeys, physical cleanup,
  hosted-runtime capacity and full free-tier rollout verification remain OPEN.

### Atomic pgvector replacement

`SupabaseVectorStore.index_document` now deletes old vectors and inserts replacements
in the same PostgreSQL transaction. Concurrent deliveries serialize per scoped
identity. An optional caller session permits the runtime worker to fence publication
with its queue lease and commit vectors/content/status together. Empty replacements
and client/tenant scopes use the same transaction. SQL error parameters are hidden
and the indexing boundary emits safe error types rather than document contents.

Real PostgreSQL regressions verify failed insertion preserves old evidence, readers
see old vectors until commit, rollback preserves old vectors, eight duplicate writers
produce one index, colliding native/client/tenant identities stay isolated, incomplete
scopes are denied, and job completion rolls back with vectors. This is an atomic
replacement primitive; immutable source versions and a bounded model cutover remain open.

### Hosted runtime conversion (implementation verification in progress)

All default API embedding, semantic-boundary, rerank, OCR, caption, rewrite and
generation paths now use the guarded Cohere boundary. Deterministic lexical
preprocessing remains in service; PostgreSQL `tsvector`/`ts_rank_cd` and RRF own
lexical ranking. The old model-name reranker aliases resolve to the pinned hosted
version. Test-only injected models remain supported. No automatic local fallback
is present. Provider denials/quotas propagate rather than becoming successful RRF
fallbacks or silently omitted OCR; JSON endpoints return safe 503 error codes.

The production Docker image uses a separate lean requirements file, contains no
Torch, Transformers, FastEmbed, Tesseract or Docling, and copies only runtime source
and migrations. An offline Docker import/package/weight/health gate is required in
CI. `/diag` is metadata plus SQL connectivity only and never uses model quota.
Authenticated journeys remain responsible for proving actual provider inference.

Executed local Docker checks: clean production image build; API import without
local inference packages; migrations through 013 on an isolated PostgreSQL/pgvector
database; container capped at 512 MiB served health/readiness/diagnostics 200 with
129.7 MiB reported memory. This is a local cold-boot observation, not a production
capacity/P95 measurement. Production still serves 8288c9d at this checkpoint.
Trusted classification binding, durable worker binding, index version filtering
and live journeys remain pending; do not deploy the model migration alone.

The hosted runtime [#35](https://github.com/karanagg166/search-sphere/pull/35)
merged at `c54a03335cac0c10d6cce9abe11f47febf4565f6` after all four CI jobs passed
([42207588839](https://github.com/karanagg166/search-sphere/actions/runs/42207588839)).
This revision has not been deployed. The separate runtime image gate passed offline.

### Legacy credential scope containment

Inspection found that the Quick-Clinic legacy secret received `*`, including
`clients:manage`, in `security/service_auth.py`. It could therefore invoke platform
client administration despite being intended for medical compatibility. The adapter
now grants only document operations, collection management, search and answers in
`quick_clinic_default`. An existing registered client can narrow or revoke these
permissions; it cannot expand the legacy credential into platform administration.
Future client registration/rotation/revocation requires an independent server-only
`clients:manage` credential. Existing medical route names and key format are preserved.

Four negative API tests deny client list/register/rotation/revocation with the
medical key. Three registered-profile tests cover revocation, tenant denial and
scope narrowing. Client lifecycle/isolation tests now bootstrap a dedicated synthetic
platform administrator instead of relying on the old wildcard. Fourteen focused
service-client/integration tests passed against isolated PostgreSQL.

The legacy scope/bootstrap repair [#36](https://github.com/karanagg166/search-sphere/pull/36)
merged at `bdfef5edee7e64e57f96fc9dc46fdd80d5ce4569` after all four jobs passed
([42208663460](https://github.com/karanagg166/search-sphere/actions/runs/42208663460)).
It has not been deployed independently of the pending runtime repair.

### PostgreSQL data eligibility foundation

Migration 014 and `inference/data_policy.py` make classification approvals server-owned
and scope-aware. Missing, expired or revoked approval fails closed; a specific denial
cannot fall through to a broader grant. Conflicting grants use the more restrictive
classification. No client metadata or API operation can approve itself. Production
model calls require an authorization validator and recheck it before transmission.
The gateway also treats permanent 4xx provider rejection as nonretryable.

Two real PostgreSQL policy tests and synthetic gateway denial/revalidation tests are
required in CI. The durable worker/source binding is a separate following PR. This
foundation is not a claim that all conversation/resource paths are wired yet.

The PostgreSQL policy foundation [#37](https://github.com/karanagg166/search-sphere/pull/37)
merged at `95d93b184f37109f0e18c60a0cb14bd447f70bfe` after all four jobs passed
([42212181916](https://github.com/karanagg166/search-sphere/actions/runs/42212181916)).
Production migration 014 and deployment remain pending.

### Unified PostgreSQL ingestion binding

`services/ingestion_worker.py` now processes standalone, generic and medical records
through one PostgreSQL queue. Registration and outbox creation commit together;
duplicate identity registration preserves the existing job/state, while explicit
reindex creates a new generation and invalidates the prior lease. Medical compatibility
routes accept `forceReindex`; `/api/v1` accepts `reindex`. New standalone rows use
`queued`, which prevents pre-upgrade workers from claiming them during handoff.

The worker uses one processing slot, heartbeats, lease fencing, delayed bounded
retries and fixed terminal codes. It resolves classification/client authorization
from PostgreSQL before downloading content and rechecks before model requests and
publication. Vector chunks, raw extracted content, exact structured medical
observations, chunk counts and completion status publish in one transaction.
Failed reindexing preserves the previous working index. Changed indexed content is
temporarily rejected with 409 until immutable document version replacement is added;
the pending version feature is not represented as complete.

Owned status/list responses include job state, stage, attempts, generation, queue
age, timestamps and safe failure codes. Legacy pending adoption is bounded and
preserves live leases and original registration age. The native sync route is now
bounded to the authenticated owner and no longer runs a global missing-vector scan.
API RabbitMQ producers and their dependency are removed. Worker startup cannot
silently fail while the API reports healthy. Health/diagnostics do not run models.

Executed local verification: 278 API tests passed, one existing live-service test
explicitly skipped; 33 real PostgreSQL/pgvector release tests passed. They include
actual HTTP generic/medical upload and registration, shared processing/publication,
structured clinical values, duplicate/concurrent registration, restart recovery,
revocation before publication, live legacy lease preservation, old queue age,
failed reindex preservation and the 100-page PDF guard before model calls. Provider
HTTP in these integration tests is synthetic; live provider capability checks are
recorded separately. The rebuilt production image passed its offline package,
weight and health checks.

The initial 350-target/400-maximum coarse token setting is provisional. Hosted-token
limit verification and the requested 500/700/60 semantic/layout benchmark remain
open. Full query-context binding, model-version filtering/cutover, strict physical
deletion verification and all three authenticated live release journeys remain open.
Do not mark the rollout complete or deploy an incomplete acceptance chain.
