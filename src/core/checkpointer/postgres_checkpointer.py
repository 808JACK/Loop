"""
Postgres-backed LangGraph checkpointer.

Provides persistent checkpoint storage using PostgreSQL instead of in-memory storage.
"""

import base64
import json
import uuid
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any, cast

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.types import RunnableConfig

from src.core.logging.logger import get_logger
from src.models.checkpoint import Checkpoint as DBCheckpoint

logger = get_logger("checkpointer")


class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle LangChain message objects."""

    def default(self, obj):
        """Override default method to handle custom object serialization."""
        # Handle LangChain message types
        if hasattr(obj, "dict"):
            return obj.dict()
        elif hasattr(obj, "to_dict"):
            return obj.to_dict()
        elif hasattr(obj, "__dict__"):
            return obj.__dict__
        # Fallback to default
        return super().default(obj)


class PostgresCheckpointer(BaseCheckpointSaver):
    """
    Postgres-based checkpointer for LangGraph state persistence.

    Stores checkpoint data in the checkpoints table for recovery and inspection.
    Implements the BaseCheckpointSaver interface for LangGraph compatibility.
    """

    def __init__(self):
        """Initialize the PostgresCheckpointer saver."""
        super().__init__()

    def _get_db(self):
        """Get a fresh database session."""
        from src.core.database.base import SessionLocal

        return SessionLocal()

    def _get_thread_id(self, config: RunnableConfig) -> str:
        """Extract thread_id from config."""
        return config.get("configurable", {}).get("thread_id", "")  # type: ignore[no-any-return]

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata | None = None,
        new_versions: dict[str, Any] | None = None,
    ) -> RunnableConfig:
        """
        Store a checkpoint.

        Args:
            config: LangGraph config containing thread_id
            checkpoint: The checkpoint state to store
            metadata: Optional metadata about the checkpoint
            new_versions: Optional new version information

        Returns:
            Updated config with checkpoint ID
        """
        thread_id = self._get_thread_id(config)
        if not thread_id:
            return config

        checkpoint_id = str(uuid.uuid4())

        try:
            db = self._get_db()
            try:
                # Serialize checkpoint and metadata using the standard
                # SerializerProtocol (dumps_typed)
                type_str, serialized_bytes = self.serde.dumps_typed(checkpoint)
                checkpoint_data = json.dumps(
                    [type_str, base64.b64encode(serialized_bytes).decode("ascii")]
                )

                metadata_data = None
                if metadata:
                    meta_type, meta_bytes = self.serde.dumps_typed(metadata)
                    metadata_data = json.dumps(
                        [meta_type, base64.b64encode(meta_bytes).decode("ascii")]
                    )

                # Check if checkpoint exists for this thread
                existing = (
                    db.query(DBCheckpoint).filter(DBCheckpoint.thread_id == thread_id).first()
                )

                if existing:
                    # Update existing checkpoint
                    existing.checkpoint_data = checkpoint_data
                    existing.checkpoint_metadata = metadata_data
                    existing.updated_at = datetime.utcnow()
                else:
                    # Create new checkpoint
                    new_checkpoint = DBCheckpoint(
                        thread_id=thread_id,
                        checkpoint_data=checkpoint_data,
                        checkpoint_metadata=metadata_data,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    db.add(new_checkpoint)

                db.commit()

                # Return updated config without copy.deepcopy to prevent
                # pickling errors on DB session/locks
                new_config = {
                    **config,
                    "configurable": {
                        **config.get("configurable", {}),
                        "checkpoint_id": checkpoint_id,
                    },
                }
                return cast(RunnableConfig, new_config)
            except Exception as e:
                db.rollback()
                logger.error(f"Error saving checkpoint: {e}")
                return config
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error getting database session: {e}")
            return config

    def get(self, config: RunnableConfig) -> Checkpoint | None:
        """
        Retrieve a checkpoint.

        Args:
            config: LangGraph config containing thread_id

        Returns:
            Checkpoint data or None if not found
        """
        thread_id = self._get_thread_id(config)
        if not thread_id:
            return None

        logger.debug(f"🧠 Loading checkpoint for thread {thread_id}")

        try:
            db = self._get_db()
            try:
                checkpoint = (
                    db.query(DBCheckpoint).filter(DBCheckpoint.thread_id == thread_id).first()
                )

                if checkpoint:
                    raw = json.loads(checkpoint.checkpoint_data)
                    logger.debug(f"🧠 Loaded checkpoint for thread {thread_id}")
                    return self.serde.loads_typed(  # type: ignore[no-any-return]
                        (raw[0], base64.b64decode(raw[1].encode("ascii")))
                    )
                return None
            except Exception as e:
                logger.error(f"Error retrieving checkpoint: {e}")
                return None
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error getting database session: {e}")
            return None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """
        List checkpoints.

        Args:
            config: Optional config to filter by thread_id
            filter: Optional dict to filter metadata
            before: Optional config filter for checkpoints before this one
            limit: Optional limit on results

        Returns:
            Iterator of CheckpointTuples
        """
        try:
            db = self._get_db()
            try:
                query = db.query(DBCheckpoint)

                if config:
                    thread_id = self._get_thread_id(config)
                    if thread_id:
                        query = query.filter(DBCheckpoint.thread_id == thread_id)

                if before:
                    # Filter by created_at if before config provided
                    before_time = before.get("configurable", {}).get("created_at")
                    if before_time:
                        query = query.filter(DBCheckpoint.created_at < before_time)

                if limit:
                    query = query.limit(limit)

                checkpoints = query.all()

                for cp in checkpoints:
                    if cp.checkpoint_data:
                        raw_cp = json.loads(cp.checkpoint_data)
                        checkpoint_data = self.serde.loads_typed(
                            (raw_cp[0], base64.b64decode(raw_cp[1].encode("ascii")))
                        )

                        metadata = None
                        if cp.checkpoint_metadata:
                            raw_meta = json.loads(cp.checkpoint_metadata)
                            metadata = self.serde.loads_typed(
                                (raw_meta[0], base64.b64decode(raw_meta[1].encode("ascii")))
                            )

                        cp_config = {
                            "configurable": {"thread_id": cp.thread_id, "checkpoint_id": str(cp.id)}
                        }

                        yield CheckpointTuple(
                            config=cast(RunnableConfig, cp_config),
                            checkpoint=checkpoint_data,
                            metadata=(
                                cast(CheckpointMetadata, metadata)
                                if metadata is not None
                                else cast(CheckpointMetadata, {})
                            ),
                            parent_config=None,
                        )
            except Exception as e:
                logger.error(f"Error listing checkpoints: {e}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error getting database session: {e}")

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ):
        """Async iterator wrapper around list()."""
        for tuple_item in self.list(config, filter=filter, before=before, limit=limit):
            yield tuple_item

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Async wrapper around get_tuple()."""
        return self.get_tuple(config)

    def delete(self, config: RunnableConfig) -> bool:
        """
        Delete a checkpoint.

        Args:
            config: LangGraph config containing thread_id

        Returns:
            True if deleted, False otherwise
        """
        thread_id = self._get_thread_id(config)
        if not thread_id:
            return False

        try:
            db = self._get_db()
            try:
                checkpoint = (
                    db.query(DBCheckpoint).filter(DBCheckpoint.thread_id == thread_id).first()
                )

                if checkpoint:
                    db.delete(checkpoint)
                    db.commit()
                    return True
                return False
            except Exception as e:
                db.rollback()
                logger.error(f"Error deleting checkpoint: {e}")
                return False
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error getting database session: {e}")
            return False

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """
        Get a checkpoint tuple for the given config.

        Args:
            config: LangGraph config containing thread_id

        Returns:
            CheckpointTuple or None if not found
        """
        thread_id = self._get_thread_id(config)
        if not thread_id:
            return None

        logger.info(f"🧠 Loading checkpoint tuple for thread {thread_id}")

        try:
            db = self._get_db()
            try:
                checkpoint = (
                    db.query(DBCheckpoint).filter(DBCheckpoint.thread_id == thread_id).first()
                )

                if checkpoint:
                    raw_cp = json.loads(checkpoint.checkpoint_data)
                    checkpoint_data = self.serde.loads_typed(
                        (raw_cp[0], base64.b64decode(raw_cp[1].encode("ascii")))
                    )

                    metadata = None
                    if checkpoint.checkpoint_metadata:
                        raw_meta = json.loads(checkpoint.checkpoint_metadata)
                        metadata = self.serde.loads_typed(
                            (raw_meta[0], base64.b64decode(raw_meta[1].encode("ascii")))
                        )

                    return CheckpointTuple(
                        config=config,
                        checkpoint=checkpoint_data,
                        metadata=(
                            cast(CheckpointMetadata, metadata)
                            if metadata is not None
                            else cast(CheckpointMetadata, {})
                        ),
                        parent_config=None,  # Simplified for now
                    )
                return None
            except Exception as e:
                logger.error(f"Error getting checkpoint tuple: {e}")
                return None
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error getting database session: {e}")
            return None

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """
        Store writes for a checkpoint.

        Args:
            config: LangGraph config containing thread_id
            writes: Sequence of writes to store
            task_id: Task identifier
            task_path: Task path identifier
        """
        # For now, we'll skip storing writes separately
        # They can be stored as part of the checkpoint metadata if needed


def create_postgres_checkpointer() -> PostgresCheckpointer:
    """
    Create a Postgres checkpointer instance.

    Returns:
        PostgresCheckpointer instance
    """
    return PostgresCheckpointer()
