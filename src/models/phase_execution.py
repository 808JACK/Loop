"""Phase execution model - granular state per phase."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import Base


class PhaseStatus(PyEnum):
    """Status of a phase execution."""

    PENDING = "pending"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    SANITY_CHECK = "sanity_check"
    PASSED = "passed"
    FAILED = "failed"


class PhaseExecution(Base):
    """
    Phase execution record - one row per phase per execution.

    See HLD §5.4 for full specification.
    """

    __tablename__ = "phase_executions"

    phase_execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("executions.execution_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phase_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    status: Mapped[PhaseStatus] = mapped_column(
        SQLEnum(PhaseStatus), nullable=False, default=PhaseStatus.PENDING, index=True
    )

    steps: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    diff_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sanity_check_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationship
    execution = relationship("Execution", backref="phase_executions")

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {
            "phase_execution_id": str(self.phase_execution_id),
            "execution_id": str(self.execution_id),
            "phase_id": self.phase_id,
            "status": self.status.value if self.status else None,
            "steps": self.steps,
            "diff_ref": self.diff_ref,
            "sanity_check_result": self.sanity_check_result,
            "retry_count": self.retry_count,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
