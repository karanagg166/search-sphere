# Search Sphere Production Deployment Guide & Runbook

This guide describes how to build, harden, deploy, and operate Search Sphere in a production environment.

---

## 1. System Architecture

```text
       [ Clients / Browsers ]
                 │
                 ▼ HTTPS
        [ Reverse Proxy / Ingress ]
                 │
      ┌──────────┴──────────┐
      ▼                     ▼
[ Next.js Web ]       [ FastAPI API ]
 (Port 3000)           (Port 8000)
                            │
        ┌───────────────────┼────────────────────┬─────────────────┐
        ▼                   ▼                    ▼                 ▼
 [ PostgreSQL DB ]   [ Qdrant Cloud ]     [ RabbitMQ Broker ] [ Cloud Storage ]
 (Supabase/Managed)  (Hybrid Vector DB)   (CloudAMQP)         (Supabase S3)
                            ▲
                            │ tasks
                    [ Dramatiq Worker ]
```

---

## 2. Environment Configuration

In production, create a secure `.env` file (or inject environment variables into your container orchestrator).

### Critical Secrets & Values

| Variable | Description | Production Requirement |
| :--- | :--- | :--- |
| `ENVIRONMENT` | Deployment environment | Must be `production` |
| `JWT_SECRET_KEY` | HMAC-SHA256 signature key | **Must be 32+ random characters**. Default keys are strictly rejected at startup. |
| `ALLOWED_ORIGINS` | Trusted frontend domains for CORS | `["https://app.yourdomain.com"]` |
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://...` (with SSL) |
| `COHERE_API_KEY` | LLM Key for query rewrite & answer synthesis | Required in production |
| `QDRANT_URL` & `QDRANT_API_KEY` | Managed Qdrant vector database | Required |
| `RABBITMQ_URL` | Broker AMQP URL | Required with SSL (`amqps://...`) |
| `RATE_LIMIT_ENABLED` | Global rate limiting flag | `true` |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | Per-client limit for expensive endpoints | `120` |

Generate a cryptographically secure JWT secret:
```bash
openssl rand -hex 32
```

---

## 3. Container Images & Build

### Building Images

All services have multi-stage, production-ready Dockerfiles:

```bash
# Build FastAPI backend
docker build -t search-sphere-api:latest -f apps/api/Dockerfile apps/api

# Build Dramatiq worker
docker build -t search-sphere-worker:latest -f apps/worker/Dockerfile apps/worker

# Build Next.js frontend
docker build -t search-sphere-web:latest -f apps/web/Dockerfile apps/web
```

### Production Execution

For production deployments (Kubernetes, AWS ECS, GCP Cloud Run, or Docker Compose):

**FastAPI Production CMD**:
```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4 --no-access-log
```

**Worker Production CMD**:
```bash
dramatiq --processes 4 --threads 4 src.main
```

---

## 4. Health and Readiness Probes

Search Sphere exposes dedicated probe endpoints for load balancers and container orchestrators:

- **Liveness Probe**: `GET /health`
  - Returns `200 OK` if the process is running.
  - Used for container restart decisions.
- **Readiness Probe**: `GET /ready`
  - Validates active database connectivity with `SELECT 1`.
  - Returns `200 OK` when ready to serve traffic.
  - Returns `503 Service Unavailable` if database is down or connection pool is exhausted.

---

## 5. Security & Hardening Checklist

1. **Strict CORS**: In `ENVIRONMENT=production`, only origins listed in `ALLOWED_ORIGINS` are accepted. Wildcards (`*`) are disallowed.
2. **Rate Limiting**: Enabled on expensive endpoints (`/search`, `/answer`, `/answer/stream`, `/conversations/{id}/answer`, `/documents`). Returns HTTP `429 Too Many Requests` with a `Retry-After` header when triggered.
3. **Request Correlation IDs**: Every request receives or preserves an `X-Request-ID` header, automatically bound to all `structlog` log messages.
4. **Input Size Limits**:
   - PDF upload capped at 20MB (`MAX_UPLOAD_SIZE_BYTES`).
   - Query length capped at 1,000 characters (`MAX_QUERY_LENGTH`).
   - Conversation context message limit (`CONVERSATION_MAX_HISTORY_MESSAGES=10`).
5. **No Leaked Stack Traces**: Global exception handler intercepts unhandled exceptions and masks internal tracebacks from API consumers while logging detailed context with the `request_id`.

---

## 6. Continuous Integration (CI/CD)

Continuous integration is automated via GitHub Actions in [`.github/workflows/ci.yml`](file:///.github/workflows/ci.yml).

Every pull request and push to `main` verifies:
- Backend linting via `ruff`
- Backend test suite execution (`pytest`) including evaluation, tuning, and hardening tests
- Frontend TypeScript linting and Vitest unit/component tests
- Next.js production build (`pnpm build`)

---

## 7. Operations Runbook

### Issue: API returns 503 on `/ready`
- **Cause**: Database unreachable or connection pool exhausted.
- **Remediation**:
  1. Check PostgreSQL instance status and CPU/memory utilization.
  2. Verify network security groups allow traffic on port 5432.
  3. Inspect logs with `request_id` or grep for `"Readiness check failed"`.

### Issue: Users receive HTTP 429 Too Many Requests
- **Cause**: Client or token exceeded rate limit threshold.
- **Remediation**:
  1. Inspect `Retry-After` response header indicating how many seconds to wait.
  2. If legitimate traffic spikes occur, tune `RATE_LIMIT_REQUESTS_PER_MINUTE` in configuration.

### Issue: Document Ingestion Lag
- **Cause**: High upload volume or worker process starvation.
- **Remediation**:
  1. Check RabbitMQ queue depth.
  2. Scale the worker service horizontally: `docker compose up -d --scale worker=4`.
