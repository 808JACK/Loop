"""Stage event log - audit trail for node transitions."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database.base import Base


class StageOutcome(PyEnum):
    """Outcome of a stage execution."""

    SUCCESS = "success"
    RETRY = "retry"
    FAILURE = "failure"


class StageEventLog(Base):
    """
    Stage event log - append-only log for audit trail.

    See HLD §5.5 for full specification.
    """

    __tablename__ = "stage_event_logs"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("executions.execution_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phase_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("phase_executions.phase_execution_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    node_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    entered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    outcome: Mapped[StageOutcome | None] = mapped_column(SQLEnum(StageOutcome), nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    execution = relationship("Execution", backref="stage_events")
    phase_execution = relationship("PhaseExecution", backref="stage_events")

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {
            "event_id": str(self.event_id),
            "execution_id": str(self.execution_id),
            "phase_execution_id": str(self.phase_execution_id) if self.phase_execution_id else None,
            "node_name": self.node_name,
            "entered_at": self.entered_at.isoformat() if self.entered_at else None,
            "exited_at": self.exited_at.isoformat() if self.exited_at else None,
            "outcome": self.outcome.value if self.outcome else None,
            "checkpoint_id": self.checkpoint_id,
        }
