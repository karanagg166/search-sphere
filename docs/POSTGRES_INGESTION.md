# Durable PostgreSQL ingestion

All three document entry points register a PostgreSQL outbox row in the same
transaction as document metadata. The original object remains private. No RabbitMQ
service, model weights, local inference or paid worker is required.

| Client path | Registration | Authorized polling |
| --- | --- | --- |
| Standalone | `POST /documents` | `GET /documents/{id}` |
| Generic clients, including ExamArena | `POST /api/v1/documents/upload`, then `POST /api/v1/documents` | `GET /api/v1/documents/{id}` |
| Quick-Clinic compatibility | `POST /internal/medical-documents`, then `POST /internal/medical-documents/{id}/ingest` | `GET /internal/medical-documents/{id}/status` |

Every status response can include `job`: status, stage, attempts, generation,
registration/updated timestamps, queue age and a server-owned error code. Queue age
continues while work is pending and stops at the terminal update. A document can
remain READY/indexed while a replacement attempt fails: its previous usable index
is preserved, and `job.status=FAILED` describes the failed attempt.

Duplicate registration for the same client/tenant/external identity returns the
existing state and does not reset the job. An explicit medical retry sends
`forceReindex: true`; generic retry sends `reindex: true`; standalone uses
`POST /documents/{id}/reindex`. Reindex invalidates the previous lease. Changing
already indexed source metadata is rejected until the document-version replacement
feature is available. Do not overwrite an indexed original as a substitute for
that versioned operation.

Claims use PostgreSQL row locks and SKIP LOCKED. Each claim has an opaque stable
token and a 120-second lease, refreshed every 20 seconds. Expired claims recover
after process loss; live claims survive handoff. There are at most three attempts
with delayed retries. Permanent provider rejection, exhausted trial quota,
unapproved data, invalid/empty input and removed documents terminate with safe
codes. No raw provider response, key or document text is stored in job errors.

Publication locks the current lease and commits chunks, extracted text, structured
medical observations and completion together. A stale worker cannot publish after
reindex/revocation. The operator-owned classification policy is rechecked before
each hosted request and before publication. Unapproved medical/confidential data
fails before download or model inference. Legacy pending adoption is bounded to
20 records per kind per minute, skips live leases and retains original queue age.
It does not automatically reindex records merely marked indexed with no vectors;
that recovery requires a scoped, bounded dry run.

The API currently starts one ingestion loop. `RUN_INGESTION_WORKER=false` runs an
API-only role; `python -m src.services.ingestion_worker` runs the worker role using
the same API image, SQL, private Storage and hosted-provider configuration. These
roles can scale independently when an authorized budget supports it. This rollout
creates no paid background-worker resource: the current free web service runs API
and one ingestion slot together. Model HTTP is limited to two concurrent requests
per process and shares the PostgreSQL trial quota across roles.

The initial parser limits PDFs to 100 pages, and the hosted pipeline bounds chunks
to 1,000 per document. These are input limits, not measured capacity claims. The
350-target/400-maximum coarse token setting still needs the required hosted-token
and layout benchmark before cutover. No production latency, full hosted throughput
or three-app live journey is implied by the local integration tests.
