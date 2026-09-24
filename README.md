# Search Sphere

A modular, Docker-based monorepo architecture for production-grade Semantic Search and Retrieval-Augmented Generation (RAG) applications.

---

## 🏛 Architecture Overview

```
search-sphere/
├── apps/
│   ├── api/             # FastAPI Backend (Python 3.12, Uvicorn, SQLAlchemy, Alembic)
│   ├── web/             # Next.js Frontend (TypeScript, Tailwind, shadcn/ui, TanStack Query)
│   └── worker/          # Asynchronous Background Worker (Dramatiq, Redis)
├── docker-compose.yml   # Multi-container orchestration with persistent volumes & networks
├── Makefile             # CLI shortcuts for development, testing, and lifecycle management
├── .env.example         # Template configuration with all connection strings and keys
├── .gitignore           # Ignores local dependencies, caches, and build artifacts
└── README.md            # Documentation and command reference
```

---

## 🚀 Services & Ports

| Service | Technology | Port(s) | Description / URL |
| :--- | :--- | :--- | :--- |
| **web** | Next.js 16 + React 19 | `3000` | UI Dashboard: [http://localhost:3000](http://localhost:3000) |
| **api** | FastAPI + Python 3.12 | `8000` | API & Health: [http://localhost:8000/health](http://localhost:8000/health)<br/>Swagger Docs: [http://localhost:8000/docs](http://localhost:8000/docs) |
| **worker** | Dramatiq + Python 3.12 | Background | Async document processing & ingestion worker |
| **postgres** | PostgreSQL 16 | `5432` | Relational database (`search_sphere`) |
| **redis** | Redis 7 | `6379` | In-memory cache & Dramatiq task broker |
| **qdrant** | Qdrant Vector DB | `6333` (REST)<br/>`6334` (gRPC) | Vector search engine dashboard: [http://localhost:6333/dashboard](http://localhost:6333/dashboard) |
| **minio** | MinIO Object Storage | `9000` (S3 API)<br/>`9001` (Console) | S3-compatible storage UI: [http://localhost:9001](http://localhost:9001)<br/>User/Pass: `minioadmin` / `minioadmin` |

---

## 💾 Persistent Volumes

Persistent Docker volumes ensure data is retained across restarts and rebuilds:
- `postgres_data` -> `/var/lib/postgresql/data`
- `redis_data` -> `/data`
- `qdrant_data` -> `/qdrant/storage`
- `minio_data` -> `/data`

---

## ⚡ Quick Start

### 1. Prerequisites
- Docker Engine & Docker Compose (`docker compose version` >= 2.20)
- Make (optional, but recommended)

### 2. Configure Environment
Copy the example environment file:
```bash
cp .env.example .env
```

### 3. Build and Start Services
```bash
# Using Makefile
make build
make up

# Or directly with Docker Compose
docker compose build
docker compose up -d
```

### 4. Verify Services
- Next.js Web: [http://localhost:3000](http://localhost:3000)
- FastAPI Health: [http://localhost:8000/health](http://localhost:8000/health)
- FastAPI Interactive Docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- MinIO Web Console: [http://localhost:9001](http://localhost:9001) (Credentials: `minioadmin` / `minioadmin`)
- Qdrant REST API: [http://localhost:6333](http://localhost:6333)

---

## 🛠 Essential Commands Reference

### 🏗 Build Services
```bash
# Build all images
make build
# or
docker compose build

# Rebuild without cache
docker compose build --no-cache
```

### ▶️ Start / Stop Services
```bash
# Start all containers in background
make up
# or
docker compose up -d

# Start with live logs attached
make dev
# or
docker compose up

# Stop all running containers
make down
# or
docker compose down

# Stop and remove persistent storage volumes (WARNING: wipes databases)
make down-v
# or
docker compose down -v
```

### 📜 Service Logs
```bash
# Stream all logs
make logs
# or
docker compose logs -f

# Stream specific service logs
make logs-api       # docker compose logs -f api
make logs-worker    # docker compose logs -f worker
make logs-web       # docker compose logs -f web
make logs-qdrant    # docker compose logs -f qdrant
make logs-postgres  # docker compose logs -f postgres
```

### 💻 Shell Access
```bash
# API container shell
make shell-api
# or
docker compose exec api bash

# Background Worker container shell
make shell-worker
# or
docker compose exec worker bash

# Next.js Web container shell
make shell-web
# or
docker compose exec web sh

# PostgreSQL psql interactive session
make shell-db
# or
docker compose exec postgres psql -U postgres -d search_sphere

# Redis CLI interactive session
make shell-redis
# or
docker compose exec redis redis-cli
```

---

## 🔥 Hot Reload & Development Flow

- **Backend (`apps/api`)**: Mounted locally into `/app`. Uvicorn runs with `--reload`, automatically picking up code changes in Python files.
- **Worker (`apps/worker`)**: Mounted locally into `/app`. Dramatiq runs with `--watch src`, automatically restarting worker threads upon file modifications.
- **Frontend (`apps/web`)**: Mounted locally into `/app` with isolated container `node_modules` and `.next` volumes. Next.js Fast Refresh automatically syncs UI updates instantly.

---

## 🧪 Testing & Code Quality

```bash
# Run pytest in the API container
make test
# or
docker compose exec api pytest

# Run Ruff linter and Mypy type-checking
make lint
# or
docker compose exec api ruff check .
docker compose exec api mypy src

# Auto-format Python code
make format
# or
docker compose exec api ruff format .
```

---

## 📦 Installed Libraries & SDKs

### Frontend (`apps/web`)
- **Framework**: Next.js 16 (App Router), React 19, TypeScript
- **Styling & UI**: Tailwind CSS, shadcn/ui component structure, Lucide Icons, class-variance-authority, tailwind-merge
- **Data & State**: TanStack Query (React Query) v5, Axios
- **Forms & Validation**: React Hook Form, Zod, `@hookform/resolvers`
- **File Ingestion**: React Dropzone

### Backend & Worker (`apps/api`, `apps/worker`)
- **Web & Async**: FastAPI, Uvicorn, Pydantic v2, Pydantic-Settings, asyncpg, SQLAlchemy 2.0, Alembic
- **Document Processing & OCR**: Docling, PyMuPDF (`fitz`), pypdf, pytesseract, Pillow, opencv-python-headless
- **Vector & Embeddings**: Sentence-Transformers, Transformers, PyTorch, Accelerate, Qdrant-Client, Tiktoken
- **LLM SDKs**: OpenAI, Anthropic, Cohere, Google GenAI (`google-genai`)
- **Storage & Queues**: Boto3, MinIO Python Client, Redis Python Client, Dramatiq (with Redis broker)
- **Utilities & Observability**: HTTPX, Tenacity, Structlog, Python-Dotenv, Langfuse
- **Tooling**: Pytest, Pytest-Asyncio, Ruff, Mypy, Watchfiles, Watchdog
