import structlog

from src.config import settings

logger = structlog.get_logger()

# Setup RabbitMQ broker for the API producer if dramatiq is available
process_document_task = None
process_medical_document_task = None
try:
    import dramatiq
    from dramatiq.brokers.rabbitmq import RabbitmqBroker

    broker = RabbitmqBroker(url=settings.RABBITMQ_URL)
    dramatiq.set_broker(broker)

    @dramatiq.actor(queue_name="default", actor_name="process_document_task")
    def process_document_task(document_id: str) -> None:
        """Dispatches document ingestion tasks to the background worker via RabbitMQ."""
        logger.info("process_document_task dispatched", document_id=document_id)

    @dramatiq.actor(queue_name="default", actor_name="process_medical_document_task")
    def process_medical_document_task(document_id: str, request_id: str | None = None) -> None:
        """Dispatches external medical document ingestion tasks to the background worker."""
        logger.info("process_medical_document_task dispatched", document_id=document_id, request_id=request_id)

except Exception as e:
    logger.warning("Could not initialize RabbitMQ broker for Dramatiq", error=str(e))
    process_document_task = None
    process_medical_document_task = None


def enqueue_document(document_id: str) -> bool:
    """Pushes document_id to the worker queue for background extraction and processing."""
    if process_document_task is None:
        return False
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


def enqueue_medical_document(document_id: str, request_id: str | None = None) -> bool:
    """Pushes external medical document_id to the worker queue for background processing."""
    if process_medical_document_task is None:
        return False
    try:
        if request_id:
            process_medical_document_task.send(document_id, request_id=request_id)
        else:
            process_medical_document_task.send(document_id)
        logger.info("Pushed medical document to worker queue", document_id=document_id, request_id=request_id)
        return True
    except Exception as exc:
        logger.warning(
            "Failed to push medical document to worker queue (queue may be offline)",
            document_id=document_id,
            error=str(exc),
        )
        return False

