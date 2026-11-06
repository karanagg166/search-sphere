# Build from the canonical API context: docker build -f apps/worker/Dockerfile apps/api
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STORAGE_BACKEND=supabase \
    SUPABASE_STORAGE_BUCKET=documents \
    UVICORN_WORKERS=1 \
    WEB_CONCURRENCY=1 \
    TIKTOKEN_CACHE_DIR=/opt/tokenizer-cache

WORKDIR /app

COPY requirements-production.txt .
RUN pip install --no-cache-dir -r requirements-production.txt
# Deterministic coarse tokenizer vocabulary, not inference weights. No runtime download.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY alembic.ini .
COPY alembic ./alembic
COPY src ./src

CMD ["python", "-m", "src.services.ingestion_worker"]
