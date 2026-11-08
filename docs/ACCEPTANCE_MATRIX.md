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
| One PostgreSQL ingestion path | Standalone and course live verified; medical code verified | Durable PostgreSQL claims, leases, retries, restart fencing and idempotency tests pass. Fresh native and course PDFs indexed in the successful current-revision journeys linked below. The complete medical journey remains pending. |
| Progress and queue age | Implemented | `services/ingestion_progress.py`, job metadata and lightweight health/diagnostics. Uploaded/indexed states were observed in the live journey; larger queue/capacity measurements remain open. |
| Consumer status/failure contracts | Code and deployment contracts verified | Quick-Clinic #160–#176 and ExamArena #29–#42 preserve actual FAILED/outage states, bounded query response deadlines, doctor context and strict cleanup receipts. Exact upstream 429/503/504 categories survive without raw diagnostics. ExamArena’s full live journey passed; the medical journey is pending. |
| Doctor/patient and school/class authorization | School/class live verified; medical acceptance pending | ExamArena’s current journey proves foreign-school and foreign-class evidence denial. Quick-Clinic’s owned synthetic doctor has an appointment grant to patient A only; independent production login and patient-B search/answer denial passed without inference. Required PostgreSQL revocation cases refresh caller, enrollment, patient and published-version permissions. Search Sphere collection-scope gate [42462882126](https://github.com/karanagg166/search-sphere/actions/runs/42462882126) passed at 9b3c137. |
| Pre-deployment PostgreSQL/pgvector gates | Backend verified; consumer CI covered | Search Sphere CI runs real migrations and scope/idempotency/recovery/reindex/provider contracts; `release.yml` admits successful exact-main CI before production migration/deploy. Consumer PR CI runs PostgreSQL and failure/authorization regressions before merge; Vercel main auto-deploys in parallel with main CI. Strict consumer deployment admission remains to review. |
| Three authenticated production journeys | **Two of three passed at current serving revisions** | Search Sphere [42462273905](https://github.com/karanagg166/search-sphere/actions/runs/42462273905) and ExamArena [42465220926](https://github.com/karanagg166/ExamArena/actions/runs/42465220926) passed fresh PDF upload/indexing, unique-marker retrieval, expected grounded/page-cited answers and physical cleanup. Native JSON/SSE/saved turns and course school/class denial passed. Quick-Clinic’s full medical journey is pending. Earlier failures remain recorded in the ledger. |
| Smoke pagination, failure handling and fixture provenance | Native and course live verified; medical controller verified | Offset pagination, strict deletion 503 failure and scoped physical counts are required. Signed content-free receipts and owned-catalog recovery preserve interrupted-run provenance. ExamArena’s successful run reverified older unresolved receipts. Quick-Clinic #173–#176 add private journals, signed restore, main-only admission and mandatory evidence; missing credentials/assertions/provenance fail closed. |
| Trusted releases and exact deployed revision | Verified for backend and consumer revisions | Main-only protected environment; pinned Render commit, separate admission/deploy/post-deploy jobs. Live revisions listed below. |
| Layout-aware native prose | Code and synthetic fixtures verified | PyMuPDF coordinates/provenance, reading order/multi-column restoration, cleanup and structured-text exclusions. Native text was exercised in production; broader labeled layout evaluation remains open. |
| Hosted image OCR/captioning | Code/public-provider fixtures verified | Hosted layout extraction, deterministic preprocessing and shared original-image provenance. Unapproved sensitive inference fails closed. Production image/scan journey remains open. |
| Structured tables and charts | Code/public-provider fixtures verified | `document_evidence`, structured chunking, stable IDs, row/header boundaries, continuation checks and private crops. Chart values remain explicitly unverified unless supported; exact clinical values use structured lookup. Broader labeled accuracy evaluation remains open. |
| Hosted semantic chunking and embeddings | Implemented and benchmarked narrowly | Exact Cohere tokenization, 384-dimensional pinned embeddings and batched calls. Public four-topic starting-value comparison documented in `HOSTED_CHUNK_BENCHMARK.md`; 400/480/60 chosen under the API's 512-token limit. This is not a broad retrieval benchmark. |
| No local inference weights in production | Verified | Canonical API/worker image gates run without networking; 96 canonical checks. Current release began at 135.2 MiB and current health reports normal pressure. Historical successful journey ended at 181.6 MiB. No local weights were introduced. |
| Context dependence, selective history and rewriting | Code verified; native conversation live proof passed | `conversation_context.py`, `query_rewriter.py`, delegated conversations. Native/v1/medical shared preparation, exact prior responses, irrelevant-history exclusion and maternity-policy/interns regression have PostgreSQL coverage. New native conversation JSON/SSE turns and saved message IDs/text passed the live gate. |
| Validated intent routes and tools | Code verified | `query_intent.py`, `query_routes.py`, server-owned `query_policies`; DIRECT/RAG/GENERAL_LLM/TOOL_API/CLARIFICATION, explicit uncertainty and a registered read-only document-status tool. General/routing inference defaults off; arbitrary side effects are unavailable. |
| Hybrid retrieval and hosted reranking | Implemented; broader tuning open | PostgreSQL lexical+dense RRF, bounded authorized candidates and pinned hosted reranking. Live marker retrieval/reranking passed. Adjacent-evidence benefit and labeled hybrid/rerank comparisons remain to measure. |
| Grounding and provider guardrails | Code verified; standalone live proof passed | Server-owned extractive selection, source/citation verification, secret screening and current permission checks before emission. Prompt-injection, fabricated-citation, clinical/authorization/failure regressions run in CI. Department/position/document-grant requirements still need final applicability review. |
| Permission-aware memory | Code/deployment verified; medical live journey open | Search Sphere #72/#73 add scoped deletion fences and compatible medical memory cleanup (migration 022). Quick-Clinic #166/#167 require upstream cleanup before local deletion and current patient/published-version checks before returning cached assistant text; doctor grants are refreshed after metadata reads. 76 focused consumer checks, required CI and deployed contract pass. No complete medical fixture journey yet. |
| Permission-aware caching | Code verified | `retrieval/permission_cache.py`, migration 020: signed references/scores, scoped context/policy/version key, fresh source reads, TTL/capacity bounds and atomic publication/deletion invalidation. Repeat-call avoidance tested with synthetic adapters; production performance effect remains to measure. |
| Document replacement and historical provenance | **Partial** | Migration 021 records publication versions, original-byte hashes, active pointer and citation/memory version IDs atomically. Changed-file upload intents, retained historical evidence, bounded reindex and verified index cutover remain open. |
| Immediate deletion and durable physical cleanup | Native and course live verified; medical regressions verified | Search Sphere #76 returns after durable SQL revocation and wakes the existing leased cleanup worker. Four blocked-storage regressions prove immediate denial/pending cleanup then fresh-worker completion. All four DELETE surfaces retain scopes and receipt contracts. Native and course live journeys prove zero physical vectors, absent originals/derived objects and inaccessible metadata. |
| Indexed-without-vectors reconciliation | Controller merged; production source blocked | [#68](https://github.com/karanagg166/search-sphere/pull/68), `services/index_recovery.py` and `scripts/recover_missing_vectors.py` provide exact-scope, bounded private receipts, fresh eligibility checks and fenced worker requeueing. PostgreSQL fixtures prove restoration and concurrency/isolation; the reported owner's production dry run found one candidate blocked by provider approval, with zero writes or inference. No private content was reclassified or reindexed. The full replacement/index cutover remains open separately. |
| Performance, quality and capacity | Partial | Small public chunk benchmark, canonical image memory checks and live journey measurements exist. Labeled Recall@K, citation/answer/refusal quality, distributions, P95 and measured capacity/free-tier comparisons remain open. Independent API/worker roles are documented; no extra paid service was created. |
| Future-client integration guide | Implemented for the current API | `VERSIONED_CLIENT_GUIDE.md` covers explicit client/tenant grants, actor/subject delegation, classification approval, upload/register/poll, search/citations, both SSE formats, version limitations and verified cleanup. #65 adds the missing conversation scopes to operator bootstrap; five PostgreSQL bootstrap cases and required CI passed. Changed-file replacement remains open in its own row. |
| Free-tier and provider constraints | Evaluation budget verified; production eligibility blocked | Render/Supabase free and owner-confirmed Vercel Hobby/Cohere Trial. Snapshot on 2026-11-08 08:58 UTC: 297 app-reserved calls under the hard 900 cap, excluding external use; later calls require a fresh snapshot. Cohere pricing prohibits production/commercial Trial use. Only synthetic/public evaluation is approved; real sensitive content stays ineligible. A provider-approved $0 production arrangement is required. |

## Serving revisions and release proof

| Application | Verified serving revision | Result |
| --- | --- | --- |
| Search Sphere | `9b3c1378b4e5a2609c876b7e8d31285d8e2d3552` | [#76](https://github.com/karanagg166/search-sphere/pull/76), exact main CI [42462072961](https://github.com/karanagg166/search-sphere/actions/runs/42462072961), Render `dep-dbo30f7f3r2c73a1v3f0`, pinned release/full journey [42462273905](https://github.com/karanagg166/search-sphere/actions/runs/42462273905) and scope gate [42462882126](https://github.com/karanagg166/search-sphere/actions/runs/42462882126) passed. Exact public health verified. |
| Quick-Clinic | `27dc3066d0617276df6352bad9414cafca536c8b` | Main CI [42466804515](https://github.com/karanagg166/Quick-Clinic/actions/runs/42466804515), Vercel `dpl_EQAfqtymix4NoTxshCEQwFaZCBWQ` READY and exact public health verified. #176 is merged as 9e24d3e; its exact main CI and a redeploy with the new production-only scoped key are pending. Full medical journey remains open. |
| ExamArena | `810f959a0693c281f68a0ddd30d42d5a73ea8fef` | [#41](https://github.com/karanagg166/ExamArena/pull/41)/[#42](https://github.com/karanagg166/ExamArena/pull/42), exact main CI [42420120895](https://github.com/karanagg166/ExamArena/actions/runs/42420120895), Vercel `dpl_GfHW97zDMcvGGaT5rtg3y6DrL2hW` READY, contract [42420950128](https://github.com/karanagg166/ExamArena/actions/runs/42420950128) and full journey [42465220926](https://github.com/karanagg166/ExamArena/actions/runs/42465220926) passed against Search Sphere 9b3c137. Course answers are JSON; this API does not support SSE. |

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

Consumer release follow-up: ExamArena [#34](https://github.com/karanagg166/ExamArena/pull/34),
[#35](https://github.com/karanagg166/ExamArena/pull/35),
[#36](https://github.com/karanagg166/ExamArena/pull/36),
[#37](https://github.com/karanagg166/ExamArena/pull/37),
[#38](https://github.com/karanagg166/ExamArena/pull/38), and
[#39](https://github.com/karanagg166/ExamArena/pull/39),
[#40](https://github.com/karanagg166/ExamArena/pull/40) are merged. The combined
fixture/controller/provenance suite passed 53 cases with real PostgreSQL. A
follow-up repairs the observed cold health-read failure with bounded GET-only
recovery (40 focused checks); it does not install the missing server key or establish
forced restart recovery. The new protected run reached the missing-key upload failure.

Search Sphere [#69](https://github.com/karanagg166/search-sphere/pull/69) and
[#70](https://github.com/karanagg166/search-sphere/pull/70) are merged and deployed.
Seventeen PostgreSQL catalog/legacy cases pass; thirteen new release-scope cases
plus the existing smoke/release suite pass (94 total). The dedicated release key
has only `collections:manage` in one synthetic tenant; the live gate proves that
it cannot read documents and verifies signed empty-catalog cleanup without inference.

The scoped ExamArena key is installed as a production-only Vercel Secret. Its
exact two-school/three-collection policies are short-lived and do not approve
private data or another school. Vercel access is now connected; the owner confirmed
Hobby/free. Four fixture logins and ownership key remain protected release secrets.
Search Sphere #71–#74 and Quick-Clinic #166/#167 are merged; current revisions and
failed/successful gate boundaries above supersede earlier serving-revision records.

Cohere's [pricing](https://cohere.com/pricing) says trial calls are free and restricts
them from production/commercial use. Its [data commitments](https://cohere.com/enterprise-data-commitments)
apply enterprise protections to paying customers; trial data uses its ordinary
terms/privacy policy. Only public/synthetic evaluation tests have been run. No
new model or paid provider was selected. The faster candidate failed two of six
required synthetic selection cases and was not deployed. A provider-approved $0 production
arrangement remains an external requirement, alongside sensitive-data approval.

The production cold-start/restart and bounded-provider-failure assertions still need
their full release-gate cases. A successful fresh deployment and the earlier bounded
504 observations do not replace those assertions. The rollout must not be marked
complete until the remaining implementation, all three required journeys and the
free-tier budget checks pass.

An ordinary account with unknown document classification remains ineligible for
Cohere trial inference. #63 displays the approval failure and prevents an automatic
second generation attempt. The successful synthetic account's approval does not
authorize that other account or its documents.
