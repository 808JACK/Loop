"""Execution Memory model - local, per-issue working state."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base


class ExecutionMemory(Base):
    """
    Execution Memory - durable, single-issue working state.

    See HLD §5.6 for full specification.
    """

    __tablename__ = "execution_memory"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    issue_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    repo: Mapped[str] = mapped_column(String(512), nullable=False, index=True)

    executions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    related_issue_links: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {
            "memory_id": str(self.memory_id),
            "workspace_id": self.workspace_id,
            "issue_key": self.issue_key,
            "repo": self.repo,
            "executions": self.executions,
            "related_issue_links": self.related_issue_links,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }
