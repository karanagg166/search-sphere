# Document intelligence and reliability acceptance report

Requirements reference: final Search Sphere extraction/chunking section of the
Google Doc AI NOTES (modified 2026-11-12), read as requirements only.

Baseline inspected: `4d28e58f4f310309f7c6ea350080419a6b51301b` on main.
Implementation is in the normal `search-sphere` repository, branch
`feat/reusable-ingestion-recovery`. No second application or checkout is required.
Status: deployed implementation; **full release acceptance remains pending the
explicit accuracy/free-tier blockers and live checks recorded below**.

## Reused baseline

FastAPI/Python 3.12; native PyMuPDF text, tables and reading order; guarded hosted
Cohere layout/OCR/captioning; `embed-english-light-v3.0` (384 dimensions,
512 tokens), exact tokenization, hybrid retrieval, query rewrite/rerank/RAG;
private Supabase originals/crops; PostgreSQL/pgvector; scoped evidence access;
durable leased worker, atomic reindex/version publication, physical cleanup and
missing-vector recovery. The deployed consumer chatbot APIs remain retired.
No users, documents or existing vectors were migrated or rebuilt.

## Completed candidate changes

- Independent typed `search-sphere-ingest` 0.1.0 library, src layout, uv.lock,
  wheel/sdist, CLI, async APIs, real PDF/image extraction, source schemas,
  sentence/section-aware overlap, specialized table/chart/image/diagram chunks,
  provider interfaces, optional adapters and private immutable local assets.
- The existing application extractor now feeds shared normalization/chunking.
  There is one ingestion pipeline, with no second PDF extraction pass or engine
  switch. The app injects Cohere's actual tokenizer and keeps the existing
  embedding/vector publication path. Plain-text sources receive checksum IDs.
- Full text/table/chart/diagram/image source objects and normalized views live
  in existing owner-scoped JSONB evidence; metadata includes original geometry,
  source IDs, hierarchy, sentences, row ranges, pipeline version and private crop
  references. A manifest preserves page/section hierarchy. No new migration.
- Dedicated strict diagram graph schemas and extraction, merged table cells,
  OCR region boxes/languages/styles, separate literal/generated fields and
  model uncertainty. Numeric labels and arrows remain unverified/review-required.
- Bounded OpenCV panel proposals inform the same approved hosted layout call.
  Multiple regions retain their full parent crop. Unresolved proposed panels
  explicitly remain partial. No Grounding DINO weights or paid fallback.
- Bounded read-only readiness recovery, idle-pool disposal, worker poll backoff
  and activity heartbeat; safe bounded frontend read retries with session checks.
- Optional dependency-free monitor with shared free-hour admission, fresh usage
  snapshots, safety reserve, failure streaks and revision/worker/pressure checks.
  It remains disabled; exact setup is in FREE_TIER_RELIABILITY.md.
- Canonical root-context API/worker images and CI package/type/audit/build/install
  plus bounded real-input image gate. Protected release checks require the new
  package job and verify Render monorepo paths before pinned deployment.

## Capability matrix

| Capability | Real implementation and evidence | Acceptance limit |
|---|---|---|
| Native PDF text, rotation, paragraphs, coordinates | PyMuPDF; real generated PDFs and source integrity tests | Native complex layouts/headings remain heuristic |
| Sentence hierarchy and overlap | Deterministic sentence offsets; heading path in embedding text; exact injected tokenizer | Oversized sentences fragment explicitly; tokenizer calls consume quota |
| PDF validation/isolation | Core size/pages/streams/dimensions/encryption checks; standalone disposable process | Existing app native parser is a bounded worker thread, not a hard-kill subprocess |
| Native ruled tables and continuation | Real cross-page table, cells/provenance, conservative geometry/header/context matching | Native merged candidates defer to structural provider; split-row repair unavailable |
| Hosted scanned/borderless/merged tables | Existing Cohere route plus typed row/cell/span schema | Broad table accuracy remains unbenchmarked; always review-required |
| Docling/TableFormer/figure classifier | Optional real converter with artifacts/memory admission and accurate mode | Not run on 512 MiB; no model/version accuracy or free-hosted acceptance |
| Internal figure panels | Real CV operations, full whitespace separation, parent-child mapping, original retained | Proposals do not verify semantic completeness; difficult panels remain partial |
| Charts, common/scientific types | Approved Cohere prompt/schema; axes/units/legend/series/raw and approximate labels; full JSON/crop | No new live type-by-type numerical benchmark; generated values never verified |
| Diagrams/flowcharts | Dedicated nodes/edges/decisions/directions, schema integrity and mapped labels/boxes | Arrows not independently visually verified; ambiguous interpretation stays partial |
| Images, OCR, multilingual/style fields | Standalone real image API; hosted app OCR/description separation; exact native Unicode | Hosted multilingual/handwriting accuracy unaccepted; difficult labels partial |
| Local printed OCR | Optional Tesseract subprocess, real English raster test | eng tested; not in Render image; no handwriting promise |
| Grounding DINO/Paddle/TrOCR/Granite/PP-Chart2Table | Unavailable capability documented; provider interfaces accept alternatives | No verified free compatible inference endpoint or resource acceptance |
| Optional Gemini | Actual Google GenAI adapter, strict response schema, key fingerprint/free-account gate | No approved configured account or live availability/quota evidence; disabled |
| Content-aware chunks | Prose overlap, repeated table headers/row boundaries, visual/graph relationships and source links | Oversized row/relation fails explicitly; no silent evidence truncation |
| Persistence/deletion/reprocessing | Existing scoped JSONB/private storage/pgvector integration; real PostgreSQL tests | No rebuild of existing vectors; per-page checkpoint resume not implemented |
| Resume and UI cold starts | Durable lease recovery; stale pool retry; GET/HEAD-only retries and reconnect UI | No worker executes during Render suspension; no uptime SLA |
| Always-warm monitoring | Minimal script/workflow with tested fail-closed budget controls | Off: shared usage unknown, external scheduler/terms verification missing |

The package's PIPELINES.md and PROVIDERS.md detail each optional path and license.
Missing a model is not treated as completed extraction. The native fallback cannot
promise that every borderless table or ambiguous visual in an arbitrary PDF was
identified. Retain the original and use an evaluated structural provider for those
requirements.

## Installation and independent use

```sh
cd packages/search-sphere-ingest
uv sync --locked --extra vision --extra opencv
uv build
# In a different Python 3.12 project:
uv add /absolute/path/to/search_sphere_ingest-0.1.0-py3-none-any.whl
# Or pin a reviewed commit after merging:
uv add "search-sphere-ingest @ git+https://github.com/karanagg166/search-sphere.git@REVISION#subdirectory=packages/search-sphere-ingest"
```

```python
from search_sphere_ingest import DocumentPipeline

pipeline = DocumentPipeline()
document = pipeline.extract('document.pdf')
chunks = pipeline.chunk(document)
image = pipeline.extract_image('public.png')  # PARTIAL without image providers
assert all(chunk.source_object_ids for chunk in chunks)
```

No application/database imports, API keys or generative inference are needed for
native extraction. The independent default tokenizer is actual cl100k_base for
text-embedding-3-small; inject the intended embedding tokenizer for another host.
Search Sphere injects Cohere. No embeddings are generated by default. PyPI
publication is not configured. The package uses AGPL-3.0-or-later because of
PyMuPDF; dependency and model attribution is in THIRD_PARTY.md.

## Measured local evidence (2026-11-14)

| Check | Result |
|---|---|
| Package lint/type/fixtures | Ruff/format/strict mypy passed; 41 package tests passed |
| Independent installation | Wheel installed in a separate Python 3.12 environment; actual in-memory PDF extraction/chunking passed |
| Canonical worker | 96 passed |
| Backend Linux regressions | 362 passed; one isolated-live-service test skipped |
| PostgreSQL recovery/integration | 57 passed on disposable PostgreSQL 16/pgvector, covering leases, atomic reindex, timeout, cleanup, evidence/assets, recovery and new normalized publication |
| Targeted reliability/schema/release gates | 49 passed (synthetic provider/fault injection; not live inference) |
| Frontend | 70 tests, typecheck and production build passed; lint has four existing navigation warnings |
| Native gold evaluation | 33 pages across three generated PDFs; 30/30 native sentences, 8/8 table values, one cross-page logical table, 31/31 source-linked chunks; scan explicitly PARTIAL |
| Production image, Linux ARM64 | Hosted-only/private-search gate passed, 263.0 MiB peak parent RSS; no local weights/retired routes |
| Real native/CV workload | 512 MiB cgroup; 340,459,520 peak RSS bytes including 16 MP panel input and isolated native extraction; two proposals |
| API + 16 MP image/CV memory | 330.8 MiB peak RSS under 512 MiB cgroup; provider response synthetic, only CPU/memory evidence |
| Runtime dependency audit | No known vulnerabilities in locked core + enabled optional vision/OpenCV dependencies |

A preliminary macOS-to-Docker PostgreSQL run had seven immediate requeue timing
failures while the two clocks diverged during builds. The same 57 tests passed
inside Linux with the database clock; no lease assertion was weakened. The initial
backend run also used a 768 MiB test container despite tests expecting 512 MiB,
and inherited a mounted local dotenv; rerunning in an isolated no-dotenv 512 MiB
container passed. Native AMD64 CI and deployed memory are separate live gates;
local ARM64 results are not substituted for them. Rosetta's RLIMIT_AS mapping
failure was not worked around by weakening existing parser security limits.

## Provider, hosting and release evidence

Cohere trial documentation checked 2026-11-14: 1,000 calls/month; the application
retains 900/month and approved public/synthetic scope. Model IDs are unchanged:
`embed-english-light-v3.0`, `rerank-v3.5`, `command-a-03-2025`,
`command-a-vision-07-2025`. The existing protected release validates the key
fingerprint and provider configuration before making synthetic live calls. Exact
account remaining quota/model availability must come from that live check, not
unit fixtures. No unapproved private documents were sent to a new provider.

Render inventory confirms the existing free Search Sphere service
`srv-db02odm7bikc73fv0e7g` and another free workspace service. They share 750
instance-hours. One continuously active service consumes 720/744 hours in a
30/31-day month; two exceed the pool. Actual cumulative usage is unavailable from
the connected API, so warming stays disabled. No paid resource was created.
Supabase readiness uses real `SELECT 1`, not a liveness-only ping. No promise is
made that this prevents platform pausing.

Pre-release Render still served `01b004478c6736d0431ef81ac48f1bd21c757ec0`.
One actual cold start resolved after an initial 35-second timeout, five-second
backoff and 13.18-second successful request; `/ready` then succeeded in 0.85 seconds.
This is evidence for the old deployment only. Vercel production was READY at
baseline `4d28e58f4f310309f7c6ea350080419a6b51301b` (`dpl_EzgqNk7h7J3Cx8ctXeXiJU17vKcd`).

Deployment and CI evidence follow below. Synthetic upload→embedding→retrieval→
answer→citation→deletion and physical cleanup are accepted only when explicitly
recorded as passed; deployment liveness alone does not prove that journey.
Broad handwritten/multilingual/chart/diagram accuracy, optional heavyweight
models, a verified scheduler, and guaranteed 24/7 hosting remain blockers.

## Pull request CI evidence

[PR #93](https://github.com/karanagg166/search-sphere/pull/93), initial implementation
`a27ea31ff5503872818be7a9696b365a6a293087`, passed all five CI jobs in
[run 43495894182](https://github.com/karanagg166/search-sphere/actions/runs/43495894182).
Native AMD64 evidence: hosted API 265.2 MiB; worker 264.2 MiB; API plus 16 MP
raster/CV 332.3 MiB; gold extraction peak 344,813,568 bytes, all text/table/link
checks passed. CI additionally passed 196 durable PostgreSQL tests, nine Data API
denial checks, 48 provider-boundary checks and 251 monitor/release safety checks.
The final merged implementation also retains all raw cross-page table
segments/header cells and maps each source to its own crop. Its final PR and main
CI runs passed before the pinned deployment.

A separate protected manual workflow, `visual-smoke.yml`, generates a real raster
bar chart (42/80 items) and three-node process diagram in one image. It uploads
only a signed synthetic fixture through the deployed API, reads scoped evidence
IDs through a read-only SQL transaction, fetches full objects through authorized
HTTP, reports label/relationship matches without claiming verification, and
attempts physical cleanup even on failure. This workflow must run only after the
pinned deployment; its unit tests establish fixture/metric logic, not live models.

## Final merged implementation and actual deployment

- [PR #93](https://github.com/karanagg166/search-sphere/pull/93) merged to
  `19c8f56be2d7044d123955e196f5536835ab0b30`.
- [Exact-main CI 43497998763](https://github.com/karanagg166/search-sphere/actions/runs/43497998763)
  passed all five required jobs: 41 package tests, 364 backend tests (one live
  isolated-service skip), 96 worker tests, 70 frontend tests, 196 durable
  PostgreSQL checks, nine Data API denials, 48 provider-boundary checks and 253
  monitor/release/fixture checks. Ruff/typing/build/install/audit/image gates passed.
- Native AMD64 main image measurements: hosted API 265.0 MiB; worker 264.4 MiB;
  API plus real 16 MP raster/CV 330.3 MiB; real gold evaluation peak 344,547,328
  bytes with the documented 30/30 sentences, 8/8 cells and 31/31 links.
- [Pinned release 43498337703](https://github.com/karanagg166/search-sphere/actions/runs/43498337703)
  verified the free plan, approved trial fingerprint, synthetic/public policy,
  private storage/RLS/browser denial and existing migration head
  `022_conversation_deletions`. Render deployment `dep-dbrmrhm7bikc73b0uhtg`
  serves exactly `19c8f56be2d7044d123955e196f5536835ab0b30`; rootDir is empty,
  Docker context `.`, Dockerfile `./apps/api/Dockerfile`, plan free, autodeploy off.
- Vercel production deployment `dpl_DftJWLwTAnDfkKbL93qRGLn75aUs` was READY at
  that same SHA. Render's sampled initial memory was approximately 183 MiB under
  a 512 MiB limit; this is not a peak visual-workload measurement.
- The release workflow's deployment succeeded; its initial post-deployment stage
  stopped before application mutations because historical signed reconciliation
  referenced a revision predating the new package CI requirement. No guard was
  weakened. A fresh [read-only reconciliation 43499089308](https://github.com/karanagg166/search-sphere/actions/runs/43499089308)
  passed and refreshed the signed baseline against the deployed revision.
- A subsequent real `/ready` request returned the exact deployed SHA,
  `database:connected` and `worker:running` after a first health wake timeout.
  No guaranteed availability or indefinite activity is inferred from that sample.

The normal repository remains the sole working checkout for this task. The
user's existing ACCEPTANCE_MATRIX, ROLLOUT_REQUIREMENTS and private student
contract edits were preserved byte-for-byte and excluded from all task commits.

## Actual production RAG journey

[Post-deployment smoke 43502734805](https://github.com/karanagg166/search-sphere/actions/runs/43502734805)
**passed** against deployed `19c8f56be2d7044d123955e196f5536835ab0b30`.
The signed synthetic fixture went through actual upload, extraction, shared
chunking, approved hosted embeddings and pgvector indexing, hybrid retrieval,
grounded standalone answers/page citations, standalone SSE, conversation
answer/history and conversation SSE with exactly one additional persisted turn.
Conversation deletion returned authorized 404. The scoped physical cleanup
receipt verified zero remaining vectors and private original deletion. The
signed fixture receipt artifact is retained by that workflow; no existing user
documents were altered. Initial/final memory telemetry was 183.0/183.9 MiB with
normal pressure. Database readiness reached a real `SELECT 1`. This proves one
real synthetic journey, not broad document accuracy or uninterrupted uptime.

## Actual hosted chart and diagram evaluation

[Visual evaluation 43502742815](https://github.com/karanagg166/search-sphere/actions/runs/43502742815)
**passed** on the same deployed SHA using actual hosted Cohere inference and a
real raster PDF with two panels. The production API indexed two chunks. Authorized
full source objects contained one chart and one diagram. Measured gold matches:
2/2 visible numeric labels (42 and 80), 3/3 node labels (Start, Review, Finish), and
2/2 directed node relationships. All generated numeric claims retained
`verified:false`; all model-reported arrows retained `visible:false`, with review
required. The original/full source objects remained retrievable through scoped
HTTP evidence. Physical cleanup verified zero vectors and private objects removed.
The signed cleanup receipt is retained in the workflow artifact.

This is one synthetic bar-chart/process-diagram sample, not a universal numerical
accuracy result or independent geometric proof of arrow visibility. Broader chart
types, handwriting, multilingual raster OCR, Docling/model resource acceptance,
unknown workspace-hour usage, scheduler setup and guaranteed availability remain
explicit limitations. The full master release is **not RELEASE ACCEPTED** despite
successful implementation CI, deployment, RAG and this visual fixture.

## Reproducible artifact

The tested 0.1.0 wheel builds with `uv build` in `packages/search-sphere-ingest`.
Implementation wheel SHA-256:
`374f8d6d378641057ea594e31fcb45bc6e16ee0f9b506a7c5a320255e7b61b12`.
Documentation-only follow-ups can change artifact hashes if rebuilt; pin the
implementation SHA for this receipt. Independent installation and real native
PDF extraction/chunking passed in a separate Python 3.12 environment.

```sh
uv add "search-sphere-ingest @ git+https://github.com/karanagg166/search-sphere.git@19c8f56be2d7044d123955e196f5536835ab0b30#subdirectory=packages/search-sphere-ingest"
```
