# Canonical hosted runtime

The API and ingestion/cleanup worker use `apps/api/src`. Production dependencies
come from `apps/api/requirements-production.txt`. The worker image builds from the
same context and runs `python -m src.services.ingestion_worker` instead of Uvicorn:

```sh
docker build -f apps/worker/Dockerfile -t search-sphere-worker:local apps/api
```

Apply Alembic migrations before starting an independent worker. `docker compose`
does this through API startup and waits for API health. Its PostgreSQL image includes
pgvector. API and worker share Supabase/private Storage and Cohere policy/budget
configuration through the server environment. Do not print rendered environment
configuration or put credentials in image build arguments.

`RUN_INGESTION_WORKER=true` (the default) runs the worker inside the API process.
The existing free Render service uses that topology; this change creates no extra
Render service. For independent capacity, disable the in-process worker on API
instances and run the worker command separately against the same PostgreSQL queue.
SKIP LOCKED claims, bounded concurrency, leases/generation fences, retries and cleanup
remain the same code. Separate process capacity is a deployment option, not a claim
that a paid Render background-worker instance fits the $0 budget.

Redis, RabbitMQ and Qdrant containers remain available only under Compose's optional
`legacy` profile. They are not dependencies of the canonical path. The historical
`apps/worker/src/main.py` entry point no longer registers RabbitMQ actors. Old local
model/OCR/provider SDK requirements are removed from the worker runtime manifest.
Historical modules/tests remain for reference; production does not import them.

## Verification

`python scripts/run_worker_checks.py` explicitly selects the API pytest configuration,
canonical import path and importlib collection mode. The worker's old pytest path
setting otherwise silently selects its legacy `src` package. A required regression
asserts actual module paths and absence of Torch, FastEmbed, Transformers, local OCR
and Docling packages. The preserved deterministic PDF/cleaning/chunking tests plus
hosted-adapter/token-limit checks pass: 96 tests. Durable delivery, retry, restart,
scope isolation, atomic reindex and deletion checks remain in the separate required
real PostgreSQL/pgvector CI gate.

CI builds both process images and runs the image checks with networking disabled
and a 512 MiB limit. The local worker-image import/tokenizer/health check passed at
182.4 MiB peak RSS with no model weights, local inference packages or environment
file in the image. This measures offline import/health, not ingestion peak capacity,
query throughput or P95 latency. Broader measured capacity and live cold starts remain
in the rollout ledger. Provider approval, the confirmed trial key, quotas, and
fail-closed sensitive-data rules apply equally to both process roles.
