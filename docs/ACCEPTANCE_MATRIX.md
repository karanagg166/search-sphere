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
| One PostgreSQL ingestion path | All three authenticated live journeys verified | Durable PostgreSQL claims, leases, retries, restart fencing and idempotency tests pass. New standalone, medical and course PDFs indexed in the current-revision workflows linked below. Forced in-flight production restart remains a separate acceptance item. |
| Progress and queue age | Implemented | `services/ingestion_progress.py`, job metadata and lightweight health/diagnostics. Uploaded/indexed states were observed in the live journey; larger queue/capacity measurements remain open. |
| Consumer status/failure contracts | Code, exact CI and all three live journeys verified | Actual FAILED/outage states, bounded query/stream response deadlines, doctor context and strict cleanup receipts are preserved. Provider 429/503/504 codes remain distinct from missing evidence without raw diagnostics. Medical #177–#179 repair receipt/schema diagnostics and runtime margin; all final positive journeys pass. |
| Doctor/patient and school/class authorization | Existing medical and student enrollment boundaries live verified; assigned-teacher repair pending rollout | Seeded doctor/patient grants deny the other patient’s evidence. The earlier course journey proves school and student enrollment isolation; it does not prove assigned-only teacher access. The owner's clarification exposed school-wide teacher reads of private student records and materials, classmate reads of full profiles, and self-service elevation paths. ExamArena [#44](https://github.com/karanagg166/ExamArena/pull/44) and [#45](https://github.com/karanagg166/ExamArena/pull/45) repair these with live PostgreSQL assignment checks, SQL catalog filters, revocation and collection-bound cleanup. Local regressions pass; final deployed checks remain required. |
| Standalone document ownership versus external AI approval | Existing ownership enforced; approval error display repaired and live | Search Sphere [#88](https://github.com/karanagg166/search-sphere/pull/88) preserves the policy error across HTTP/SSE/JSON, explains external AI approval separately from document access, and removes the ineffective retry button. All 45 frontend tests, exact main CI and the deployed production JavaScript pass. No account or document was reclassified; unknown/private content remains blocked. |
| Private student PDF ownership and Q&A | Implementation pending; proposed contract recorded | [Private student-document contract](PRIVATE_STUDENT_DOCUMENT_CONTRACT.md). ExamArena private profiles and report histories use the new self/assigned-teacher/principal checks. `CourseMaterial` stores shared teaching resources and has no student-owner field. Private student PDF ingestion must remain disabled until a dedicated student subject contract and per-operation grants exist; class membership alone must never grant access to another student’s private reports. |
| Pre-deployment PostgreSQL/pgvector gates | Backend verified; consumer CI covered | Search Sphere CI runs real migrations and scope/idempotency/recovery/reindex/provider contracts; `release.yml` admits successful exact-main CI before production migration/deploy. Consumer PR CI runs PostgreSQL and failure/authorization regressions before merge; Vercel main auto-deploys in parallel with main CI. Strict consumer deployment admission remains to review. |
| Three authenticated production journeys | **All three passed at the previous checkpoint; newer ExamArena permissions require a refreshed journey** | Search Sphere [42493846494](https://github.com/karanagg166/search-sphere/actions/runs/42493846494), Quick-Clinic [42494564776](https://github.com/karanagg166/Quick-Clinic/actions/runs/42494564776) and ExamArena [42495385199](https://github.com/karanagg166/ExamArena/actions/runs/42495385199) pass fresh PDFs, indexing, marker retrieval, expected grounded/page-cited answers and physical cleanup against Search Sphere b205832. Native and medical JSON/SSE/persisted turns pass; medical patient and course school/class isolation pass. Earlier failures remain recorded. |
| Smoke pagination, failure handling and fixture provenance | Native fresh-runner restoration and physical checks live verified | Search Sphere #78/#79 preserve signed pending IDs and catalog journaling; #80/#81 establish the read-only eight-count SQL/Storage baseline; #83/#84 restore bounded signed artifacts before mutation and detect old run IDs rerun after the baseline. [42492212378](https://github.com/karanagg166/search-sphere/actions/runs/42492212378) and [42493846494](https://github.com/karanagg166/search-sphere/actions/runs/42493846494) pass. Consumer signed journals retain their own scope/provenance. Offset pagination, deletion-503 rejection and scoped physical counts remain required. |
| Trusted releases and exact deployed revision | Verified for backend and consumer revisions | Main-only protected environment; pinned Render commit, separate admission/deploy/post-deploy jobs. Live revisions listed below. |
| Layout-aware native prose | Code and synthetic fixtures verified | PyMuPDF coordinates/provenance, reading order/multi-column restoration, cleanup and structured-text exclusions. Native text was exercised in production; broader labeled layout evaluation remains open. |
| Hosted image OCR/captioning | Code/public-provider fixtures verified | Hosted layout extraction, deterministic preprocessing and shared original-image provenance. Unapproved sensitive inference fails closed. Production image/scan journey remains open. |
| Structured tables and charts | Code/public-provider fixtures verified | `document_evidence`, structured chunking, stable IDs, row/header boundaries, continuation checks and private crops. Chart values remain explicitly unverified unless supported; exact clinical values use structured lookup. Broader labeled accuracy evaluation remains open. |
| Hosted semantic chunking and embeddings | Implemented and benchmarked narrowly | Exact Cohere tokenization, 384-dimensional pinned embeddings and batched calls. Public four-topic starting-value comparison documented in `HOSTED_CHUNK_BENCHMARK.md`; 400/480/60 chosen under the API's 512-token limit. This is not a broad retrieval benchmark. |
| No local inference weights in production | Verified in images and current live release | API/worker image checks run without networking under 512 MiB; local uv image checks peak at about 191 MiB. Current live release begins at 215.1 MiB and ends at 257.3 MiB, normal pressure, after fresh PDF/JSON/SSE/cleanup. No local weights or downloads are present. |
| Context dependence, selective history and rewriting | Code verified; native conversation live proof passed | `conversation_context.py`, `query_rewriter.py`, delegated conversations. Native/v1/medical shared preparation, exact prior responses, irrelevant-history exclusion and maternity-policy/interns regression have PostgreSQL coverage. New native conversation JSON/SSE turns and saved message IDs/text passed the live gate. |
| Validated intent routes and tools | Code verified | `query_intent.py`, `query_routes.py`, server-owned `query_policies`; DIRECT/RAG/GENERAL_LLM/TOOL_API/CLARIFICATION, explicit uncertainty and a registered read-only document-status tool. General/routing inference defaults off; arbitrary side effects are unavailable. |
| Hybrid retrieval and hosted reranking | Implemented; broader tuning open | PostgreSQL lexical+dense RRF, bounded authorized candidates and pinned hosted reranking. Live marker retrieval/reranking passed. Adjacent-evidence benefit and labeled hybrid/rerank comparisons remain to measure. |
| Grounding and provider guardrails | Code verified; standalone live proof passed | Server-owned extractive selection, source/citation verification, secret screening and current permission checks before emission. Prompt-injection, fabricated-citation, clinical/authorization/failure regressions run in CI. Department/position/document-grant requirements still need final applicability review. |
| Permission-aware memory | Code, exact CI and native/medical live journeys verified | Scoped conversations, saved JSON/SSE turns, compact memory/source references and upstream-before-local conversation deletion pass. Current grants and source permissions are refreshed before emission/history reuse; the complete medical journey verifies saved turns and cleanup. Changed-file replacement/historical evidence remains in its separate partial row. |
| Permission-aware caching | Code verified | `retrieval/permission_cache.py`, migration 020: signed references/scores, scoped context/policy/version key, fresh source reads, TTL/capacity bounds and atomic publication/deletion invalidation. Repeat-call avoidance tested with synthetic adapters; production performance effect remains to measure. |
| Document replacement and historical provenance | **Partial** | Migration 021 records publication versions, original-byte hashes, active pointer and citation/memory version IDs atomically. Changed-file upload intents, retained historical evidence, bounded reindex and verified index cutover remain open. |
| Immediate deletion and durable physical cleanup | All three current live journeys verified | SQL revocation returns before physical cleanup; the leased worker retries object/vector deletion. Native, medical and course journeys independently verify zero physical vectors, deleted originals/derived objects and inaccessible metadata, including signed tombstones and interrupted-run receipts. |
| Indexed-without-vectors reconciliation | Controller merged; production source blocked | [#68](https://github.com/karanagg166/search-sphere/pull/68), `services/index_recovery.py` and `scripts/recover_missing_vectors.py` provide exact-scope, bounded private receipts, fresh eligibility checks and fenced worker requeueing. PostgreSQL fixtures prove restoration and concurrency/isolation; the reported owner's production dry run found one candidate blocked by provider approval, with zero writes or inference. No private content was reclassified or reindexed. The full replacement/index cutover remains open separately. |
| Performance, quality and capacity | Partial | Small public chunk benchmark, canonical image memory checks and live journey measurements exist. Labeled Recall@K, citation/answer/refusal quality, distributions, P95 and measured capacity/free-tier comparisons remain open. Independent API/worker roles are documented; no extra paid service was created. |
| Future-client integration guide | Implemented for the current API | `VERSIONED_CLIENT_GUIDE.md` covers explicit client/tenant grants, actor/subject delegation, classification approval, upload/register/poll, search/citations, both SSE formats, version limitations and verified cleanup. #65 adds the missing conversation scopes to operator bootstrap; five PostgreSQL bootstrap cases and required CI passed. Changed-file replacement remains open in its own row. |
| Free-tier and provider constraints | Free/trial safeguards verified; real production/private inference approval remains open | November 9 read-only recheck: 584 app-reserved Cohere calls under the hard 900 cap, 316 remaining, excluding external key use; database 16,944,275 bytes. The November 8 object snapshot remains 0.74 MB. Render reports the service plan `free` and the owner confirmed no payment method is attached; its [documented quota behavior](https://render.com/docs/faq) pauses builds/services instead of charging without one. Vercel’s connector confirms `hobby`; the owner confirms Cohere Trial and free/trial use only. No paid upgrade was enabled. Trial production/commercial and sensitive-data approval remain separate blockers. |

## Serving revisions and release proof

| Application | Verified serving revision | Result |
| --- | --- | --- |
| Search Sphere API | `b205832aa49c983c2bf95c692a0e29b35df697f9` | Exact main CI [42493579449](https://github.com/karanagg166/search-sphere/actions/runs/42493579449) and pinned release/full native journey [42493846494](https://github.com/karanagg166/search-sphere/actions/runs/42493846494) pass. Public health verifies the intended revision. The earlier 2b95740 release deployed successfully but its SSE gate failed with provider_timeout; this failure remains recorded. |
| Search Sphere frontend | `f006484793a21a846aa9fe7ed291a9ccf3433c08` | [#88](https://github.com/karanagg166/search-sphere/pull/88) and exact main CI [42590240984](https://github.com/karanagg166/search-sphere/actions/runs/42590240984) pass. Vercel `dpl_3PGQP2h5KamwBNgPxdiYTHTKUF24` is READY at that revision. Production `/search` returned 200 and its 11 JavaScript assets contain the approval-required heading and separate-access explanation. Render remains at b205832. |
| Quick-Clinic | `577d53368ffe7ddd6877db4eff8c605eecad4183` | Exact main CI [42491487001](https://github.com/karanagg166/Quick-Clinic/actions/runs/42491487001), Vercel `dpl_AV937vwUbtJ9BFJfj9XCFx3kTj1u` READY/public health and full medical journey [42494564776](https://github.com/karanagg166/Quick-Clinic/actions/runs/42494564776) pass against b205832. Two new PDFs, patient isolation, cited legacy/JSON/SSE answers, persisted turns and physical cleanup pass. Earlier 60-second termination and provider-timeout failures remain recorded. |
| ExamArena | `c95aeb63420259da4462cd0f19b62164f2d13dd0` | Exact main CI [42491826128](https://github.com/karanagg166/ExamArena/actions/runs/42491826128), Vercel `dpl_36obuHsFajes8b3p7ApnJ6CKB2FZ` READY/public health and full course journey [42495385199](https://github.com/karanagg166/ExamArena/actions/runs/42495385199) pass against b205832. Three new PDFs, school/class isolation, expected cited answers and physical cleanup pass. Production build logs confirm uv dependency installation. Course answers use JSON; SSE is unsupported. |

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
