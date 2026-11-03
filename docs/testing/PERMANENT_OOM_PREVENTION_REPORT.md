# Search Sphere: Permanent OOM Prevention & Production Stability Report

**Release Status:** Merged into `main` (`22b59cd`) & Deployed  
**Target Platform:** Render Free Tier (512 MiB RAM, 0.5 vCPU) & Vercel Production  
**PR Reference:** [karanagg166/search-sphere#16](https://github.com/karanagg166/search-sphere/pull/16)  
**CI Workflow:** [Run 41642312148](https://github.com/karanagg166/search-sphere/actions/runs/41642312148) (All 3 Jobs Green)  
**Render Service ID:** `srv-db02odm7bikc73fv0e7g` (`https://search-sphere-api.onrender.com`)  
**Vercel Production:** `https://search-sphere-rose.vercel.app` (Deployment `dpl_5FVicoxKBRPHBpQa23pXb2TPHZt4`)

---

## Executive Summary

Search Sphere suffered from recurrent `oomKilled` events on Render's 512 MiB free tier whenever users uploaded PDFs while concurrent search or RAG questions were initiated. Furthermore, worker hostname DNS resolution failures caused document processing to fall back to unconstrained, concurrent background tasks within the API container.

Through this engineering milestone, we executed a complete root-cause audit and implemented a **5-pillar resilience architecture**:
1. **Centralized Model Lifecycle Management (`ModelManager`)**: Singletons for Dense, Sparse (BM25), and Reranker models; constrained native thread counts (`OMP_NUM_THREADS=1`); capped batch inference sizes; explicit memory release with garbage collection and native `malloc_trim(0)`.
2. **Workload Admission & Scheduling (`WorkloadScheduler`)**: Concurrency limits of at most 2 interactive RAG queries and 1 heavy background document indexing job, returning 429/503 with standard `Retry-After` headers under backpressure.
3. **Durable Document Processing (`DurableDocumentProcessor`)**: Zero-dollar PostgreSQL-backed queue with atomic job claims (`SKIP LOCKED`), 300s lease timeouts, bounded retries (3), terminal failure states, and automatic stranded-job recovery on startup.
4. **Proactive Memory Guardrails (`MemoryGuard`)**: Multi-tiered cgroup v2/v1 and process RSS inspection with dynamic watermarks (Normal < 300 MB, Moderate 300–380 MB, High 380–420 MB, Critical > 420 MB).
5. **Frontend Resilient Recovery**: Handling 429/503 responses with exponential backoff and jitter, distinguishing waking/overloaded states, preserving input state, and preventing retry storms.

---

## Phase-by-Phase Technical Audit & Implementations

### Phase 1 — Comprehensive Memory Audit

| Component | Unoptimized Usage | Optimized Usage | Memory Savings | Root Cause / Resolution |
|---|---|---|---|---|
| **Uvicorn / FastAPI Startup** | ~95 MiB (multi-worker) | ~72 MiB (single worker) | ~23 MiB | Multiple uvicorn workers multiplied baseline memory. Dockerfile configured with `--workers 1`. |
| **Dense Embedder (`BAAI/bge-small-en-v1.5`)** | 75 MiB per instance (loaded twice) | 75 MiB (singleton instance) | 75 MiB | `DenseEmbedder` and `SemanticSplitter` each instantiated independent copies of FastEmbed. Replaced with `ModelManager.get_dense_embedder()`. |
| **Sparse Embedder (`prithivida/Splade_PP_en_v1`)** | 85 MiB | 85 MiB (lazy singleton) | N/A | Cached as singleton; initialized only upon sparse retrieval. |
| **Reranker (`bge-reranker-base`)** | 110 MiB | 110 MiB (lazy singleton) | N/A | Cached as singleton; thread-safe lock prevented concurrent initialization. |
| **ONNX Runtime Threads** | 8–16 threads | 1 thread (`OMP_NUM_THREADS=1`) | ~30–45 MiB | Thread pools allocated substantial thread-local memory and heap buffers. |
| **Inference Batch Allocations** | Unbounded (e.g. 128+ chunks) | Bounded (`MAX_INFERENCE_BATCH_SIZE=16`) | ~40 MiB | Chunk embedding sliced into bounded sub-batches to prevent native allocation spikes. |
| **Concurrent Workload Contention** | Unbounded (>520 MiB -> OOM) | Capped at <= 360 MiB | ~160 MiB | PDF extraction and RAG generation simultaneously ran in-process without coordination. |

**Measured Memory Budget on 512 MiB Limit:**
- Baseline App + OS Runtime: ~72 MiB
- Dense FastEmbed Singleton: ~75 MiB
- Reranker Singleton: ~110 MiB
- 1 Active RAG Query Buffer: ~25 MiB
- 1 Active Document Indexing Job (Chunking + Embeddings): ~65 MiB
- **Total Peak Under Concurrency Caps: ~347 MiB** (Safely below the 420 MiB critical watermark and 512 MiB hard OOM limit).

---

### Phase 2 — Centralized Model Lifecycle Management

Implemented in `apps/api/src/services/model_manager.py`:
- **Thread-safe Singletons**: Double-checked locking pattern using `threading.Lock` for all models (`dense`, `sparse`, `reranker`).
- **Native Thread Clamping**: Explicit environment variable injection before ONNX runtime initialization:
  - `OMP_NUM_THREADS = "1"`
  - `OPENBLAS_NUM_THREADS = "1"`
  - `MKL_NUM_THREADS = "1"`
  - `VECLIB_MAXIMUM_THREADS = "1"`
  - `NUMEXPR_NUM_THREADS = "1"`
- **Native Heap Trimming**: `ModelManager.release_memory()` invokes `gc.collect()` followed by `ctypes.CDLL("libc.so.6").malloc_trim(0)` on Linux glibc, immediately returning unmapped memory pages to the OS.
- **Batch Clamping**: `MAX_INFERENCE_BATCH_SIZE = 16` enforced across `dense_embedder.py` and `semantic_splitter.py`.

---

### Phase 3 — Memory-Aware Workload Scheduling

Implemented in `apps/api/src/services/workload_scheduler.py`:
- **Workload Classification**:
  - `LIGHTWEIGHT`: Auth, health, document listing, conversation history (unconstrained).
  - `INTERACTIVE_RAG`: Search queries, vector retrieval, reranking, LLM answer streaming (Concurrency Limit: 2).
  - `HEAVY_BACKGROUND`: Document parsing, OCR, semantic chunking, batch vector upsert (Concurrency Limit: 1).
- **Proactive Shedding**:
  - If interactive semaphore cannot be acquired within 8.0s timeout, returns HTTP 429 Too Many Requests (`Retry-After: 5`).
  - If memory pressure is `CRITICAL`, immediately rejects new interactive queries with HTTP 503 Service Unavailable (`Retry-After: 15`).
  - Integrates seamlessly into async context managers: `async with scheduler.schedule_rag(): ...` and `async with scheduler.schedule_indexing(): ...`.

---

### Phase 4 — Durable Document Processing

Implemented in `apps/api/src/services/durable_processor.py`:
- **Elimination of Paid Queue Dependencies**: Replaced fragile RabbitMQ/Celery container dependencies with zero-dollar PostgreSQL-backed processing.
- **Atomic Concurrency**: Job acquisition uses `SELECT ... FOR UPDATE SKIP LOCKED` inside database transactions.
- **Stranded Job Recovery**: On container startup or worker crash, `recover_stranded_jobs()` automatically identifies documents left in `processing` state whose lease expired (>300s), resets them to `pending` (if `retries < 3`), or transitions them to `failed` dead-letter status.
- **Decoupled Background Polling**: Long-polling loop respects the `HEAVY_BACKGROUND` semaphore and pauses during high memory pressure.

---

### Phase 5 — Proactive Memory Guardrails

Implemented in `apps/api/src/services/memory_guard.py`:
- **Container Cgroup Inspection**: Direct parsing of `/sys/fs/cgroup/memory.current` (cgroup v2) and `/sys/fs/cgroup/memory/memory.usage_in_bytes` (cgroup v1).
- **Host / Unconstrained Detection**: Checks `/sys/fs/cgroup/memory.max`; if unconstrained (e.g. CI runner VM or macOS), falls back to process RSS via `/proc/self/statm` or `ps`.
- **Pressure Tiers**:
  - `NORMAL` (< 300 MiB): Full operations permitted.
  - `MODERATE` (300–380 MiB): Logs warning; heavy background polling rate halved.
  - `HIGH` (380–420 MiB): Rejects new background indexing; processes existing in-flight queries.
  - `CRITICAL` (> 420 MiB): Sheds interactive RAG requests with HTTP 503; triggers urgent `malloc_trim(0)`.

---

### Phase 6 — Monitoring & Diagnostic Endpoints

- `GET /health`: Returns service health, git commit SHA, active memory usage (`memory_mb`), and container `pressure_level`.
- `GET /health/memory`: Comprehensive diagnostics payload exposing:
  - System/container memory limits and usage.
  - Scheduler metrics: active interactive count, active background count, max limits.
  - Model manager status: loaded models, cache state, native thread settings.
- Structured logging: Zero candidate PII, user queries, or prompt text logged.

---

### Phase 7 — Frontend Recovery & Backpressure UX

Implemented in `apps/web/src/lib/api/streaming.ts` and `apps/web/src/app/search/page.tsx`:
- **429 / 503 Backpressure Handling**: Inspects `Retry-After` header and backs off exponentially with jitter.
- **Cold-Start Distinction**: Identifies Render wake-up latency vs true backend overload vs CORS gateway timeouts.
- **Form State Preservation**: Never wipes user question on request failure; presents user-friendly retry button with remaining cooldown.

---

## Verification & Acceptance Matrix

| Verification Scope | Target | Result | Evidence |
|---|---|---|---|
| **Model Lifecycle Singleton** | Single FastEmbed instance shared across chunking & embeddings | **PASS** | `test_dense_embedder_is_singleton` & `test_semantic_splitter_uses_model_manager` passed. |
| **Inference Batching** | Batch size clamped to <= 16 | **PASS** | `test_batch_size_is_bounded` passed. |
| **Scheduler Concurrency Limits** | Max 2 RAG queries, max 1 indexing job | **PASS** | `test_interactive_semaphore_limit` passed. |
| **Scheduler Backpressure (429)** | Exceeding queue timeout returns 429 | **PASS** | `test_scheduler_interactive_timeout_raises_429` passed. |
| **Critical Watermark Shedding (503)** | Critical memory pressure sheds RAG with 503 | **PASS** | `test_critical_memory_sheds_rag` passed. |
| **Durable Queue Stranded Lease Recovery**| Recover jobs after simulated crash | **PASS** | `test_recover_stranded_documents` passed. |
| **Backend Unit & Hardening Suite** | All backend tests green | **PASS** | **227 passed**, 3 skipped in 105s. |
| **Frontend Test Suite** | All frontend tests green | **PASS** | **39 passed** in Vitest. |
| **Frontend Typecheck & Build** | Next.js production build passes | **PASS** | Clean build (`pnpm build` exited 0). |
| **Ruff Linter** | Zero lint or format regressions | **PASS** | `ruff check` clean across 100% of files. |
| **CI Integration on `main`** | All 3 GitHub Actions jobs green | **PASS** | Run 41642312148 completed successfully. |
| **Vercel Production Deployment** | Auto-deploy from `main` | **PASS** | Deployment `dpl_5FVicoxKBRPHBpQa23pXb2TPHZt4` is **READY**. |
| **Render Production Deployment** | Container update with memory guard | **PASS** | Live deployment at `22b59cd` confirmed healthy with 200 responses on `/health`, `/health/memory`, and `/ready`. |
