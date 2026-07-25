"""Execution log model for persistent log storage."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base


class ExecutionLog(Base):
    """
    Execution log storage.

    Stores execution log content for debugging and inspection after worktree cleanup.
    """

    __tablename__ = "execution_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    log_content: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        """Convert execution log to dictionary."""
        return {
            "id": str(self.id),
            "workspace_id": self.workspace_id,
            "execution_id": self.execution_id,
            "log_content": self.log_content,
            "file_size": self.file_size,
            "repo_url": self.repo_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
