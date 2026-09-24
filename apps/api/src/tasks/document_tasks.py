import dramatiq
import structlog
from dramatiq.brokers.redis import RedisBroker

from src.config import settings

logger = structlog.get_logger()

# Setup broker for the API producer
try:
    broker = RedisBroker(url=settings.REDIS_URL)
    dramatiq.set_broker(broker)
except Exception as e:
    logger.warning("Could not initialize Redis broker for Dramatiq", error=str(e))


@dramatiq.actor(queue_name="default", actor_name="process_document_task")
def process_document_task(document_id: str) -> None:
    """Worker actor placeholder. Processing logic will be implemented in the next phase."""
    logger.info("process_document_task dispatched", document_id=document_id)


def enqueue_document(document_id: str) -> bool:
    """Pushes document_id to the worker queue for future extraction/processing."""
    try:
        process_document_task.send(document_id)
        logger.info("Pushed document to worker queue", document_id=document_id)
        return True
    except Exception as exc:
        logger.warning(
            "Failed to push document to worker queue (queue may be offline)",
            document_id=document_id,
            error=str(exc),
        )
        return False
