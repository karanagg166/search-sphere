# Quick-Clinic integration audit — 2026-10-06

## 1. Compatibility result: synthetic backend verified; deployment pending

The 2026-10-06 follow-up verified synthetic PDF ingestion through PostgreSQL, RabbitMQ, extraction, chunking, real dense/BM25 models and Qdrant, followed by both retrieval contracts, observations, real Cohere answers/citations, chat/SSE and deletion. A live retrieval regression was reproduced: hybrid result mapping dropped generic scope fields, so security correctly discarded otherwise permitted chunks. Both Qdrant copies now preserve those fields, with a real local-Qdrant regression test. Browser E2E and deployed migration remain pending; this active migration report is temporary and must be removed when the deployed migration is finished and its useful conclusions are in permanent docs.

Heads verified before this follow-up's changes: Search-Sphere `df257762970a743a05329734965f26620effb85d`; Quick-Clinic `224e047ded04f08b57096e4250dc226f5e4483d2` on the follow-up branches below. Earlier source-head checks are historical.

Verified remote source heads before changes and again before publication:

- Search-Sphere `feature/generic-rag-microservice`: `bbbeefab23aa83ff2f5a4a1b1d524d3e2812ced8`.
- Quick-Clinic `feature/medical-ai-hardening`: `eb93700fb9755b87781ed774b395bfec40eb8997`.

Follow-up branches: `fix/quick-clinic-generic-rag-compat` and `fix/search-sphere-v1-integration`. Neither is merged automatically.

## 2. Compatibility map recorded before behavior changes

All original calls authenticated with the legacy bearer secret. Tenant was not sent by Quick-Clinic; legacy database records could have `default`, and patient scope was expressed only through body fields and legacy vector aliases.

| Quick-Clinic method | Search-Sphere endpoint | Expected response |
|---|---|---|
| uploadToStorage | POST /internal/medical-documents | storagePath, mimeType, fileSize |
| getSignedStorageUrl | GET /internal/medical-documents/signed-url | url |
| deleteFromStorage | DELETE /internal/medical-documents | success |
| queueMedicalDocumentIngestion / retry | POST /internal/medical-documents/{id}/ingest | documentId, status |
| getMedicalDocumentProcessingStatus | GET /internal/medical-documents/{id}/status | status, processingError |
| deleteMedicalDocumentIndex | DELETE /internal/medical-documents/{id}/index | success |
| searchPatientMedicalRecords | POST /internal/medical-retrieval/search | patient-scoped results |
| generatePatientMedicalAnswer | POST /internal/medical-rag/answer | answer, validated citations |
| generatePatientMedicalChat | POST /internal/medical-rag/chat | answer, rewritten query, citations |
| openPatientMedicalChatStream | POST /internal/medical-rag/chat/stream | SSE tokens, citations, completion |
| queryPatientMedicalObservations | POST /internal/medical-observations/query | typed observations and dates |

Updated wrapper sends backend-only API key (legacy fallback), `X-Client-ID=quick_clinic`, `X-Tenant-ID=quick_clinic_default`, `X-Subject-ID=patientId`, and a deterministic patient collection. Status/deletion explicitly receive the authenticated patient subject. Medical adapters bind headers to body/resource ownership before I/O. Collection ID is `patient_{sha256(patientId UTF-8)[:32]}_records`; patient IDs never serve as tenant IDs.

## 3. Bugs, impact, fixes and tests

Paths below are relative to Search-Sphere unless prefixed Quick-Clinic.

| File / area | Root cause and impact | Fix | Evidence |
|---|---|---|---|
| API + worker vector_store/qdrant_store.py | Undefined payload_schema raised NameError during initialization | Safely read optional CollectionInfo.payload_schema; create only missing eight keyword indexes | test_payload_indexes in both applications: existing/missing/new/None/unavailable schema; byte-parity guard |
| Same Qdrant copies | External IDs alone determined UUIDs and stale deletion; clients/tenants could overwrite or delete each other | Include client + tenant in deterministic point identity; mandatory scoped stale/deletion filters; native deletion excludes external payloads | test_scoped_qdrant_lifecycle: colliding IDs, changed-content shrinking, repeat indexing, scoped deletion |
| security/service_auth.py | Absent legacy secret turned invalid database key into 500; legacy identity could be forged | Invalid/revoked/missing keys return 401; bound client and tenant return 403 on forgery | test_quick_clinic_contract authentication matrix |
| security/service_context.py; generic and medical retrieval services | Body/metadata could replace header scope; legacy retrieval lacked generic mandatory filters | Resolve and reject contradictory scope; filter before retrieval/reranking; reject foreign payloads and DB ownership during enrichment | contract, medical retrieval/chat/RAG and v1 isolation tests |
| services/generic_rag_service.py | Wrong provider keyword context_chunks and tuple-return handling broke generic answers | Call chunks=; unpack answer and actually used evidence; validate citations; safe errors | v1 search/answer tests |
| generic_document_service.py; medical ingestion router | External ID jobs were ambiguous; false enqueue results looked successful; owner reassignment possible | Queue primary UUID; persist FAILED on enqueue failure; refuse owner/scope reassignment and foreign storage/upload | v1 documents, medical ingestion and contract tests |
| v1 deletion + extensions/medical_cleanup.py | Generic deletion called nonexistent vector method; extension observations survived deletion | Scoped vector deletion and transactional optional domain cleanup; preserve metadata on vector failure | v1 document lifecycle and medical deletion tests |
| processing/file_validation.py; upload adapters | Signature-only validation admitted malformed PDFs/images and MIME mismatch | Parse PDF and verify actual JPEG/PNG/WebP content; reject empty, malformed and mismatched input | nine binary validation cases plus storage/upload suites |
| models/medical_observation.py; observation service | Structured observation rows lacked tenant scope | Tenant column, scoped query/replacement/delete and migration | observation suite and migration test |
| worker/db.py | Pool reused asyncpg connections across fresh asyncio.run job loops | NullPool; request context cleared around each actor | Repeated real synthetic medical and generic ingestion/reindex jobs completed in the follow-up |
| Quick-Clinic search-sphere-client.ts and routes | Global secret only; upstream details could leak; SSE timeout ended at headers; delete failure swallowed | API key first, common scope headers, safe errors, bounded retries, stream lifetime timeout/cancel and metadata-preserving failure | Full suite preserves the previously passing focused coverage; real retrieval and Cohere/chat/SSE lifecycle passed |
| API + worker hybrid Qdrant result mapping | Correctly scoped points lost generic identity in HybridSearchResult, causing post-retrieval rejection | Preserve all four generic fields and legacy aliases through mapping/reranking | Regression failed before fix, passed afterward; both full suites and both live retrieval contracts passed |
| repositories/conversation_repository.py | Server timestamp precision allowed random UUID tie ordering to invert native conversation turns | Assign UTC microsecond timestamps when messages are inserted | Native conversation regression passed; original baseline reproduced failure |

No embeddings, vector database or RAG implementation was added to Quick-Clinic. The API/worker Qdrant copies remain identical; sharing code across their separate build contexts is deferred, with a parity regression test preventing drift.

## 4. Actual architecture and authentication

Quick-Clinic browser → Quick-Clinic backend authorization → server-only wrapper → medical compatibility adapters → generic worker pipeline → one Qdrant vector set → dense + BM25 + RRF + cross-encoder retrieval → Cohere grounded generator → citation validation → Quick-Clinic response/UI.

Quick-Clinic continues to own doctor/patient identity, roles, eligible appointment access, consent and its Prisma/UI models. Search-Sphere does not query that database. Structured numeric routing remains in the medical extension, with deterministic observation lookup preferred over generation.

Authentication supports **both during migration**, preferring database ServiceClient API key. The isolated test database has a registered `quick_clinic` client authorized only for `quick_clinic_default`, with documents:read/write/delete, search:execute, answers:generate and collections:manage. No real generated key is committed. Registration in the deployed database is still required; the local registration is not a production migration.

Provision in the intended environment:

```bash
python -m src.maintenance.register_quick_clinic --output /private/private-quick-clinic.env
```

The output is exclusively created with mode 0600. Manually configure the backend API URL/key/client/tenant from it. Never use NEXT_PUBLIC_ for service credentials. Remove the legacy secret after consumers and old data have migrated.

## 5. Vector payload

Sanitized shape of an inspected synthetic server-Qdrant point (UUID suffixes omitted):

```json
{
  "client_id": "quick_clinic",
  "tenant_id": "quick_clinic_default",
  "collection_id": "patient_<sha256-prefix>_records",
  "owner_subject_id": "synthetic-patient-a",
  "source_system": "quick_clinic",
  "patient_id": "synthetic-patient-a",
  "document_id": "synthetic-document-a",
  "document_type": "LAB_REPORT"
}
```

All six generic/legacy scope fields were inspected and asserted against a real indexed synthetic point. Its collection matched the patient hash, with one matching point holding the unnamed dense vector and `bm25` sparse vector. No duplicate legacy/generic vector set or real patient data was used.

## 6. Integration evidence

| Area | Deterministic coverage | Live evidence |
|---|---|---|
| Upload / validation | PDF/JPEG/PNG/WebP, empty/malformed/MIME mismatch, auth, compensation | Real synthetic PDF storage and extraction succeeded; image OCR lifecycle pending |
| Index | Scope metadata mapping, index creation, deterministic IDs | Synthetic PDF reached READY and one chunk indexed |
| Search / hybrid / reranker | Mandatory filters, RRF/rerank interfaces, foreign chunk rejection | Legacy and generic contracts retrieved the same permitted synthetic document |
| Grounded answer / no evidence / citations | Provider contract and citation invariants | Real Cohere answer, valid page/chunk citations and missing-MRI refusal passed |
| Chat / structured+narrative | Routing, numeric observations, context isolation | Structured HbA1c, date follow-up and hybrid numeric/narrative checks passed |
| SSE | Incremental mocked chunks, content type, cancellation, completion | Real provider stream returned citations and completion; provider-error/cancellation remain mock-tested |
| Observations | Numeric types, dates and scope covered in suites | Real HbA1c 7.4 lookup and deletion cleanup passed |
| Delete / retry / reindex | Scoped deletion and changed/repeated vectors in Qdrant local engine | Real scoped deletion, changed-content reindex and repeated-index idempotency passed |
| Patient / doctor isolation | Quick-Clinic access guards and Search-Sphere pre-retrieval scope defenses | Full logged-in browser security matrix pending |
| Client isolation | Auth matrix, colliding IDs in Qdrant local engine | Real two-client colliding-ID lifecycle, foreign-owner rejection and revoked-token 401 passed |

Optional integration entry points:

- Quick-Clinic: `RUN_SEARCH_SPHERE_INTEGRATION=1 pnpm exec vitest run src/__tests__/integration/search-sphere-live.test.ts`; opt into actual provider calls with `RUN_SEARCH_SPHERE_COHERE_SMOKE=1`.
- Search-Sphere: `RUN_GENERIC_LIVE=1 pytest tests/test_live_generic_lifecycle.py` inside the isolated API runtime.
- `docker compose -p rag-compat-test -f compose.integration.yml up -d --build` uses dedicated synthetic DB, RabbitMQ, Qdrant and storage volumes. The integration Dockerfile uses an existing API base image; first build it from apps/api when absent. Supply provider credentials only through private environment configuration.

## 7. Search-Sphere results

- Complete Docker API pytest follow-up: **209 passed / 0 failed / 1 skipped / 1 warning, 27.87s**. The skip is the opt-in live lifecycle, separately passed. Alembic upgrade through 007 and migration tests passed. Previous Docker result was 208 passed; one hybrid-scope regression was added.
- Latest host run excluding unavailable Dramatiq test module: **204 passed, 2 skipped** (Alembic package missing on host; optional live test disabled).
- Complete Docker worker follow-up: **355 passed / 0 failed / 0 skipped / 1 warning, 41.95s**. Subsystem runs passed before the full run. Unit loader/task tests now isolate caches, use bounded mocks for non-model assertions, and own SQLite state. Real BM25/local-Qdrant retrieval remains tested; real dense/BM25/cross-encoder inference was exercised by the live lifecycle. Both Qdrant copies and payload-index edge cases passed.
- The original full-worker attempt was incomplete. The follow-up uses the integration image's PYTHONPATH, explicit SQLite test databases and one model-heavy run at a time. Running pytest inside the model-loaded live API twice caused confirmed OOM kills (exit 137) on the 4 GB Docker VM. Separate API test containers passed; the generic live driver passed on the host against real Docker services after waiting for API health.
- Web: **19 passed**; lint **0 errors / 4 warnings**. Default pnpm build failed with Turbopack OS permission error; `next build --webpack` passed.
- Baseline API run before fixes (excluding unavailable Dramatiq module): **181 passed / 1 failed**, reproducing native conversation ordering. Baseline worker had dependency/model and known payload-index failures; no clean full-worker baseline.

## 8. Quick-Clinic results

- Latest focused backend integration/auth/client suites: **143 passed in 12 files**.
- Previous full suite: **933 passed / 12 failed / 6 skipped**. Initial follow-up reproduced **932 passed / 18 failed / 1 skipped** because fixed-seed assumptions changed with shared database state. Final full suite against a fresh isolated PostgreSQL database: **951 passed / 0 failed / 1 skipped**, 174 files, 134.55s. One profile authorization test was added. The sole skip is the separately passed opt-in live test; the previous five Redis-suite setup skips were fixed with unique owned dataset IDs.
- Fixture fixes cover seeded login/profile/signup/doctors, OTP, notifications and logger tests. The stale Priya search assertion now queries its own Bhavna fixture with mixed case. Doctor-profile cleanup cannot issue unbounded deletes after failed setup. An intermediate run had transient infrastructure timeouts; all affected files and the final suite passed unchanged timeout assertions. The prior medical-search reset failure timed out awaiting initial results before reset; it did not reproduce in the final suite or 20 targeted runs, and component behavior was unchanged.
- pnpm type-check passed.
- pnpm lint: **0 errors / 1093 warnings**.
- pnpm build passed after the earlier interrupted attempt was restarted.
- Both opt-in Quick-Clinic live runs passed: retrieval-only (51.50s including startup/model warmup) and real Cohere answer/chat/SSE (14.41s). The generic Search-Sphere live lifecycle passed separately (initially 19.87s; final cleanup-safe rerun 9.50s, five host dependency warnings). One host attempt raced API startup and another saw a transient HTTP disconnect; the latter still cleaned up its clients after the fixture fix. Final rerun passed without changing retrieval or isolation assertions.
- Playwright was not run; logged-in browser/auth E2E remains unverified.

## 9. Evaluation

The preset-output evaluator ran against the same 30 synthetic cases on baseline and changes. Both report Recall@5 1.0000, Precision@5 0.7667, MRR 1.0000, nDCG@5 1.0000, citation validity 100%, no-evidence accuracy 100%, keyword coverage 93.5%. **These unchanged scores measure the deterministic evaluator, not live retrieval or Cohere quality.** Live benchmark regression remains unmeasured. The previously ignored synthetic JSON fixture is now included so the evaluator is reproducible in a fresh checkout.

## 10. Data migration and remaining risks

Apply Alembic 007 before enabling the updated adapters. Legacy DB records gain the default platform tenant, patient owner and hashed patient collection; custom existing collections are preserved and require review. Observation rows gain tenant scope. This security migration intentionally does not discard scope metadata on downgrade.

**Database migration does not modify Qdrant.** Prefer controlled reindexing when manageable. Alternatively inspect a dry run, then apply only an unambiguous payload plan:

```bash
python -m src.maintenance.backfill_vector_scope --client-id quick_clinic --tenant-id quick_clinic_default
# After reviewing a zero-ambiguity plan:
python -m src.maintenance.backfill_vector_scope --client-id quick_clinic --tenant-id quick_clinic_default --apply
```

Ambiguous/foreign/incomplete ownership is refused. The tool never deletes vectors or changes point IDs. Reindexing uses new scoped UUIDs and removes stale scoped chunks. Back up existing data and inspect payloads before the retrieval cutover; no deployed documents/vectors were migrated or silently deleted during this work.

Remaining deployment work: legacy secret still accepted; deployed client not provisioned; production legacy data/vector migration and browser E2E not performed. Live image OCR, provider-error/cancellation under real networking, and a live quality benchmark remain unverified. The old compatibility storage path lacks a tenant segment, so medical adapters deliberately permit only quick_clinic_default. Future clinic tenants use generic storage/API, not this legacy path. Synthetic backend verification is complete; deployed migration is not.

## 11. Migration recommendation

Keep compatibility endpoints temporarily. Classify medical-documents and medical-retrieval as deprecated generic-operation adapters; medical-observations and structured/hybrid medical chat/RAG are domain extensions. Move upload/register/status/delete/search to /api/v1 using the shared SDK only after legacy vector scope migration and complete live validation. Keep the Quick-Clinic backend adapter and medical safety routing. Generic answer migration requires preserving medical retrieval/summarization safety and structured numeric lookup; it must not introduce diagnosis, treatment or risk prediction. Improve SDK request IDs, retry/timeouts and error normalization before replacing the existing centralized wrapper. Do not duplicate vector sets during migration.
