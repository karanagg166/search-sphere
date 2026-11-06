FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STORAGE_BACKEND=supabase \
    SUPABASE_STORAGE_BUCKET=documents \
    UVICORN_WORKERS=1 \
    WEB_CONCURRENCY=1

WORKDIR /app

COPY requirements-production.txt .
RUN pip install --no-cache-dir -r requirements-production.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY src ./src

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
