"""
Queue integration client for execution requests.

Supports AWS SQS and Redis Streams as queue backends.
See HLD §7.4 for full specification.
"""

import json
from dataclasses import dataclass

from src.settings import settings


@dataclass
class ExecutionMessage:
    """Message for execution requests."""

    issue_key: str
    idempotency_key: str
    event_type: str


class QueueClient:
    """
    Abstract queue client interface.

    Concrete implementations for SQS and Redis Streams.
    """

    async def enqueue(self, message: ExecutionMessage) -> str:
        """
        Enqueue an execution request.

        Args:
            message: Execution message

        Returns:
            str: Message ID
        """
        raise NotImplementedError

    async def dequeue(self) -> ExecutionMessage | None:
        """
        Dequeue an execution request.

        Returns:
            Optional[ExecutionMessage]: Message or None if queue empty
        """
        raise NotImplementedError

    async def delete(self, message_id: str) -> bool:
        """
        Delete a message from the queue.

        Args:
            message_id: Message ID to delete

        Returns:
            bool: True if deleted
        """
        raise NotImplementedError


class SQSQueueClient(QueueClient):
    """AWS SQS queue client."""

    def __init__(self):
        """Initialize SQS client."""
        try:
            import boto3

            self.client = boto3.client(
                "sqs",
                region_name=settings.sqs_region,
                aws_access_key_id=settings.sqs_access_key_id,
                aws_secret_access_key=settings.sqs_secret_access_key,
            )
            self.queue_url = settings.sqs_queue_url
        except ImportError:
            raise ImportError("boto3 is required for SQS queue support")

    async def enqueue(self, message: ExecutionMessage) -> str:
        """Enqueue message to SQS."""
        response = self.client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(message.__dict__),
            MessageDeduplicationId=message.idempotency_key,
            MessageGroupId=message.issue_key,  # For FIFO queues
        )
        return response["MessageId"]  # type: ignore[no-any-return]

    async def dequeue(self) -> ExecutionMessage | None:
        """Dequeue message from SQS."""
        response = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,  # Long polling
        )

        messages = response.get("Messages", [])
        if not messages:
            return None

        message = messages[0]
        body = json.loads(message["Body"])

        # Store receipt handle for deletion
        self._last_receipt_handle = message["ReceiptHandle"]

        return ExecutionMessage(**body)

    async def delete(self, message_id: str) -> bool:
        """Delete message from SQS."""
        try:
            self.client.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=self._last_receipt_handle,
            )
            return True
        except Exception:
            return False


class RedisQueueClient(QueueClient):
    """Redis Streams queue client."""

    def __init__(self):
        """Initialize Redis client."""
        try:
            import redis  # type: ignore[import-untyped]

            self.client = redis.from_url(settings.redis_url)
            self.stream_name = "ai_sdlc:execution_queue"
        except ImportError:
            raise ImportError("redis is required for Redis queue support")

    async def enqueue(self, message: ExecutionMessage) -> str:
        """Enqueue message to Redis Stream."""
        message_id = self.client.xadd(
            self.stream_name,
            message.__dict__,
        )
        return message_id.decode()  # type: ignore[no-any-return]

    async def dequeue(self) -> ExecutionMessage | None:
        """Dequeue message from Redis Stream."""
        # Read from consumer group
        try:
            messages = self.client.xreadgroup(
                "ai_sdlc_workers",
                "worker_1",
                {self.stream_name: ">"},
                count=1,
                block=20000,  # 20 second timeout
            )
        except Exception:
            # Consumer group might not exist, create it
            try:
                self.client.xgroup_create(
                    self.stream_name, "ai_sdlc_workers", id="0", mkstream=True
                )
            except Exception:  # nosec B110
                pass
            return None

        if not messages:
            return None

        stream, entries = messages[0]
        message_id, data = entries[0]

        # Store message ID for deletion
        self._last_message_id = message_id

        # Convert bytes to strings
        data = {k.decode(): v.decode() for k, v in data.items()}

        return ExecutionMessage(**data)

    async def delete(self, message_id: str) -> bool:
        """Delete message from Redis Stream."""
        try:
            self.client.xack(self.stream_name, "ai_sdlc_workers", self._last_message_id)
            self.client.xdel(self.stream_name, self._last_message_id)
            return True
        except Exception:
            return False


# Factory function
def get_queue_client() -> QueueClient:
    """
    Get queue client based on configuration.

    Returns:
        QueueClient: Configured queue client
    """
    if settings.queue_type == "sqs":
        return SQSQueueClient()
    elif settings.queue_type == "redis":
        return RedisQueueClient()
    else:
        raise ValueError(f"Unsupported queue type: {settings.queue_type}")


# Convenience functions
async def enqueue_execution(
    issue_key: str,
    idempotency_key: str,
    event_type: str,
) -> str:
    """
    Enqueue an execution request.

    Args:
        issue_key: Jira/Confluence issue key
        idempotency_key: Idempotency key for deduplication
        event_type: Event type from webhook

    Returns:
        str: Message ID
    """
    client = get_queue_client()
    message = ExecutionMessage(
        issue_key=issue_key,
        idempotency_key=idempotency_key,
        event_type=event_type,
    )
    return await client.enqueue(message)


async def dequeue_execution() -> ExecutionMessage | None:
    """
    Dequeue an execution request.

    Returns:
        Optional[ExecutionMessage]: Message or None if queue empty
    """
    client = get_queue_client()
    return await client.dequeue()
