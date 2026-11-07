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
| One PostgreSQL ingestion path | Code verified; standalone and course upload/index live proof | The durable worker/queue and fencing/retry regressions pass. New PDFs indexed through native Search Sphere and all three ExamArena fixture collections in [42417055080](https://github.com/karanagg166/ExamArena/actions/runs/42417055080). The complete medical journey remains open. |
| Progress and queue age | Implemented | `services/ingestion_progress.py`, job metadata and lightweight health/diagnostics. Uploaded/indexed states were observed in the live journey; larger queue/capacity measurements remain open. |
| Consumer status/failure contracts | Code and deployment contracts verified | Quick-Clinic #160/#161/#163/#164/#165 and ExamArena #29–#39; actual upstream FAILED/outage handling, scoped doctor context and strict physical-cleanup receipts. ExamArena now has a protected authenticated release controller; its complete live journey has not passed. |
| Doctor/patient and school/class authorization | Partial acceptance | ExamArena #34 refreshes caller role/school/enrollment after upstream work; eight PostgreSQL revocation cases withhold evidence or cleanup results after grants change. Quick-Clinic #165 refreshes account/appointment/thread access and withholds unvalidated SSE text. Four synthetic ExamArena logins work in production. Search Sphere #69 enforces catalog collection bounds and rejects subject-only tenant expansion; deployed scope gate [42413367038](https://github.com/karanagg166/search-sphere/actions/runs/42413367038) passed. Complete consumer evidence journeys remain open. |
| Pre-deployment PostgreSQL/pgvector gates | Verified | Required CI runs migrations and scope/idempotency/recovery/reindex/provider/consumer-contract tests. `release.yml` admits only successful exact-main CI and applies production migrations before deployment. |
| Three authenticated production journeys | **Current revisions incomplete** | Historical Search Sphere full journey passed on `38e5830`. Current `370c311` passed fresh upload/index/retrieval/cited JSON but failed hosted SSE with `provider_timeout` in [42416819329](https://github.com/karanagg166/search-sphere/actions/runs/42416819329) and [42417637853](https://github.com/karanagg166/search-sphere/actions/runs/42417637853); both verified physical cleanup. ExamArena reached three indexed PDFs and school/class isolation, then failed answer and cleanup verification. Independent authenticated follow-up verified all three physical cleanups. Quick-Clinic full journey is pending. |
| Smoke pagination, failure handling and fixture provenance | Standalone live verified; consumer code verified | Standalone smoke uses offset pagination, signed ownership, strict 503/physical-count checks and retained receipts. ExamArena #37–#39 add signed content-free journals, interrupted-upload discovery and trusted prior-artifact restoration; missing provenance from a mutating run fails closed. Its first failed protected run preserved the empty journal. Consumer physical cleanup still needs live proof. |
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
| Immediate deletion and durable physical cleanup | Standalone live verified; consumer live proof open | SQL revocation/fencing plus idempotent leased object/crop cleanup and scoped vector-count receipts. The recovery journey and two interrupted receipts verified completion. |
| Indexed-without-vectors reconciliation | Controller merged; production source blocked | [#68](https://github.com/karanagg166/search-sphere/pull/68), `services/index_recovery.py` and `scripts/recover_missing_vectors.py` provide exact-scope, bounded private receipts, fresh eligibility checks and fenced worker requeueing. PostgreSQL fixtures prove restoration and concurrency/isolation; the reported owner's production dry run found one candidate blocked by provider approval, with zero writes or inference. No private content was reclassified or reindexed. The full replacement/index cutover remains open separately. |
| Performance, quality and capacity | Partial | Small public chunk benchmark, canonical image memory checks and live journey measurements exist. Labeled Recall@K, citation/answer/refusal quality, distributions, P95 and measured capacity/free-tier comparisons remain open. Independent API/worker roles are documented; no extra paid service was created. |
| Future-client integration guide | Implemented for the current API | `VERSIONED_CLIENT_GUIDE.md` covers explicit client/tenant grants, actor/subject delegation, classification approval, upload/register/poll, search/citations, both SSE formats, version limitations and verified cleanup. #65 adds the missing conversation scopes to operator bootstrap; five PostgreSQL bootstrap cases and required CI passed. Changed-file replacement remains open in its own row. |
| Free-tier and provider constraints | Evaluation budget verified; production provider eligibility blocked | Existing Render/Supabase free; owner-confirmed Vercel Hobby and Cohere Trial. App budget check observed 169 reserved monthly calls under the hard 900 cap (excludes external key use). Current Cohere pricing prohibits production/commercial use of Trial keys; deployed synthetic evaluation is separate. Sensitive content remains ineligible. A provider-approved $0 production arrangement is required before production enablement. |

## Serving revisions and release proof

| Application | Verified serving revision | Result |
| --- | --- | --- |
| Search Sphere | `370c311ee3a2a10f439bc4221ae789dbbcf58ab7` | Exact main CI [42415311711](https://github.com/karanagg166/search-sphere/actions/runs/42415311711) and admission/migrations/Render `dep-dbnpl1ou01pc73dd8njg` deployment passed [42416819329](https://github.com/karanagg166/search-sphere/actions/runs/42416819329). Its complete journey and repeat [42417637853](https://github.com/karanagg166/search-sphere/actions/runs/42417637853) failed SSE with safe `provider_timeout`; physical cleanup passed. Deployed collection-scope gate [42418348287](https://github.com/karanagg166/search-sphere/actions/runs/42418348287) passed. |
| Quick-Clinic | `ef5b2b7e74abf0436b737673b188c1ea9d1a0f83` | Vercel production deployment `7627527273`, exact live health, main CI [42417587912](https://github.com/karanagg166/Quick-Clinic/actions/runs/42417587912) and deployment-contract [42418221198](https://github.com/karanagg166/Quick-Clinic/actions/runs/42418221198) passed after #166/#167. Full medical journey remains open. |
| ExamArena | `5bc1a09dcd9c91c271484c23e3540e75d165eccc` | Vercel redeploy `dpl_E5d7C1VZWD7bLjCnNtPzCca7j7WZ`, exact production health, main CI [42411134515](https://github.com/karanagg166/ExamArena/actions/runs/42411134515) and earlier contract [42412380764](https://github.com/karanagg166/ExamArena/actions/runs/42412380764). Authenticated [42417055080](https://github.com/karanagg166/ExamArena/actions/runs/42417055080) reached indexing/isolation then failed answer and cleanup verification; #41 repairs consumer deadline/status handling. |

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
