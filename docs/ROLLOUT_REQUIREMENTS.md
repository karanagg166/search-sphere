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

### Release gate repair (PR follows ingestion runtime #38)

The standalone post-deployment gate is dispatched from trusted `main` after deployment,
with a required exact SHA. It no longer starts on pre-deployment CI completion or
accepts an older backend revision. The `search-sphere-release` GitHub environment
allows only the `main` branch. Missing synthetic credentials/signing key fail the job;
search, grounded answer, SSE completion, and cleanup are mandatory.

`scripts/ci_production_smoke.py` now uses `offset`, rejects partial/error/repeated
pages, fails deletion verification on 503, and requires an owner-scoped physical
cleanup receipt from `GET /documents/{id}/cleanup`. That backend receipt is a
remaining dependency: this stricter gate must fail until it is implemented and
serving. Search denial alone is never reported as physical vector cleanup.

`scripts/smoke_fixtures.py` signs fixture filenames with an environment secret and
binds signatures to the authenticated owner and unique run ID. An interrupted run
can be recovered from the owner's listing even before its manifest write. Filename
prefixes or an unsigned local manifest do not authorize cleanup. Receipts omit
credentials and account email; corrupt/unwritable manifests fail. Offline regressions
cover pagination, interrupted runs, owner mismatch/forgery, provider outages during
cleanup, physical cleanup receipts, unique-marker answer/citation support, and SSE.
The three-app live release remains **open**; this is not a successful production run.

### Durable deletion foundation

Migration 015 and `services/document_deletion.py` revoke live metadata, chunks,
structured observations, and ingestion publication atomically. `cleanup_worker.py`
retains immutable cleanup targets, leases attempts, verifies private object absence,
and retries failures after restart. Owner/delegation-scoped `/cleanup` endpoints
provide a fresh physical vector count and verified object deletion. Native,
`/api/v1`, and medical compatibility deletes share this path. Deleted identities
cannot be resurrected by delayed registration or upload; registered bytes cannot be
overwritten before versioned replacement is implemented. Detailed contracts and the
preexisting signed-URL expiry limitation are in `docs/DOCUMENT_DELETION.md`.

Real PostgreSQL regressions cover deletion/publication fencing, atomic rollback,
client/tenant collisions, receipt authorization, delayed delivery, concurrent claims,
storage failures, and restart recovery. This foundation is not deployed yet and does
not complete the pending versioned replacement, old-citation authorization, or live
three-app verification requirements.

### Native PDF layout and evidence provenance foundation

`processing/extraction/reading_order.py` separates whitespace-defined columns and
wide header/footer bands, including disconnected columns PyMuPDF placed in a single
text block. `DocumentExtractor` retains the original PDF block/line indices, stable
source hash/block ID, page number, unrotated bounding box, page dimensions/rotation,
and extraction method. The inferred reading order is separate from original order.
Ambiguous overlapping layouts retain deterministic visual order; complex hosted
layout recognition is still pending.

Provenance survives cleaning, prose/image chunking, dense embedding, and the existing
`document_chunks.metadata_json` column without adding a table. OCR uncertainty is
flagged for review; captions remain descriptions. Image cleaning preserves embedded
`[unreadable]` markers and subsequent numeric values. Native, medical, and generic
uploads now read at most their configured size limit plus one byte before validation.

Native layout, mixed image OCR/caption, identifier preservation, and full provenance
propagation have synthetic regressions. A real hosted public-synthetic probe of
Cohere `/v1/tokenize` with `embed-english-light-v3.0` returned HTTP 200 (14 tokens).
This confirms availability of the selected embedding tokenizer; the exact-limit
chunking benchmark is the next implementation slice. Structured table/chart parsing,
private crops, table row-group storage, cross-page continuation, final token tuning,
versioned cutover, and live verification remain open.

### Hosted token-limit verification and initial tuning

`CohereGateway.token_count`, `processing/chunking/hosted_token_limits.py` and the
shared ingestion pipeline verify final chunks with the pinned embedding model's
hosted tokenizer. Evidence is split with source links intact; no silent embedding
truncation is allowed. Unicode hard splits preserve complete characters. Coarse
vocabulary is baked into the image and tested without network access. Token counts
are cached only within the authorized job/request, reauthorize on cache hits, and
use the same trial eligibility and PostgreSQL budget. Background admission waits
are bounded and do not repeat provider failures or quota responses.

The public synthetic benchmark compared 500/700/60, 350/400/60, and 400/480/60 at
cosine distance 0.5 / percentile 80. All scored 4/4 dense marker Recall@1 on the small
policy/class fixture. The 700-token stress chunk exceeded the real 512 limit; fitting
split it safely. 400/480 used three stress chunks versus four for 350/400, with no
observed recall loss. Initial settings are 400/480/60 plus mandatory exact hosted
verification. Results and limits are recorded in `docs/HOSTED_CHUNK_BENCHMARK.md`.
This does not complete the broader hybrid/rerank, citation, answer, refusal, P95,
capacity, or production cold-start evaluation requirements.

### Native structured tables

`native_tables.py` uses deterministic PyMuPDF grid detection (`lines_strict`,
`use_layout=False`) and accepts only consistent rectangular tables with complete,
unique headers. It preserves cells verbatim, captions, units, row/page references,
coordinates, and a stable table identity. Structured table/header/caption lines are
excluded from prose extraction. Cross-page continuations merge only with adjacent
pages, matching headers and normalized columns, matching captions, and bottom/top
margin alignment. Merged/missing cells remain for hosted layout recognition.

`structured_chunks.py` keeps small tables in one chunk and splits large tables at
row boundaries using exact hosted token counts, repeats headers, and records stable
table IDs and row intervals. An oversized single row fails for review instead of
silently dropping cells. Prose retains semantic chunking independently.

Migration 016 adds RLS-protected `document_evidence` for full structured tables,
separate from row-group retrieval chunks. Publication/reindexing replaces evidence
and vectors in the same ingestion transaction; deletion removes both. Native and
`/api/v1` full-evidence routes authorize the live document before returning a table.
Unit fixtures cover grid extraction, units/identifiers, duplicate exclusion, row
splitting, and continuation rejection. Real PostgreSQL checks cover table/chunk
links, idempotent reindex identity, and complete deletion. Hosted scanned/complex
tables, chart extraction, private crop persistence, and versioned cutover remain open.

### Hosted layout, charts, and durable private source crops

The canonical worker now routes image/scanned regions and vector graphics clusters
through `HostedLayoutRecognizer`, while retaining native prose/tables. Strict
validation rejects overlapping regions, malformed tables, invalid geometry, unknown
routes/keys, and model claims of value verification. Text/image/table/chart blocks
retain source provenance. Captions stay generated descriptions; chart values retain
readability/approximation flags and server-owned `verified=false`. Unreviewed OCR,
images/captions, and charts cannot enter exact structured clinical observations.
Tables split at row boundaries; large charts split at point boundaries with stable
IDs and repeated context. Continuations require explicit matching column geometry.

`derived_assets.py` records private crop cleanup intents before object upload and
publishes references under the ingestion fence. Cleanup recovers orphaned crops,
preserves live staged work, and removes obsolete crops after successful reindex.
Document cleanup receipts include `derived_objects_remaining`; the live gate waits
only on valid pending receipts and still fails on 503 or physical vectors remaining.
Authorized crop routes issue short-lived private signed URLs after live document
scope checks. No public bucket, local model weights, or paid service is introduced.

Real public synthetic hosted probes passed table extraction (3 rows/values, 3.90s)
and labeled chart extraction (2 values, 5.34s), with review flags preserved. Tests
cover schema/injection rejection, numeric uncertainty, clinical-path exclusion,
atomic crop publication, orphan restart recovery, live leases, reindex and deletion.
Detailed behavior and limits are in `docs/HOSTED_LAYOUT_EVIDENCE.md`. Exact chart
comparisons require reviewed values and remain unavailable; broader model accuracy,
full query guardrails, versioned cutover, and production journeys remain open.

### Query provider and candidate authorization foundation

Native, versioned, conversation-answer, and medical query routes now establish a
request-scoped inference context from authenticated identity and PostgreSQL data
policy. Native conversation ownership is checked before starting SSE. Fresh sessions
recheck user/client activity, key rotation, allowed tenant/operation, selected resource
ownership, and policy before and after every hosted request.

Dense/lexical SQL requires the pinned Cohere embedding version and current authorized
PostgreSQL document metadata. MiniLM vectors are excluded despite their matching 384
dimensions. Before reranking, candidate IDs are re-fetched under the same scope;
content/metadata are rebuilt from authoritative rows and unknown/unauthorized points
are dropped. Active evidence hashes and permissions are revalidated for later calls,
so deletion, replacement, revocation, or classification changes cannot reuse stale
provider context. Policy resolution is batched across candidate scopes. Retrieval is
bounded to 32 candidates and at most 20 final results; the default remains five.

Provider failures keep their explicit timeout/quota/approval codes through application
layers. Query/rewrite/generation/retrieval logs record lengths and error types instead
of query text, model output, or exception contents. Real PostgreSQL regressions cover
old-model/native-external collisions, malicious candidate text, current collection
scope, document/policy/client revocation, and revocation while inference is running.

This is the authorization foundation. Selective authorized history, validated intent
routing, citation support checks before all SSE output, compact memory, caches,
versioned replacement, and all live journeys remain open. No new backend rollout is
claimed by these merged foundations.

Recent merged implementation/CI evidence (backend production remains 8288c9d until
pinned rollout and live verification):

| PR | Merge revision | Passing PR workflow |
|---|---|---|
| [#38 ingestion runtime](https://github.com/karanagg166/search-sphere/pull/38) | a0720e2ed54fb9bb23e492b34bb460ec5893392e | [42215194099](https://github.com/karanagg166/search-sphere/actions/runs/42215194099) |
| [#39 release smoke](https://github.com/karanagg166/search-sphere/pull/39) | 4f1cfb9fa299898ce0570d282db4dd3cc910898a | [42220757632](https://github.com/karanagg166/search-sphere/actions/runs/42220757632) |
| [#40 durable deletion](https://github.com/karanagg166/search-sphere/pull/40) | 420f041cf2ba310e6761a09c184d03d8672c8a49 | [42221838170](https://github.com/karanagg166/search-sphere/actions/runs/42221838170) |
| [#41 layout provenance](https://github.com/karanagg166/search-sphere/pull/41) | 93e89b0ba5b5d24cb31a298eef944617ee2f29f8 | [42223484421](https://github.com/karanagg166/search-sphere/actions/runs/42223484421) |
| [#42 hosted token limits](https://github.com/karanagg166/search-sphere/pull/42) | 77fba3f5d067559457382066badc444230339f27 | [42226394219](https://github.com/karanagg166/search-sphere/actions/runs/42226394219) |
| [#43 native tables](https://github.com/karanagg166/search-sphere/pull/43) | 8e28313859071f0192157057fe3072a01e186141 | [42227369092](https://github.com/karanagg166/search-sphere/actions/runs/42227369092) |
| [#44 hosted layout/crops](https://github.com/karanagg166/search-sphere/pull/44) | 7babae2cad6dc67c26067a0d3d2ba6845ad71c0d | [42229574288](https://github.com/karanagg166/search-sphere/actions/runs/42229574288) |

### Selective native conversation history

`services/conversation_context.py` classifies `requires_history` independently of
intent. Greetings and standalone questions perform no history query. Follow-ups
select one bounded, topic/recency/message-reference turn pair from the authorized
PostgreSQL conversation, including older topic questions outside the recent window.
Greetings/acknowledgments are skipped. Exact previous-answer requests load that
response without truncation or substitution. Missing, oversized, stale, or revoked
references produce clarification. Prior source text must match a current authorized
chunk; selected message and source hashes are rechecked around subsequent inference.

Native search/answer/conversation JSON and SSE expose history selection metadata.
The compatibility `conversation_context` field is ignored by native query routes;
only server-selected history is transmitted. Rewriting conservatively preserves
names, dates, numbers, identifiers and negation, rejects invented numbers, and asks
for clarification when a follow-up cannot be rewritten. No generated rewrite reasons
or exception contents are logged. Real PostgreSQL tests cover the maternity-policy
“What about interns?” regression, greetings, old-topic selection, exact response and
message references, cross-user denial, evidence replacement, and context mutation.
HTTP tests seed authoritative history and prove client instructions are discarded.

Delegated conversation persistence, complete intent routing, exact-prior-response
answer execution, full citation support/SSE validation, compact memory, versioned
provenance, and caching remain open. This native foundation is not a completed query
pipeline or production rollout.

Quick-Clinic [PR #162](https://github.com/karanagg166/Quick-Clinic/pull/162) is merged
at `98e35720e14cb5f0654cdae9d6bab78177a76e85`; live `/api/health` reports that exact
SHA. Its trusted post-deployment deployment-contract workflow
[42234017364](https://github.com/karanagg166/Quick-Clinic/actions/runs/42234017364)
passed. This verifies deployment contracts only; the mandatory authenticated Search
Sphere patient journey remains open. ExamArena PR #30's abbreviated-SHA test fixture
was corrected after CI rejected it; full CI/review/deployment verification continues.

### Delegated conversation identity foundation

Migration 017 reuses existing `conversations` and `messages`. A conversation has
one native user owner or a delegated client/tenant/actor identity, with collection,
subject, and external reference. A check constraint prevents mixed identities;
scoped uniqueness and a PostgreSQL advisory lock make concurrent registration
idempotent. Downgrade refuses to erase delegated conversations. The migration keeps
existing native ownership and works in the SQLite migration regression as well as
real PostgreSQL; backend table grants/RLS remain restricted.

`X-Actor-ID` is independent from the evidence subject. Versioned conversation
registration/read/delete and offset-paginated messages enforce all five delegated
identity/resource dimensions. Answer/search conversation IDs are authorized before
JSON/SSE processing. The shared history selector and fresh inference revalidation
accept the same delegated scope. A completed-turn helper commits messages together;
old delegated answers are withheld when source access/version changes.

Six real PostgreSQL regressions cover concurrent idempotency, actor/patient/tenant/
collection/client isolation, missing actors, atomic turns/rollback, source and client
revocation, cascade deletion, and the authenticated HTTP contract. Turn persistence,
context/routing integration in all external answer routes, Quick-Clinic actor/thread
forwarding, compact memory, and complete query guardrails remain the next phase.

[Search Sphere #46](https://github.com/karanagg166/search-sphere/pull/46) merged at
`468421bae23136b4a4db464298194be13ce7aef4`, with all required jobs passing in
[42234508762](https://github.com/karanagg166/search-sphere/actions/runs/42234508762).
[ExamArena #30](https://github.com/karanagg166/ExamArena/pull/30) merged at
`84ede3356aa149080aba71caa04f09ffba5aaf16`; live API health reports exactly that SHA.
Its post-deployment contract run
[42235359854](https://github.com/karanagg166/ExamArena/actions/runs/42235359854) passed.
Neither consumer contract run substitutes for the required authenticated Search
Sphere journey. Backend production remains `8288c9d`; full rollout remains open.

### Versioned answer context and completion wiring

`GenericRagService` now shares selective authorized history preparation with native
query paths. Client `conversation_history` and `system_prompt` compatibility fields
are ignored; grounding remains server-owned. Completed versioned turns and their
source references commit together to PostgreSQL. Follow-ups expose history selection
and rewrite metadata; unresolved references clarify before retrieval. Native and
versioned exact previous-answer requests load that response and refetch its current
sources in original citation order without an embedding/reranking search.

Versioned SSE buffers generation, checks current access, and only then emits metadata,
answer, citations and a completion event with the saved message ID. Provider failures
emit a safe terminal error and never tokens, success completion, a retry, or a saved
answer. Source revocation after generation prevents persistence and return. Full
claim-to-source support validation and the other streaming routes remain open.

The shared gateway distinguishes provider timeout from network unavailability, quota,
and approval failure. Rewrite provider/format/key failures remain explicit instead
of appearing as clarification. The default rewrite deadline is 20 seconds, bounded
by the existing 15-second HTTP/20-second admission deadline. A single actual Cohere
trial-key probe using only public synthetic maternity-policy history passed the
“What about interns?” standalone rewrite in 5.93 seconds; this is one observed call,
not a P95 or capacity estimate. No sensitive source content was sent.

[Search Sphere #47](https://github.com/karanagg166/search-sphere/pull/47) merged at
`2a57f6c9286aef06827011f15bab84376a1040d3`; all required jobs passed in
[42236051089](https://github.com/karanagg166/search-sphere/actions/runs/42236051089).
The clinical compatibility history/actor forwarding, validated complete intent router,
citation support checks, memory, caching, versions, index cutover and authenticated
live journeys remain open. Backend production is still `8288c9d`.

### Medical compatibility history and current structured evidence

The existing medical routes are preserved. When Quick-Clinic supplies a conversation
reference, the server maps it idempotently under the authenticated doctor actor,
patient, collection, tenant and client. Raw request history is ignored. The same
PostgreSQL selector, exact response behavior and rewrite validation apply. Clinical
short temporal refinements (for example “Only the last 3 days.”) reuse the authorized
clinical concept through deterministic routing without hosted inference. Completed
medical turns retain chunk/observation IDs and fingerprints, not source passages.

Structured observations now join currently READY, authorized external documents and
include only deterministic REGEX evidence. Orphans, other patients, failed documents,
and unreviewed OCR values cannot supply exact clinical readings. Observation snapshots
are rechecked around later provider calls and response completion; document revocation
or value changes invalidate their history. Zero-valued observations are preserved.
Medical SSE completes the shared JSON answer path and authorization before any token;
upstream failure produces a safe HTTP error before a stream starts. This buffering is
not yet full claim-to-evidence support validation; that remains open for every route.

Seven real PostgreSQL regressions verify actor/patient isolation, ignored caller
history, deterministic temporal follow-up, source/observation revocation, orphan/OCR
exclusion, no saved turn on provider failure, and identical token/citation/completion
payloads with a persisted message ID. The API suite passes with its existing isolated
live-services skip. Quick-Clinic forwarding is a coordinated consumer PR; production
backend migration/deployment and all authenticated live journeys remain open.

[Search Sphere #48](https://github.com/karanagg166/search-sphere/pull/48) merged at
`20588e6e4ec5042c1fddc76f8ab33ea020b96ade`; required CI passed in
[42238038361](https://github.com/karanagg166/search-sphere/actions/runs/42238038361).
Vercel health/Actions deployment verification remains available. Its local CLI
management credential returns HTTP 403 even for `/v2/user`; billing-plan verification
is pending reconnection or the owner's dashboard confirmation. No paid resource was
created or upgraded. This limitation does not block local code/CI work.

### Shared validated query routing

Native search/answers/conversations, versioned search/answers, and medical answers/
chat now classify context dependence, select authorized history and rewrite before
intent execution. `QueryRoute` is validated across DIRECT, RAG, GENERAL_LLM, TOOL_API
and CLARIFICATION. Deterministic greetings/thanks avoid inference; ambiguous references
and unregistered actions clarify. RAG remains the default document capability.

Migration 018 adds one missing server policy table scoped by client/tenant, with RLS.
General generation and optional hosted semantic classification default off. Strict
hosted routing rejects unknown enums/fields and nonfinite confidence; confidence below
0.75 clarifies. General generation requires the explicit policy and no history, uses
no private retrieval, and rejects invented document citations. The only registered
tool is read-only `document.status`; PostgreSQL checks resource scopes and credentials
before execution. No generic URL/SQL/side-effect tool is registered. Policy changes
are rechecked during inference, including changes while a result is in flight.

Thirteen real PostgreSQL routing tests cover defaults, confidence/enum validation,
general-policy gates, no private context, policy revocation during generation,
registered-tool/owner checks, delegated patient/collection boundaries/read-scope
revocation, and DIRECT through native/versioned/medical APIs without retrieval.
The API suite and focused clinical/migration regressions pass. Complete claim support
validation, all streaming guards, compact memory, versioned replacement/cutover,
caches, labeled performance evaluation and the three full authenticated production
journeys remain open.

[Search Sphere #49](https://github.com/karanagg166/search-sphere/pull/49) merged at
`f50664620c3a05b8727386721f76647b1d030fbd`; required CI passed in
[42252964139](https://github.com/karanagg166/search-sphere/actions/runs/42252964139).
[Quick-Clinic #163](https://github.com/karanagg166/Quick-Clinic/pull/163) passed
[42253283898](https://github.com/karanagg166/Quick-Clinic/actions/runs/42253283898)
and merged. Exact new consumer deployment verification continues; the backend remains
on the last verified production `8288c9d` until the pinned rollout gates run.

### Native response buffering and atomic turns

Native answer/conversation SSE now buffers one bounded provider result before any
source or answer frame. Empty streams and provider failures cannot trigger another
generation attempt. Adapter compatibility is decided from the method signature before
inference, avoiding retries when an in-flight provider raises TypeError. Completed
native conversation question/answer pairs commit atomically after validation; failed
turns do not leave a purported assistant response or a dangling question. JSON and SSE
share that completion/persistence path.

`security/response_stream.py` rechecks current authorization before every content and
completion frame across native, versioned, and medical SSE. Revocation after metadata
prevents later source/token output. Error frames contain safe codes, never provider
exception text; general request exception logs likewise record types instead of raw
diagnostics. Tokens concatenate exactly to the completion answer. Full claim-to-source
validation remains a following phase; buffering alone is not an entailment guarantee.

Five real PostgreSQL native-stream cases cover partial-provider failure, empty-result
no-retry behavior, mid-delivery source revocation, and atomic successful conversation
completion. The combined native/versioned/medical stream/context suite passed 17
PostgreSQL tests; 27 gateway/runtime checks and 30 focused API checks also passed.
The full API suite passed before the final two no-retry regressions were added.

Quick-Clinic #163 merged at `4ba7f6f61dfa6ded1b3738dff12b762bff6298cc`, and live web
health now reports exactly that revision. Its trusted contract workflow
[42254878720](https://github.com/karanagg166/Quick-Clinic/actions/runs/42254878720)
was dispatched after deployment. Full Search Sphere patient journeys remain open.

Shared query routing PR #50 merged at `b9d53d01da31a53c6cdb6364f7c3cbdcf0b80c6d`.
All required checks passed in [42254517270](https://github.com/karanagg166/search-sphere/actions/runs/42254517270),
including the legacy worker job after its package-install delay recovered. Native
stream PR #51 now targets main; its required CI is run on the updated branch.
Quick-Clinic contract run [42254878720](https://github.com/karanagg166/Quick-Clinic/actions/runs/42254878720) passed
against exact deployed `4ba7f6f61dfa6ded1b3738dff12b762bff6298cc`.

### Code-rendered grounded evidence and response screening

Hosted grounded generation now selects IDs of complete source sentences/paragraphs
or structured row/chart units. The model cannot supply answer prose or citation IDs.
The service validates a strict selection schema, rejects unknown/duplicate units and
contradictory decisions, then renders original complete text and code-constructed
source citations. Negation, numbers, identifiers, headings and table headers cannot
be silently removed by selecting a fragment. Clinical advice requests get a predefined
record-retrieval limitation; image/chart evidence remains visibly unverified and no
exact chart comparison is generated. This conservative extractive behavior sacrifices
free paraphrasing to give deterministic claim-to-source support. Model relevance and
refusal quality still require broader labeled evaluation.

Known configured credentials and credential-shaped strings are screened before a
provider request, before reranking authoritative candidates, and in provider output.
Source instructions are excluded from selection units. Invalid model output fails
before JSON/SSE content instead of deleting a citation while retaining an unsupported
claim. Native historical message reads now re-fetch/recheck source evidence and
withhold revoked answers/passages; query metadata is withheld with an unavailable
answer. The separate messages/memory/version/caching work remains open.

Seven real PostgreSQL guardrail journeys use actual authorization and synthetic model
HTTP: marker/page support, fabricated JSON/SSE claims, source-secret rejection before
inference, prompt instructions, revocation during generation, and revocation when old
messages are read. API tests cover whole-unit negation/identifier preservation,
structured headers/review flags, clinical limits and malformed selections. The full
API suite passed (existing isolated-services skip); 29 gateway/runtime tests passed.

Three actual Cohere trial-key public/synthetic selection probes passed: unique-marker
answer/citation (1.86s), missing-clinical-evidence behavior (0.84s), and prompt injection
(1.01s). These are individual observations, not P95 or accuracy/capacity claims. No
medical/company record or credential was included in the provider fixtures.

[Search Sphere #51](https://github.com/karanagg166/search-sphere/pull/51) merged at
`4c1d97dadf0c5bf5fc53c8cae6188ac649ee23d2`; required CI passed in
[42256311065](https://github.com/karanagg166/search-sphere/actions/runs/42256311065).
The backend production revision remains `8288c9d` pending migrations, versioned index
cutover, protected release deployment and all three required authenticated journeys.

### Bounded permission-aware compact memory

Migration 019 adds `conversation_memory`, separate from messages and owned through
the existing conversation foreign key. Existing native/delegated identity checks
therefore also isolate memory by user or client/tenant/actor/collection/subject.
Completed turns update messages and memory in the same transaction while holding
the conversation row lock. Memory keeps eight distinct topic turns, eight recent
intents, bounded answer keywords, message IDs, and compact source fingerprints.
It contains no copied source passages and is capped at 32 KiB per conversation.
Document version IDs are retained when available; full versioned provenance is the
following document-version phase, not claimed by this memory foundation.

Memory supplies IDs only. The selector fetches those messages inside the same
conversation and reauthorizes their evidence before history enters rewriting or
answer selection. Useful topics survive intervening greetings. Deleted/changed
sources cannot be revived from memory. Conversation deletion cascades memory.

Six real PostgreSQL cases cover compact references/provenance without source text,
transaction rollback, topics outside the 64-message window, revocation, native and
delegated actor isolation, bounds/cascade deletion, and concurrent updates without
lost turns. The combined authorization/history/stream/guardrail/memory suite passed
37 PostgreSQL tests, and the full API suite passed with its existing isolated-services
skip. No model inference is needed for compact memory maintenance.

[Search Sphere #52](https://github.com/karanagg166/search-sphere/pull/52) merged at
`0863f555e03b0a2cd78301c3eb8d78921eefe2e1`; all required CI passed in
[42257904520](https://github.com/karanagg166/search-sphere/actions/runs/42257904520).
Production remains on backend `8288c9d`. Caching, document versions/cutover,
legacy-worker retirement, broader evaluation and all three live journeys remain open.
The Vercel management credential still returns 403; the browser dashboard opens the
Vercel login page, so billing-tier verification remains pending owner confirmation
or reconnection. No paid resource or upgrade was requested or created.

### Canonical worker image and deterministic regression gate

The independent worker image now uses the same API source, hosted dependencies,
baked deterministic tokenizer vocabulary and PostgreSQL worker command. Its historical
RabbitMQ entry point no longer registers actors; the local-model requirement list is
replaced by the canonical manifest. Compose uses pgvector, starts API migrations before
the worker, disables the duplicate in-process worker for that local two-process setup,
and places Redis/RabbitMQ/Qdrant services under an optional legacy profile. Existing
free Render remains a single API/in-process-worker service; no paid worker is created.

A test configuration issue was caught before accepting the result: the old worker
pytest configuration can silently select its own legacy `src` package even with API
PYTHONPATH set. The new runner explicitly sets configuration, root, import mode and
path, and an assertion checks actual source module paths and forbidden packages.
Ninety-six preserved deterministic processing/hosted adapter/token-limit regressions
then passed against canonical source. Real PostgreSQL ingestion/claim/recovery/reindex/
cleanup gates remain separate and required. The old system OCR/Torch installation
job is replaced by this canonical gate and an offline worker-image build/check.

The independent worker image passed network-disabled import/tokenizer/health checks
under 512 MiB at 182.4 MiB peak RSS; its default command is the PostgreSQL worker CLI.
Compose configuration and diff/lint checks pass. This is not a throughput or live
cold-start capacity measurement. Broader evaluation, caching, document versions/index
cutover and all three authenticated production journeys remain open.

[Search Sphere #53](https://github.com/karanagg166/search-sphere/pull/53) merged at
`bf15e50850afc4750bb2d22a162ca9cbc4f97ea4`; required CI passed in
[42259891811](https://github.com/karanagg166/search-sphere/actions/runs/42259891811).

[Search Sphere #54](https://github.com/karanagg166/search-sphere/pull/54) merged at
`6278e3a50c429d409adeb1bb818ee7f078759540`; required CI passed in
[42262102153](https://github.com/karanagg166/search-sphere/actions/runs/42262102153)
and main [42262756951](https://github.com/karanagg166/search-sphere/actions/runs/42262756951).
The hard-isolated canonical worker runner passed all 96 tests; it forces a synthetic
SQLite database and test key even if invoked from a deployment-configured shell.

## Permission-aware retrieval reuse (implementation; production pending)

Migration 020 adds private, RLS-protected `retrieval_cache` references. The shared
`retrieval/reranked_retriever.py` checks `retrieval/permission_cache.py` before
embedding/reranking. Entries contain signed IDs, hashes and scores, no raw queries,
passages, answers or history. Keys bind client/tenant/actor/resource/conversation,
selected context, current authorized dataset manifest, classification, policy,
provider eligibility, query options and pinned models. Every hit refetches and
reauthorizes source text. Delegated requests without actors and scopes above 10,000
chunks skip caching. Publication/deletion invalidate the affected client/tenant in
their own transaction. TTL is 15 minutes; concurrent writes enforce 128 entries
globally, at most 16 KiB/20 references each. See `docs/RETRIEVAL_CACHE.md`.

Verified nine new real-PostgreSQL tests, plus the query-access, cleanup and ingestion
suites (31 passed together). They cover actual repeat-search call avoidance, actor/
conversation/context isolation, newly added evidence, changed bytes, user/client/
policy/trial revocation, expiry/tampering, deletion, rollback and concurrent bounds.
All API tests passed with the existing isolated-live-service skip; all-source Ruff
and diff checks passed. Synthetic adapters demonstrate skipped embedding/reranking
work, not production latency. Document-version replacement/cutover, full labeled
evaluation and the three authenticated production journeys remain open. No new
production migrations or Search Sphere runtime deployment occurred in this phase.

[Search Sphere #55](https://github.com/karanagg166/search-sphere/pull/55) merged at
`fb34007e3f8deaaa8cb80926d98c7009a8adf23e`; required CI passed in
[42263736443](https://github.com/karanagg166/search-sphere/actions/runs/42263736443).

## Published version identity and citation provenance (partial replacement phase)

Migration 021 adds private `document_versions` and active-version pointers to the
existing native/delegated document tables. `services/ingestion_pipeline.py` hashes
the actual downloaded original; `services/document_versions.py` records a successful
publication under the job fence. Catalog, vector version tags, active pointer,
cache invalidation and completed job commit atomically. Failed publication preserves
the old working version. Existing legacy rows remain explicitly unversioned until
successfully indexed; no historical byte hashes are fabricated.

`security/query_access.py` applies active-version predicates to dense, lexical and
candidate SQL, and compares evidence versions around provider calls. Native,
`/api/v1` and medical citations carry actual version IDs when available. Stored
message references and compact memory bind version, chunk and source-message IDs.
Native and versioned `GET /documents/{id}/versions` metadata listings authorize the
live parent, use offset pagination and omit private storage paths. Root deletion
removes version metadata with vector revocation before durable object cleanup.

Six new PostgreSQL/pgvector cases verify byte hashes, actual citation/memory links,
publication rollback, continued prior-index retrieval, monotonically numbered
successful reindexes, stale-citation refusal, stale-version exclusion in dense and
lexical retrieval, duplicate delivery, scoped metadata pagination and deletion.
The combined version/cache/ingestion/query/memory/stream/cleanup group passed 47
tests before the final citation-link case was added. Another 28 structured-evidence,
crop, medical/history and guardrail tests passed. The API suite passed with its
existing isolated-live-service skip; focused medical/structured API checks and
all-source Ruff passed. See `docs/DOCUMENT_VERSIONS.md` for the boundary of this phase.

Changed-file replacement uploads, staged-original cleanup, retained historical
evidence, bounded reindex/cutover, broader labeled evaluation and production journeys
remain open. Production is still on the older runtime; no production version
migration has been applied.

## Bounded provider timeout recovery

Reproduced a queue failure for `ProviderTimeoutError`: the server-owned failure
allowlist omitted `provider_timeout`, so `IngestionQueue.fail` raised `ValueError`
and left the job RUNNING until lease expiry. Added the timeout code to the existing
retry path. Two new real-PostgreSQL regressions cover fresh ingestion and reindexing:
each timeout releases the lease, backoff applies, attempt three becomes terminal
FAILED with the exact reason, and an existing working index remains searchable.
Both tests failed before the repair and passed after it; the complete queue/timeout
group passed 11 tests. The required PostgreSQL CI gate includes these regressions.

Schema-only, read-only connection checks succeeded for both consumers' configured
databases. No medical/student records or credential/hash fields were selected.
This establishes available setup access, not completed synthetic production journeys.

[Search Sphere #56](https://github.com/karanagg166/search-sphere/pull/56) merged at
`7865d09ea1967b4f9ebf9b2fd13a0df966bee0de`; required CI passed in
[42265470832](https://github.com/karanagg166/search-sphere/actions/runs/42265470832).
All six final version-provenance PostgreSQL cases passed. [#57](https://github.com/karanagg166/search-sphere/pull/57)
merged at `6c0cb34fb1d5e1fb448d1c1c60382d134821847a`, with required CI
[42265959786](https://github.com/karanagg166/search-sphere/actions/runs/42265959786) passing.

## Release controller and consumer evidence recheck

`release.yml` and `scripts/release_gate.py` separate exact-main CI admission/production
migrations, pinned Render deployment, and the standalone authenticated journey.
Admission checks the authorized existing free service and approved trial model/data
configuration before changes. Production SQL verification reads migration/grant/RLS/
bucket metadata only. Driver output is captured and withheld; secret/body values are
never logged. There is no new infrastructure or automatic plan upgrade. See
`docs/RELEASE_GATES.md`. Offline release-admission and smoke regressions exercise
missing configuration, skipped CI, wrong revision/resource/plan/provider/scope,
redacted failures and bounded HTTP reads. This does not yet establish live success.

Fresh Render management metadata confirms the Search Sphere service remains free,
in the authorized workspace, with automatic deployment disabled. Search Sphere is
a public GitHub repository. The protected release environment's branch policy is
still enabled. Vercel account-plan verification remains open after management API
403 and an unauthenticated dashboard session; the owner clarification is pending.

During seeded-journey preparation, ExamArena's answer mapper was found to drop a
cross-school citation while still returning its answer text. [ExamArena #31](https://github.com/karanagg166/ExamArena/pull/31)
rechecks all result/citation document identities against its fresh PostgreSQL catalog
(school, subject, class collection, READY state and requested document type), rejecting
the entire response on failure. It uses catalog metadata, validates citation numbers,
preserves chunk/version IDs and replaces uncited upstream claims with a deterministic
no-evidence response. Six regressions reproduced the original failures; 26 focused
unit and 17 local-PostgreSQL HTTP tests passed. Its review/CI/deployment is still open.

ExamArena #31 subsequently merged at `d96fe305a871d8560e89a0bd320cfcf742f51cd0`;
required backend/frontend/worker/full-stack checks passed in
[42268241921](https://github.com/karanagg166/ExamArena/actions/runs/42268241921).

The release controller is [Search Sphere #58](https://github.com/karanagg166/search-sphere/pull/58).
Its 71 offline release/smoke checks passed. A production preflight initially refused
the service because `STORAGE_BACKEND` was implicit in the image; configured explicit
Supabase storage and the already verified hosted-model/trial settings without
triggering deployment. Live Render preflight now passes on the existing free service.
The protected main-only environment now holds the required release SQL connection,
Render credential and approved trial fingerprint; values were never logged.

Added dedicated synthetic release identity provisioning after migrations and before
deployment. Only the fixed signed fixture account receives a tenant/user-specific
synthetic policy; changed or revoked ownership/policy is refused, and approval expires
after 30 days. Existing hashes and unrelated user/record contents are never selected.
Two real PostgreSQL identity cases plus the 21 release-admission cases passed (23
together). No new production migration or Search Sphere deployment has occurred yet.

## First hosted-runtime production release and open recovery

Search Sphere #58 merged at `b3138a042c6e30c7b954d9ade9611b6da9ad2355`.
Exact main CI [42303923953](https://github.com/karanagg166/search-sphere/actions/runs/42303923953)
passed. Release [42317818894](https://github.com/karanagg166/search-sphere/actions/runs/42317818894)
passed production migrations through 021, effective-grant/RLS/private-bucket checks,
signed synthetic account setup, and pinned deployment `dep-dbn6v2ec6qcc73crbe7g`.
The serving health revision was independently verified as the full SHA above.

The authenticated live journey measured 135.3 MiB initial memory, uploaded the public
synthetic PDF, observed indexed status with two chunks, retrieved its unique marker
and verified hosted reranking. This repairs the originally observed uploaded stall.
The journey failed before grounded-answer/SSE proof; cleanup failure masked its
original exception. Required jobs correctly reported failure. No rollout completion
is claimed. The signed fixture receipt was retained in the workflow artifact.

Scoped checks of that owned synthetic fixture found a cleanup receipt with
`object_cleanup_unverified`. Supabase's deleted-object endpoint returned HEAD HTTP
400, and a bounded range GET returned HTTP 400 with JSON `error=not_found` and
`statusCode=404`. The storage verifier only recognized HTTP 404, so it could not
confirm absence. A repair accepts this specific structured legacy missing-object
response after the authenticated bounded range GET; auth errors, 503s and unrelated
400s remain errors. Smoke cleanup now preserves and immediately reports a prior
journey failure instead of replacing it. Storage and smoke regressions passed (65
cases together). Grounded answers, SSE and verified cleanup remain open pending the
repair and rerun, alongside the two consumer journeys and remaining feature phases.

ExamArena's exact live revision is `d96fe305a871d8560e89a0bd320cfcf742f51cd0`;
deployment-contract run [42303541326](https://github.com/karanagg166/ExamArena/actions/runs/42303541326)
passed. Quick-Clinic remains at `4ba7f6f61dfa6ded1b3738dff12b762bff6298cc`.
Neither result is an authenticated consumer journey. Vercel management still returns
403 / `forbidden` / `Not authorized`; free-plan verification and management setup
through that credential remain open. Anonymous HEAD-only probes of users, service
clients and medical observations continue to return 404 without record contents.
