"""
Memory management service - handles Execution Memory.

See HLD §4.12 for full specification.
"""

from typing import Any

from sqlalchemy.orm import Session

from src.models.execution_memory import ExecutionMemory


class MemoryService:
    """Service for managing Execution Memory."""

    def __init__(self, db: Session):
        """
        Initialize memory service.

        Args:
            db: Database session
        """
        self.db = db

    def get_execution_memory(self, issue_key: str) -> dict[str, Any] | None:
        """
        Get Execution Memory for an issue.

        Execution Memory is local, per-issue working state that is
        written continuously throughout a run.

        Args:
            issue_key: Jira/Confluence issue key

        Returns:
            Dict containing Execution Memory or None if not exists
        """
        memory = (
            self.db.query(ExecutionMemory).filter(ExecutionMemory.issue_key == issue_key).first()
        )

        if memory:
            return memory.to_dict()  # type: ignore[no-any-return]
        return None

    def add_execution_to_memory(
        self,
        issue_key: str,
        repo: str,
        execution_id: str,
        outcome: str,
        roadmap_summary: str,
        phases: list[dict[str, Any]],
        review_comments: list[str] | None = None,
        pr_url: str | None = None,
    ) -> ExecutionMemory:
        """
        Add an execution record to Execution Memory.

        Args:
            issue_key: Jira/Confluence issue key
            repo: Repository identifier
            execution_id: Execution UUID
            outcome: Execution outcome (merged, failed, etc.)
            roadmap_summary: Summary of the roadmap
            phases: List of phase outcomes
            review_comments: Review comments received
            pr_url: PR URL if created

        Returns:
            ExecutionMemory: Updated memory record
        """
        memory = (
            self.db.query(ExecutionMemory).filter(ExecutionMemory.issue_key == issue_key).first()
        )

        if not memory:
            memory = ExecutionMemory(
                issue_key=issue_key,
                repo=repo,
                executions=[],
                related_issue_links=[],
            )
            self.db.add(memory)

        from datetime import datetime

        execution_record = {
            "execution_id": execution_id,
            "outcome": outcome,
            "roadmap_summary": roadmap_summary,
            "phases": phases,
            "review_comments": review_comments or [],
            "pr_url": pr_url,
            "completed_at": datetime.utcnow().isoformat(),
        }

        memory.executions.append(execution_record)
        self.db.commit()
        self.db.refresh(memory)

        return memory  # type: ignore[no-any-return]


# Convenience function
def get_execution_memory(db: Session, issue_key: str) -> dict[str, Any] | None:
    """Get Execution Memory for an issue."""
    service = MemoryService(db)
    return service.get_execution_memory(issue_key)
