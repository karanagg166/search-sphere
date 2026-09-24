import os
import dramatiq
import structlog
from dramatiq.brokers.rabbitmq import RabbitmqBroker

logger = structlog.get_logger()

# Configure RabbitMQ broker for background worker
rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
rabbitmq_broker = RabbitmqBroker(url=rabbitmq_url)
dramatiq.set_broker(rabbitmq_broker)

logger.info("Worker initialized with RabbitMQ broker", rabbitmq_url=rabbitmq_url)


@dramatiq.actor(queue_name="default", actor_name="process_document_task")
def process_document_task(document_id: str) -> None:
    """Sample background document processing actor."""
    logger.info("Received document task", document_id=document_id)



if __name__ == "__main__":
    logger.info("Worker process starting up...")
