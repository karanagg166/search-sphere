# Final Production Safety Hardening Implementation Plan

> **For Claude / Antigravity:** Follow task-by-task execution with TDD and verified commits.

**Goal:** Close fixture cleanup vulnerabilities, distributed lease race conditions during container restarts, and raw memory admission risks under a strict $0 budget.

**Architecture:** 
- Robust multi-page manifest-backed fixture lifecycle in `scripts/ci_production_smoke.py` with account ownership checks, provenance validation, and retryable deletion.
- Distributed lease locking in PostgreSQL via `DurableDocumentProcessor` with worker identity (`worker_id`), atomic CAS updates, heartbeats, and safe expired-only lease recovery (eliminating `force_all=True` race conditions during deployment handoffs).
- Hard container raw memory guardrails in `MemoryGuard` to prevent OOM termination at ~497 MiB regardless of reclaimable page cache.
- Full provenance-backed historical data reconciliation manifest distinguishing verified user documents from test accounts.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy (Asyncpg), PostgreSQL (Supabase), pgvector, Linux cgroups v1/v2.

---

### Task 1: Make CI Fixture Cleanup Provably Safe

**Files:**
- Modify: `scripts/ci_production_smoke.py`
- Create / Test: `apps/api/tests/test_ci_smoke_fixture_safety.py`

**Key Improvements:**
1. Dedicated test-account ownership check: strictly only purge documents owned by the active CI test account (`PROD_SMOKE_USER_EMAIL` or test auth user ID). Never touch documents belonging to other users.
2. Unique CI run identifier (`CI_RUN_ID = f"run_{int(time.time())}_{uuid.uuid4().hex[:6]}"`) embedded in document metadata / title / filename.
3. Persistent fixture manifest on disk (`.ci_smoke_manifest.json`) recording run ID, document ID, upload timestamp, and cleanup status. Survives script interruptions.
4. Provenance validation: check that the candidate document matches the active test user ID AND has provenance markers in the title / metadata / manifest before attempting DELETE.
5. Safe pagination: iterate through all pages of documents (`/documents?page=N&limit=50`) instead of assuming all documents fit in page 1.
6. Snapshot iteration: iterate over `list(SYNTHETIC_DOCUMENT_IDS)` and handle partial failures gracefully with retry logic and explicit status validation (verifying HTTP 200/204/404 AND vector query check).

---

### Task 2: Fix Restart-Safe Document Leases & Distributed Recovery

**Files:**
- Modify: `apps/api/src/services/durable_processor.py`
- Test: `apps/api/tests/test_durable_processor_recovery.py`

**Key Improvements:**
1. Unique worker identity: Each processor instance generates a unique `worker_id` (e.g. `w_...`).
2. Structured lease token: `proc:{worker_id}:{expires_epoch}:{attempt}`.
3. Eliminate `force_all=True` on startup: startup recovery MUST only reclaim expired leases (`now > expires_epoch` or `updated_at < now - LEASE_TIMEOUT`), allowing overlapping deployment instances (e.g. container A finishing a job while container B boots) to operate concurrently without lease theft.
4. Compare-and-Set updates: All state transitions (`claim_next_job`, `recover_stranded_jobs`, `process_job` completion, `_mark_failure`, and heartbeats) strictly enforce `where Document.id == doc_id and Document.status == expected_lease`.
5. Heartbeat loop: A background asyncio task refreshes the lease expiration every 20 seconds during processing. If CAS fails (lease was stolen or expired), processing aborts immediately.
6. Idempotent indexing: Chunks for `document_id` are deleted before re-indexing in vector storage.

---

### Task 3: Verify Raw Memory Safety & Conservative Workload Admission

**Files:**
- Modify: `apps/api/src/services/memory_guard.py`
- Test: `apps/api/tests/test_memory_guard_raw_safety.py`

**Key Improvements:**
1. Distinguish raw container memory (`raw_cgroup_mb`) from working set memory (`estimated_working_set_mb`).
2. Hard container headroom limit: Regardless of inactive file page cache, if `raw_cgroup_mb` exceeds `platform_limit_mb - 35.0` (i.e. >= 477 MiB in a 512 MiB container) or container headroom is <= 35 MiB, heavy workloads (PDF extraction, OCR, batch embedding) are BLOCKED.
3. Interactive RAG queries are blocked if raw container memory exceeds `platform_limit_mb - 15.0` (>= 497 MiB) or container headroom is <= 15 MiB.
4. Pressure escalation: If `raw_cgroup_mb >= 490.0` or container headroom <= 22 MiB, memory pressure escalates to `high` or `critical` even if page cache claims to be reclaimable.
5. Keep `/health` and diagnostic endpoints lightweight (no expensive filesystem traversal; cached metrics).

---

### Task 4: Historical Data Reconciliation Manifest (Owner Approval Batch)

**Files:**
- Update: `docs/acceptance/historical-data-safety-and-recovery.md`
- Script: Read-only reconciliation audit script verifying exact account ownership and storage references for all 722 document records and 560 external document records.

**Key Deliverables:**
- Explicit separation of genuine user documents (under `aggarwalkaran241@gmail.com`) from test accounts (`*@testsphere.io`, `*@example.com`).
- Storage object verification for all candidate records.
- Cleanup manifest formatted for owner approval with rollback instructions. Zero destructive SQL execution.

---

### Task 5: Production Validation & Delivery

- Local test execution of all unit & hardening suites.
- Push branch `feat/final-production-safety-hardening`.
- Open PR against `main`.
- Ensure all CI workflows pass (Backend, Frontend, Worker, Vercel).
- Merge PR and monitor Render production deployment.
- Execute live production smoke test and confirm clean zero-leakage run.
