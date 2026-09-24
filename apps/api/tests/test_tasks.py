from unittest.mock import patch

from dramatiq.brokers.rabbitmq import RabbitmqBroker

from src.config import settings
from src.tasks.document_tasks import broker, enqueue_document, process_document_task


def test_rabbitmq_broker_configuration():
    """Verify that the Dramatiq broker is configured as a RabbitmqBroker using RABBITMQ_URL."""
    assert isinstance(broker, RabbitmqBroker)
    assert settings.RABBITMQ_URL.startswith("amqp://")
    assert process_document_task.actor_name == "process_document_task"
    assert process_document_task.queue_name == "default"


def test_enqueue_document_success():
    """Verify that enqueue_document calls process_document_task.send with document_id."""
    with patch.object(process_document_task, "send") as mock_send:
        result = enqueue_document("test-doc-uuid-123")
        assert result is True
        mock_send.assert_called_once_with("test-doc-uuid-123")


def test_enqueue_document_handles_offline_queue_gracefully():
    """Verify that if the RabbitMQ broker is offline, enqueue_document logs error and returns False without crashing."""
    with patch.object(
        process_document_task,
        "send",
        side_effect=Exception("RabbitMQ connection refused"),
    ):
        result = enqueue_document("test-doc-uuid-456")
        assert result is False
