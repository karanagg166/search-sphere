# Acceptance Report: Search Sphere Historical Data Safety & Recovery

**Date:** 2026-11-05  
**Implementation Branch:** `feat/historical-data-safety-and-recovery`  
**Base Commit:** `c88e5bf183d599d624318593b35624b4acc664a7`  
**Status:** COMPLETE (All 5 Tasks Verified)

---

## 1. Executive Summary

This milestone delivers production data safety hardening, historical record reconciliation, fixture lifecycle containment, and worker recovery enhancements for Search Sphere's PostgreSQL + pgvector architecture under the strict $0 infrastructure budget.

Key accomplishments:
1. **Backfill CLI Safety Hardening**: Flipped `scripts/backfill_pgvector_chunks.py` to default to safe, read-only audit mode. Live writes now strictly require `--execute` accompanied by bounded scope controls (`--limit`, `--max-documents`, or `--document-id`). Unbounded live invocations abort immediately with exit code 2. Logs explicitly distinguish `[PROPOSED]` dry-run intentions from `[COMMITTED]` database operations.
2. **Historical Document Audit & Storage Probe**: Audited all 722 `documents` and 560 `external_documents` records using read-only SQL and object-storage probes. Categorized records into genuine recoverable user files (5), missing files (17), and synthetic test fixtures (700 in `documents`, 560 in `external_documents`). No data was deleted or marked failed during the audit.
3. **CI Synthetic Fixture Lifecycle Containment**: Eliminated the root cause of historical test accumulation in `scripts/ci_production_smoke.py`. Document IDs are now registered immediately upon upload, guaranteed to be purged in `finally` handlers even if polling or assertion failures occur, and automatically swept prior to new test runs.
4. **Worker Queue & RabbitMQ Warning Elimination**: Resolved the alarming `Could not initialize RabbitMQ broker for Dramatiq: [Errno -2] Name or service not known` startup log by configuring `ENABLE_RABBITMQ: bool = False` as default and gracefully identifying unconfigured Docker Compose host placeholders in production.
5. **Durable Processor Crash & Restart Recovery**: Upgraded `DurableDocumentProcessor` with instant restart recovery (`force_all=True` on server boot) and periodic 60s idle sweeps, ensuring interrupted document leases survive container restarts and resume automatically without waiting for full lease timeouts.

---

## 2. Historical Document Reconciliation Breakdown

### Database Record Inventory
- **Total `documents` rows:** 722
  - `indexed`: 657
  - `failed:missing_storage_file`: 36
  - `failed`: 25
  - `failed:max_retries_exceeded`: 4
- **Total `external_documents` rows:** 560
  - `READY`: 412
  - `FAILED`: 135
  - `QUEUED`: 12
  - `PROCESSING`: 1
- **Current `document_chunks` rows in pgvector:** 0 (all pre-pgvector documents await bounded backfill)

### Physical Storage Probe Results
Every non-synthetic record was individually probed against Supabase Object Storage (`documents` bucket):

#### Genuine Recoverable Documents (Physical PDF Exists in Storage)
| Document ID | Filename | Size (Bytes) | Owner Email | DB Status | Storage Key |
|---|---|---|---|---|---|
| `67476e61-22ff-4cc7-8d6e-1ad1f5b92546` | `HarshMishra_Resume.pdf` | 128,421 | `aggarwalkaran241@gmail.com` | `indexed` (Qdrant-era) | `documents/a353db5c-1b50-4549-8bf7-046d3e1adb26/67476e61-22ff-4cc7-8d6e-1ad1f5b92546.pdf` |
| `557522fc-33f5-4cc9-a4a6-ab52408d798a` | `HarshMishra_Resume.pdf` | 128,421 | `aggarwalkaran241@gmail.com` | `failed:max_retries_exceeded` | `documents/a353db5c-1b50-4549-8bf7-046d3e1adb26/557522fc-33f5-4cc9-a4a6-ab52408d798a.pdf` |
| `f304fe09-21e1-4298-9be5-c846890e0a79` | `karan_cv.pdf` | 132,922 | `aggarwalkaran241@gmail.com` | `failed` | `documents/a353db5c-1b50-4549-8bf7-046d3e1adb26/f304fe09-21e1-4298-9be5-c846890e0a79.pdf` |
| `c3e175b7-1120-4595-850b-5329f821ddbe` | `multimodal_doc.pdf` | 229,174 | `extraction_test@example.com` | `failed` | `documents/0ffef71b-fef6-4d05-af04-30ad9a0b4264/c3e175b7-1120-4595-850b-5329f821ddbe.pdf` |
| `a6be03cb-4391-4565-9b07-5d0eec3f2eb6` | `rabbitmq_test.pdf` | 79 | `rabbitmq_test_64edcc55@testsphere.io` | `indexed` | `documents/67435345-3e2b-4844-85e8-194acf7b1f1e/a6be03cb-4391-4565-9b07-5d0eec3f2eb6.pdf` |

#### Missing Physical Files (17 Documents)
- **15 `handbook.pdf` records**: Created under ephemeral `e2e_explorer_*@example.com` accounts during frontend browser test journeys where file storage was omitted or cleaned up.
- **2 Demo Whitepapers**:
  - `cbc94c4b-afd1-4703-bd97-bc2ae9c06895` (`Enterprise_Security_Whitepaper.pdf`)
  - `a12f9ef7-fed0-4044-876f-5604cc728204` (`Cloud_Native_Architecture_Specification.pdf`)
  Created under `demo.google@searchsphere.io` during seeding; physical objects do not exist in storage.

#### Verified Synthetic Test Records (1,260 Total)
- **700 records in `documents`**: Matches `filename LIKE 'ci_smoke%' OR filename LIKE 'test%' OR filename LIKE 'mock%' OR filename = 'sample.pdf' OR file_url LIKE '%test%' OR storage_key LIKE '%test%' OR storage_key LIKE '%mock%'`.
- **560 records in `external_documents`**: Integration test mocks under source systems `quick_clinic` (407 medical lab mock paths), `client_a_*` (multi-tenant boundary tests), `exam_arena_*` (school test mocks), and `path/*` (unit fixtures).

---

## 3. Synthetic Fixture Cleanup Manifest

> [!IMPORTANT]
> In accordance with repository guidelines, **zero records or storage objects were deleted or mutated during this audit**. The following manifest identifies candidate artifacts verified as disposable synthetic test clutter for future owner-approved purge batches.

### Cleanup Batch Candidates:
1. **User Documents (`documents` table)**:
   - 700 synthetic test rows owned by mock test users (`ci_smoke_*`, `test_*`, `mock_*`).
   - Query filter: `filename LIKE 'ci_smoke%' OR filename LIKE 'test%' OR storage_key LIKE '%test%' OR storage_key LIKE '%mock%'`.
   - Associated storage objects: Mock URLs (`http://test/storage/...`), no external S3/Supabase footprint.
2. **External Documents (`external_documents` table)**:
   - 560 mock external document rows from tenant and integration test suites.
   - Storage existence verified: 0 external physical files exist; all storage paths are fictional fixture paths (`medical-documents/pat-*/bp_normal.pdf`, `path/1.pdf`).
3. **Rollback & Preservation Plan**:
   - Before executing any future purge, export database dump: `pg_dump --data-only -t documents -t external_documents > backup_pre_cleanup.sql`.
   - Execute deletion exclusively via transactional SQL batches bounded by tenant and source IDs.

---

## 4. Test Verification Matrix

| Verification Target | Requirement | Result | Evidence |
|---|---|---|---|
| Backfill CLI Default Mode | Must default to dry-run (audit only) without arguments | **PASS** | `test_default_audit_mode_no_flags`, verified `dry_run=True` |
| Backfill CLI Live Execution Gate | `--execute` without bounded scope (`--limit` or `--document-id`) must abort | **PASS** | Exits code 2 with `Safety error: Live execution requires explicit bounded scope` |
| Backfill CLI Bounded Scope | `--execute --limit N` and `--execute --document-id ID` permitted | **PASS** | `test_execute_with_limit_allows_live_mode`, `test_execute_with_document_id_allows_live_mode` |
| Backfill CLI Proposed vs Committed | Logs distinguish `[PROPOSED]` vs `[COMMITTED]` | **PASS** | `test_audit_and_backfill_dry_run_never_commits_or_indexes` logs `[PROPOSED]` |
| Backfill CLI Zero-Mutation in Dry Run | Dry run never commits SQL transaction or calls `index_document` | **PASS** | `session.commit` and `vector_store.index_document` assert not called |
| CI Smoke Fixture Immediate Registration | Synthetic document ID tracked immediately upon upload | **PASS** | `SYNTHETIC_DOCUMENT_IDS.append(doc_id)` executed before polling |
| CI Smoke Fixture Guaranteed Cleanup | Cleanup called in `finally` even if polling or assertions fail | **PASS** | `finally: for pending_id in list(SYNTHETIC_DOCUMENT_IDS): cleanup_synthetic_document(...)` |
| CI Smoke Pre-test Sweep | Interrupted prior test artifacts purged before test begins | **PASS** | `sweep_dangling_synthetic_documents()` purges dangling `ci_smoke_test.pdf` |
| RabbitMQ DNS Warning Silencing | No `Name or service not known` warning when unconfigured | **PASS** | `_is_rabbitmq_enabled()` check logs info cleanly when disabled |
| Durable Processor Crash Recovery | Interrupted leases recovered immediately on API container boot | **PASS** | `recover_stranded_jobs(force_all=True)` tests in `test_durable_processor_recovery.py` |
| Max Retries Dead-Lettering | Leases exceeding 3 retries marked `failed:max_retries_exceeded` | **PASS** | `test_recover_stranded_jobs_marks_terminal_when_max_retries_exceeded` |
| Local Test Suite | All core unit & integration tests pass | **PASS** | 40 passed in 97.72s (`apps/api/tests/`) |

---

## 5. Reproduction & Validation Commands

```bash
# 1. Run backfill CLI safety unit tests
PYTHONPATH=.:apps/api python3.14 -m pytest apps/api/tests/test_backfill_cli_safety.py -v

# 2. Run durable processor restart recovery tests
PYTHONPATH=.:apps/api python3.14 -m pytest apps/api/tests/test_durable_processor_recovery.py -v

# 3. Test backfill CLI in safe audit mode (default)
PYTHONPATH=apps/api python3.14 scripts/backfill_pgvector_chunks.py --limit 2

# 4. Verify safety check blocks unbounded live execution
PYTHONPATH=apps/api python3.14 scripts/backfill_pgvector_chunks.py --execute
# Expected: Exit code 2, error logged

# 5. Lint and formatting check
python3.14 -m ruff check scripts/backfill_pgvector_chunks.py scripts/ci_production_smoke.py apps/api/src/
```
