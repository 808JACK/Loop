"""Execution model - represents a single issue-to-PR execution."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base


class ExecutionStatus(PyEnum):
    """Status of an execution."""

    QUEUED = "queued"
    ROADMAPPING = "roadmapping"
    AWAITING_APPROVAL = "awaiting_approval"
    PHASE_PLANNING = "phase_planning"
    CODING = "coding"
    SANITY_CHECK = "sanity_check"
    PR_OPEN = "pr_open"
    IN_REVIEW = "in_review"
    MERGED = "merged"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RUNNING = "running"
    PAUSED = "paused"
    RATE_LIMITED = "rate_limited"


class RiskLevel(PyEnum):
    """Risk level of an execution."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LLMProvider(PyEnum):
    """LLM provider used for execution."""

    CLAUDE = "claude"
    GEMINI = "gemini"
    GROQ = "groq"
    OLLAMA = "ollama"


class Execution(Base):
    """
    Execution record - represents a single issue-to-PR execution.

    See HLD §5.1 for full specification.
    """

    __tablename__ = "executions"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    issue_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )

    status: Mapped[ExecutionStatus] = mapped_column(
        SQLEnum(ExecutionStatus), nullable=False, default=ExecutionStatus.QUEUED, index=True
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        SQLEnum(RiskLevel), nullable=False, default=RiskLevel.MEDIUM
    )

    roadmap: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    current_phase_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    langgraph_thread_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    llm_provider: Mapped[LLMProvider] = mapped_column(
        SQLEnum(LLMProvider), nullable=False, default=LLMProvider.CLAUDE
    )

    worktree_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    repo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    lock_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return {
            "execution_id": str(self.execution_id),
            "workspace_id": self.workspace_id,
            "issue_key": self.issue_key,
            "idempotency_key": self.idempotency_key,
            "status": self.status.value if self.status else None,
            "risk_level": self.risk_level.value if self.risk_level else None,
            "roadmap": self.roadmap,
            "current_phase_index": self.current_phase_index,
            "langgraph_thread_id": self.langgraph_thread_id,
            "llm_provider": self.llm_provider.value if self.llm_provider else None,
            "worktree_path": self.worktree_path,
            "branch_name": self.branch_name,
            "repo_url": self.repo_url,
            "pr_url": self.pr_url,
            "retry_count": self.retry_count,
            "error": self.error,
            "error_message": self.error_message,
            "lock_key": self.lock_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
