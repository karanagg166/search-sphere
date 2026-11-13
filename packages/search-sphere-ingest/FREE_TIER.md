# Free-tier evidence and restrictions

Verified against official documentation on 2026-11-13; account observations are
separate from published allowances. No paid resources were provisioned.

| Service | Verified evidence | Consequence |
|---|---|---|
| Render free web service | [Free service restrictions](https://render.com/docs/free), [compute limits](https://render.com/docs/compute-plans) | 512 MiB, one free instance; idle services sleep after 15 minutes; no 24/7 uptime guarantee |
| Existing Search Sphere | Render connector: service srv-db02odm7bikc73fv0e7g, plan free | Preserve current plan and deployment; no disk, paid worker or cron added |
| Gemini Flash-Lite / Flash | [Pricing](https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite), [model capabilities](https://aistudio.google.com/docs/models/gemini-3.5-flash-lite) | Requested IDs exist with image/structured output and free inference tiers; key/account eligibility remains unverified |
| Gemini quotas | [Rate limits](https://aistudio.google.com/docs/rate-limits) | RPM/TPM/RPD are project-specific; active allowances require AI Studio; do not invent fixed quota numbers |
| Google data terms | [API terms](https://ai.google.dev/gemini-api/terms) | Free-tier data handling needs explicit review before private/clinical material; this adapter only accepts public/synthetic |
| Existing Cohere | Existing application guard and PostgreSQL budget | Same trial-key fingerprint, data approvals and capped monthly budget; no paid replacement |
| Supabase/vector storage | Existing configured infrastructure preserved | No new projects, upgrades or production data mutations; account remaining quotas not verified here |

No Gemini key with a verified free-account fingerprint is configured locally.
No inference request was sent to Gemini and no successful live chart/diagram
extraction is claimed. The adapter is a real SDK integration with deterministic
contract tests; those tests are not inference-quality evidence.

The canonical production image contains no Torch, Paddle, Docling, Grounding DINO,
TrOCR or local generative model. Native extraction was measured under a Linux
512 MiB container ceiling without networking, including a 30-page text PDF, a
two-page table and a scanned PDF. See TESTING.md for results. This benchmark is
small and synthetic; it does not certify every PDF or free-cloud CPU latency.
Visual inference, optional OCR and heavy layout models need separate measurements.

Defaults: 20 MiB file, 50 pages, 20,000 PDF objects, 16M image pixels, 8 MiB total
stream/text and crop budgets, 32 visual regions, 1400-pixel render side, serial
region processing, 90-second stage deadline and <=1000 chunks. Standalone isolated native parsing
uses a disposable Linux process with 256 MiB virtual-memory and 90 CPU-second
ceilings. The application retains its existing worker thread and container
ceiling; cancelling an await cannot terminate a running native parser thread. Large/complex PDFs may fail these bounds.

The existing DB lease/outbox mechanism recovers interrupted attempts when the
service starts again. It does not execute while Render suspends the process.
Polling or keepalive traffic cannot establish a commercial uptime SLA. A $0
always-awake backend requirement remains incompatible with this Render plan.

Deployment gate: the new package has not been deployed to Render. The optional
image requires repository-root build context, empty Render rootDir, and
`./apps/api/Dockerfile`. The pinned release controller verifies the free/provider
policy before configuring these paths. Two observed free web services share 750
instance-hours; actual usage remains unknown. Warming is disabled without a fresh
budget snapshot. See [reliability controls](../../docs/FREE_TIER_RELIABILITY.md).
