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

# Import actors to register with Dramatiq broker
from src.tasks.document_tasks import (  # noqa: E402
    process_document_task,
)

__all__ = ["process_document_task"]


if __name__ == "__main__":
    logger.info("Worker process starting up...")

