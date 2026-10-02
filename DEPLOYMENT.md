# Search Sphere Production Deployment Guide & Runbook

This guide describes how to configure, migrate, build, deploy, and operate Search Sphere in a production environment.

> [!IMPORTANT]
> **Deployment Status**: Search Sphere is **deployment-ready** — the container images, environment schema, Alembic migrations, health probes, security controls, and integration test suites are verified. It is not currently deployed to a public cloud URL. This document details the exact operational procedures to deploy it to any production cloud container orchestrator (e.g., AWS ECS/EKS, GCP Cloud Run, Kubernetes, or Docker Compose on a hardened Linux VPS).

---

## 1. System Architecture

```text
       [ Clients / Browsers ]
                 │
                 ▼ HTTPS (TLS Termination)
        [ Reverse Proxy / Ingress / Cloudflare ]
                 │
      ┌──────────┴──────────┐
      ▼                     ▼
[ Next.js Web ]       [ FastAPI API ]
 (Port 3000)           (Port 8000)
                            │
        ┌───────────────────┼────────────────────┬─────────────────┐
        ▼                   ▼                    ▼                 ▼
 [ PostgreSQL DB ]   [ Qdrant Vector ]    [ RabbitMQ Broker ] [ Object Storage ]
 (Supabase/Managed)  (Hybrid Search)      (CloudAMQP/Cluster) (Supabase/S3)
        ▲                   ▲                    ▲                 ▲
        │                   │                    │                 │
        └───────────────────┴─────────┬──────────┴─────────────────┘
                                      │
                              [ Dramatiq Worker ]
```

---

## 2. Required Production Services

| Service | Component Role | Recommended Production Options |
| :--- | :--- | :--- |
| **Next.js Web** | Frontend UI & SSR | Vercel, AWS ECS/Amplify, Docker on Kubernetes |
| **FastAPI API** | REST API & Task Dispatcher | AWS ECS, GCP Cloud Run, DigitalOcean App Platform, Kubernetes |
| **Dramatiq Worker** | Asynchronous Document Ingestion | AWS ECS, Railway, Kubernetes (HPA-scalable) |
| **PostgreSQL** | Relational metadata, auth, conversations | Supabase Managed Postgres, AWS RDS Aurora (v16+) |
| **Qdrant** | Dense vector + BM25 sparse index | Qdrant Cloud Managed Cluster or Self-hosted Qdrant |
| **RabbitMQ** | Message broker for task queue | CloudAMQP or Managed RabbitMQ Cluster |
| **Redis** | Ephemeral caching & rate limiting | Upstash Redis, AWS ElastiCache, or Redis Cloud |
| **Object Storage** | Raw PDF document persistence | Supabase Storage or AWS S3 |
| **Cohere API** | Query rewrite & grounded RAG synthesis | Managed Cohere API (`command-a-03-2025`) |

---

## 3. Environment Configuration

All environment variables should be injected securely via secrets managers (AWS Secrets Manager, GCP Secret Manager, or container orchestrator secret variables).

### Critical Secrets & Settings

| Variable | Description | Production Requirement |
| :--- | :--- | :--- |
| `ENVIRONMENT` | Runtime mode | Must be set to `production` |
| `JWT_SECRET_KEY` | HMAC-SHA256 signature key | **Must be 32+ random characters**. Default keys are strictly rejected at startup. |
| `ALLOWED_ORIGINS` | Trusted frontend domains for CORS | `["https://app.yourdomain.com"]` (no wildcards) |
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://...` (with SSL enabled) |
| `DATABASE_URL_SYNC` | Sync PostgreSQL connection string | `postgresql://...` (used for Alembic migrations) |
| `COHERE_API_KEY` | Cohere API authentication key | Required for production query rewrite & RAG |
| `QDRANT_URL` & `QDRANT_API_KEY` | Managed Qdrant vector database URL/key | Required |
| `RABBITMQ_URL` | AMQP broker connection URL | Required with SSL (`amqps://...`) |
| `REDIS_URL` | Redis cache URL | `redis://...` or `rediss://...` |
| `RATE_LIMIT_ENABLED` | Global rate limiting flag | `true` |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | Per-client limit for expensive endpoints | `120` |

Generate a cryptographically secure JWT secret:
```bash
openssl rand -hex 32
```

---

## 4. Startup Order

To prevent connection failures and startup races, start services in this strict order:

```text
Step 1: PostgreSQL + Qdrant + RabbitMQ + Redis (Infrastructure Tier)
   ↓
Step 2: Database Schema Migration (`alembic upgrade head`)
   ↓
Step 3: Dramatiq Worker Service
   ↓
Step 4: FastAPI Backend Service (Verify /ready probe)
   ↓
Step 5: Next.js Frontend Web Service
```

---

## 5. Database Schema Migrations

Production does **not** rely on runtime `Base.metadata.create_all()`. Schema evolution is strictly managed by **Alembic**.

### Running Migrations

Before starting or updating the API and Worker containers, run:
```bash
# In container or deployment CI/CD pipeline
cd apps/api
alembic upgrade head
```

Or via the Makefile / Docker:
```bash
make migrate
# or: docker compose exec api alembic upgrade head
```

### Inspecting Current Schema Revision
```bash
docker compose exec api alembic current
```

---

## 6. Container Images & Production Commands

### Building Production Images

```bash
# Build FastAPI backend image
docker build -t search-sphere-api:latest -f apps/api/Dockerfile apps/api

# Build Dramatiq worker image
docker build -t search-sphere-worker:latest -f apps/worker/Dockerfile apps/worker

# Build Next.js frontend image
docker build -t search-sphere-web:latest -f apps/web/Dockerfile apps/web
```

### Production Commands

* **FastAPI Backend**:
  ```bash
  uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4 --no-access-log
  ```
* **Dramatiq Worker**:
  ```bash
  dramatiq --processes 4 --threads 4 src.main
  ```
* **Next.js Frontend**:
  ```bash
  pnpm build && pnpm start
  ```

---

## 7. Health and Readiness Probes

Search Sphere exposes dedicated HTTP probes for load balancers (AWS ALB, Nginx, Traefik) and container orchestrators:

- **Liveness Probe**: `GET /health`
  - Returns `200 OK` (`{"status": "ok"}`) if the process is alive.
  - Used for container health and restart policies.
- **Readiness Probe**: `GET /ready`
  - Validates active database connectivity with `SELECT 1`.
  - Returns `200 OK` (`{"status": "ready"}`) when traffic can safely be routed.
  - Returns `503 Service Unavailable` if database is down or connection pool is exhausted.

---

## 8. Security & Hardening Controls

1. **Strict CORS**: In `ENVIRONMENT=production`, only explicit origins in `ALLOWED_ORIGINS` are accepted. Wildcards (`*`) with credentials are completely prohibited.
2. **Rate Limiting**: Applied to expensive endpoints (`/search`, `/answer`, `/answer/stream`, `/conversations/{id}/answer`, `/documents`). Returns HTTP `429 Too Many Requests` with a `Retry-After` header.
3. **Request Correlation IDs**: Every request receives or passes through an `X-Request-ID` header, automatically bound to all `structlog` log messages.
4. **Input Size & Context Limits**:
   - PDF upload capped at 20MB (`MAX_UPLOAD_SIZE_BYTES`).
   - Query length capped at 1,000 characters (`MAX_QUERY_LENGTH`).
   - Conversation context message limit (`CONVERSATION_MAX_HISTORY_MESSAGES=10`).
5. **No Leaked Stack Traces**: Global exception handler masks internal database and framework tracebacks from clients while recording full context with the `request_id`.

---

## 9. Release & Smoke Test Checklist

Follow this 12-step verification protocol for any staging or production release:

1. [ ] **Configure Environment**: Verify all production environment variables and API keys are injected.
2. [ ] **Start Infrastructure**: Ensure PostgreSQL, Qdrant, RabbitMQ, and Redis instances are reachable.
3. [ ] **Run Migrations**: Execute `alembic upgrade head` and verify `alembic current` matches the head revision.
4. [ ] **Start Worker**: Launch the Dramatiq worker process and verify successful connection to RabbitMQ.
5. [ ] **Start API**: Launch the FastAPI backend container.
6. [ ] **Start Frontend**: Build and launch the Next.js frontend application.
7. [ ] **Verify Liveness**: Send `GET /health` -> HTTP 200 `{"status": "ok"}`.
8. [ ] **Verify Readiness**: Send `GET /ready` -> HTTP 200 `{"status": "ready"}`.
9. [ ] **Upload Test Document**: Upload a sample PDF via the UI or `POST /documents`. Verify worker queues and finishes ingestion.
10. [ ] **Ask Test Question**: Submit a query via `/conversations/{id}/answer/stream` or `/answer`.
11. [ ] **Verify Citations & Groundedness**: Ensure the assistant response references source citations with valid chunk/page metadata.
12. [ ] **Verify Follow-up & Isolation**: Ask a follow-up pronoun question (confirming query rewriter context), and test another user account to verify strict tenant isolation.

---

## 10. Operations Runbook & Incident Response

### Issue: API returns 503 on `/ready`
- **Cause**: PostgreSQL unreachable or connection pool exhausted.
- **Remediation**:
  1. Check database instance CPU/RAM utilization and active connections.
  2. Verify network security groups allow ingress on port 5432.
  3. Inspect logs for `request_id` or grep for `"Readiness check failed"`.

### Issue: Users receive HTTP 429 Too Many Requests
- **Cause**: Client IP or user exceeded rate limit quota.
- **Remediation**:
  1. Inspect `Retry-After` response header indicating wait duration.
  2. If legitimate traffic spikes occur, tune `RATE_LIMIT_REQUESTS_PER_MINUTE` in configuration.

### Issue: Ingestion Queue Backlog
- **Cause**: Large batch upload volume or worker node crash.
- **Remediation**:
  1. Check RabbitMQ queue depth in the RabbitMQ management dashboard.
  2. Scale worker processes horizontally: `docker compose up -d --scale worker=4` (or scale container replicas in your cloud orchestrator).
