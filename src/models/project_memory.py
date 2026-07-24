"""Project Memory model - global, per-repo knowledge store."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base


class ProjectMemory(Base):
    """
    Project Memory - durable, cross-execution knowledge about a repo.

    See HLD §5.3 for full specification.
    """

    __tablename__ = "project_memory"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repo: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)

    architecture_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    module_summaries: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    conventions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    dependency_graph_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recent_changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    compact_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    project_structure: Mapped[str | None] = mapped_column(String(3000), nullable=True)
    project_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {
            "memory_id": str(self.memory_id),
            "repo": self.repo,
            "architecture_summary": self.architecture_summary,
            "module_summaries": self.module_summaries,
            "conventions": self.conventions,
            "dependency_graph_ref": self.dependency_graph_ref,
            "recent_changes": self.recent_changes,
            "compact_summary": self.compact_summary,
            "project_structure": self.project_structure,
            "project_type": self.project_type,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }
