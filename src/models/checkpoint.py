"""Checkpoint model for LangGraph state persistence."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base


class Checkpoint(Base):
    """
    LangGraph checkpoint storage.

    Stores checkpoint data for workflow state persistence and recovery.
    """

    __tablename__ = "checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    checkpoint_data: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        """Convert checkpoint to dictionary."""
        return {
            "id": str(self.id),
            "workspace_id": self.workspace_id,
            "thread_id": self.thread_id,
            "checkpoint_data": self.checkpoint_data,
            "checkpoint_metadata": self.checkpoint_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
