FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    STORAGE_BACKEND=supabase \
    SUPABASE_STORAGE_BUCKET=documents \
    UVICORN_WORKERS=1 \
    WEB_CONCURRENCY=1 \
    TIKTOKEN_CACHE_DIR=/opt/tokenizer-cache

WORKDIR /app

COPY requirements-production.txt .
RUN uv pip install --system --no-cache -r requirements-production.txt
# Deterministic coarse tokenizer vocabulary, not inference weights. No runtime download.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY alembic.ini .
COPY alembic ./alembic
COPY src ./src

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
