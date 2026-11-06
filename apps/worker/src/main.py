"""The historical RabbitMQ entry point is superseded by the PostgreSQL worker."""

if __name__ == "__main__":
    raise SystemExit("Use apps/api: python -m src.services.ingestion_worker (hosted runtime).")
