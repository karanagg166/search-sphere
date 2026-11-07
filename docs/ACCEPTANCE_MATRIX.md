# Search Sphere acceptance matrix

This is an interim acceptance record. The overall rollout is **open**. Code tests,
deployment-contract checks and authenticated production journeys are distinct proof.
The original pre-implementation inventory and detailed PR history remain in
[ROLLOUT_REQUIREMENTS.md](ROLLOUT_REQUIREMENTS.md).

| Requirement | Current result | Implementation and verification |
| --- | --- | --- |
| Close browser Data API exposure | Verified in production | Migrations 010/011; backend table grants/defaults/RLS, empty exposed schema and private Storage. Release verifies effective anon/authenticated privileges and RLS. Latest HEAD-only probes of users, service clients and observations return 404 without record bodies. |
| Historical access assessment | Limited evidence recorded | Available access-log window distinguishes confirmed pre-containment HEAD visibility from historical record retrieval; absence of observed retrieval in that window does not prove no earlier access. See audit evidence in the ledger. |
| Preserve backend SQL, authentication and private originals | Verified standalone production flow | SQL readiness, dedicated-account login, private upload/download during ingestion and verified private-object cleanup passed in the live journey. |
| One PostgreSQL ingestion path | Code verified; consumer live proof open | `services/ingestion_worker.py`, `repositories/ingestion_queue.py`, medical/generic registration. Atomic claims, leases, restart recovery, idempotency, fenced publication, bounded retry and explicit timeout reasons have real PostgreSQL coverage. Standalone production PDF reached indexed status. |
| Progress and queue age | Implemented | `services/ingestion_progress.py`, job metadata and lightweight health/diagnostics. Uploaded/indexed states were observed in the live journey; larger queue/capacity measurements remain open. |
| Consumer status/failure contracts | Code and deployment contracts verified | Quick-Clinic #160/#161/#163 and ExamArena #29/#30/#31; actual upstream FAILED/outage handling and scoped doctor context. Authenticated complete consumer journeys are still required. |
| Doctor/patient and school/class authorization | Partial acceptance | Consumer authorization plus Search Sphere client/tenant/collection/subject checks. ExamArena #31 rejects unverified catalog evidence before emitting answer text. Remaining permission review and seeded patient/school/class production negatives are open. |
| Pre-deployment PostgreSQL/pgvector gates | Verified | Required CI runs migrations and scope/idempotency/recovery/reindex/provider/consumer-contract tests. `release.yml` admits only successful exact-main CI and applies production migrations before deployment. |
| Three authenticated production journeys | **1 of 3 passed** | Search Sphere's fresh PDF passed standalone and persistent-conversation JSON answers, supporting page citations, SSE done, exact persisted turns and physical cleanup in [42367181169](https://github.com/karanagg166/search-sphere/actions/runs/42367181169). Quick-Clinic and ExamArena remain open; their contract checks do not satisfy this requirement. |
| Smoke pagination, failure handling and fixture provenance | Verified standalone | `scripts/ci_production_smoke.py`, `smoke_fixtures.py`; offset pagination, missing-credential failure, 503 rejection, signed ownership, physical counts and retained receipts. #59 preserves the original failure and verifies Supabase's structured legacy missing-object response. Two interrupted owned receipts subsequently reached COMPLETE. |
| Trusted releases and exact deployed revision | Verified for backend and consumer revisions | Main-only protected environment; pinned Render commit, separate admission/deploy/post-deploy jobs. Live revisions listed below. |
| Layout-aware native prose | Code and synthetic fixtures verified | PyMuPDF coordinates/provenance, reading order/multi-column restoration, cleanup and structured-text exclusions. Native text was exercised in production; broader labeled layout evaluation remains open. |
| Hosted image OCR/captioning | Code/public-provider fixtures verified | Hosted layout extraction, deterministic preprocessing and shared original-image provenance. Unapproved sensitive inference fails closed. Production image/scan journey remains open. |
| Structured tables and charts | Code/public-provider fixtures verified | `document_evidence`, structured chunking, stable IDs, row/header boundaries, continuation checks and private crops. Chart values remain explicitly unverified unless supported; exact clinical values use structured lookup. Broader labeled accuracy evaluation remains open. |
| Hosted semantic chunking and embeddings | Implemented and benchmarked narrowly | Exact Cohere tokenization, 384-dimensional pinned embeddings and batched calls. Public four-topic starting-value comparison documented in `HOSTED_CHUNK_BENCHMARK.md`; 400/480/60 chosen under the API's 512-token limit. This is not a broad retrieval benchmark. |
| No local inference weights in production | Verified | Canonical API/worker image gates run without networking; 96 canonical processing checks. Production live smoke measured 134.8 MiB initially and 179.9 MiB after the journey, below the 420 MiB gate. Historical worker modules are not the production runtime. |
| Context dependence, selective history and rewriting | Code verified; native conversation live proof passed | `conversation_context.py`, `query_rewriter.py`, delegated conversations. Native/v1/medical shared preparation, exact prior responses, irrelevant-history exclusion and maternity-policy/interns regression have PostgreSQL coverage. New native conversation JSON/SSE turns and saved message IDs/text passed the live gate. |
| Validated intent routes and tools | Code verified | `query_intent.py`, `query_routes.py`, server-owned `query_policies`; DIRECT/RAG/GENERAL_LLM/TOOL_API/CLARIFICATION, explicit uncertainty and a registered read-only document-status tool. General/routing inference defaults off; arbitrary side effects are unavailable. |
| Hybrid retrieval and hosted reranking | Implemented; broader tuning open | PostgreSQL lexical+dense RRF, bounded authorized candidates and pinned hosted reranking. Live marker retrieval/reranking passed. Adjacent-evidence benefit and labeled hybrid/rerank comparisons remain to measure. |
| Grounding and provider guardrails | Code verified; standalone live proof passed | Server-owned extractive selection, source/citation verification, secret screening and current permission checks before emission. Prompt-injection, fabricated-citation, clinical/authorization/failure regressions run in CI. Department/position/document-grant requirements still need final applicability review. |
| Permission-aware memory | Code verified | PostgreSQL messages and separate bounded compact memory; actor/conversation isolation, message/source/version IDs, fresh evidence authorization, rollback/concurrency/revocation tests. |
| Permission-aware caching | Code verified | `retrieval/permission_cache.py`, migration 020: signed references/scores, scoped context/policy/version key, fresh source reads, TTL/capacity bounds and atomic publication/deletion invalidation. Repeat-call avoidance tested with synthetic adapters; production performance effect remains to measure. |
| Document replacement and historical provenance | **Partial** | Migration 021 records publication versions, original-byte hashes, active pointer and citation/memory version IDs atomically. Changed-file upload intents, retained historical evidence, bounded reindex and verified index cutover remain open. |
| Immediate deletion and durable physical cleanup | Standalone live verified; consumer live proof open | SQL revocation/fencing plus idempotent leased object/crop cleanup and scoped vector-count receipts. The recovery journey and two interrupted receipts verified completion. |
| Indexed-without-vectors reconciliation | Open | A bounded dry-run-first approved-scope recovery/cutover controller is still required; do not run the superseded local-model backfill on private production content. |
| Performance, quality and capacity | Partial | Small public chunk benchmark, canonical image memory checks and live journey measurements exist. Labeled Recall@K, citation/answer/refusal quality, distributions, P95 and measured capacity/free-tier comparisons remain open. Independent API/worker roles are documented; no extra paid service was created. |
| Future-client integration guide | Implemented for the current API | `VERSIONED_CLIENT_GUIDE.md` covers explicit client/tenant grants, actor/subject delegation, classification approval, upload/register/poll, search/citations, both SSE formats, version limitations and verified cleanup. #65 adds the missing conversation scopes to operator bootstrap; five PostgreSQL bootstrap cases and required CI passed. Changed-file replacement remains open in its own row. |
| Free-tier and provider constraints | **Partial verification** | Existing Render service free; Supabase free previously verified; public GitHub repository; owner-confirmed Cohere trial key matched to protected fingerprint with a 900-call app cap. Sensitive content remains ineligible. Vercel management returns 403, so its account-plan verification is still open. |

## Serving revisions and release proof

| Application | Verified serving revision | Result |
| --- | --- | --- |
| Search Sphere | `f1d9e72ca5fde0d6cd51b0d9379ad00b397fc76c` | Render `dep-dbnga7lg1s2s73ckiuhg`; admission/migrations, deployment and standalone journey passed [42366356255](https://github.com/karanagg166/search-sphere/actions/runs/42366356255). Expanded standalone/conversation JSON/SSE and physical cleanup passed [42367181169](https://github.com/karanagg166/search-sphere/actions/runs/42367181169) using trusted controller `ac1af4501028c29ffba34d5de52fa54201a39a3a`. |
| Quick-Clinic | `4ba7f6f61dfa6ded1b3738dff12b762bff6298cc` | Exact live health verified; earlier deployment-contract [42254878720](https://github.com/karanagg166/Quick-Clinic/actions/runs/42254878720) passed. Full authenticated medical journey remains open. |
| ExamArena | `d96fe305a871d8560e89a0bd320cfcf742f51cd0` | Exact live health and deployment-contract [42303541326](https://github.com/karanagg166/ExamArena/actions/runs/42303541326) passed. Full authenticated course-material journey remains open. |

Recovery PRs: [Search Sphere #58](https://github.com/karanagg166/search-sphere/pull/58),
[#59](https://github.com/karanagg166/search-sphere/pull/59),
[#60](https://github.com/karanagg166/search-sphere/pull/60),
[#61](https://github.com/karanagg166/search-sphere/pull/61), and
[ExamArena #31](https://github.com/karanagg166/ExamArena/pull/31).
Earlier feature/security PRs and their checks are linked in the detailed ledger.

Conversation repairs and coverage: [#63](https://github.com/karanagg166/search-sphere/pull/63),
[#64](https://github.com/karanagg166/search-sphere/pull/64),
[#65](https://github.com/karanagg166/search-sphere/pull/65), and
[#66](https://github.com/karanagg166/search-sphere/pull/66).

The production cold-start/restart and bounded-provider-failure assertions still need
their full release-gate cases. A successful fresh deployment and the earlier bounded
504 observations do not replace those assertions. The rollout must not be marked
complete until the remaining implementation, all three required journeys and the
free-tier budget checks pass.

An ordinary account with unknown document classification remains ineligible for
Cohere trial inference. #63 displays the approval failure and prevents an automatic
second generation attempt. The successful synthetic account's approval does not
authorize that other account or its documents.
