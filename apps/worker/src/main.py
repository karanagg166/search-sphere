import os
import dramatiq
from dramatiq.brokers.redis import RedisBroker
import structlog

logger = structlog.get_logger()

# Configure Redis broker for background worker
redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_broker = RedisBroker(url=redis_url)
dramatiq.set_broker(redis_broker)

logger.info("Worker initialized with Redis broker", redis_url=redis_url)


@dramatiq.actor
def process_document_task(document_id: str) -> None:
    """Sample background document processing actor."""
    logger.info("Received document task", document_id=document_id)


if __name__ == "__main__":
    logger.info("Worker process starting up...")
