#!/usr/bin/env python3
# its for clearning residue of each of the prev exection from db and workspace
"""Script to clear all residue for a specific issue ID (e.g., KAN-19, KAN-10).

Usage:
1. Edit the ISSUE_ID variable below
2. Run: uv run python -m app.utils.clear_issue_residue
"""

import shutil
import sys
from pathlib import Path

# Project root is two levels up from app/utils/
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.database.base import SessionLocal  # noqa: E402
from src.models.checkpoint import Checkpoint  # noqa: E402
from src.models.execution import Execution  # noqa: E402
from src.models.execution_log import ExecutionLog  # noqa: E402
from src.models.execution_memory import ExecutionMemory  # noqa: E402
from src.models.phase_execution import PhaseExecution  # noqa: E402
from src.models.project_memory import ProjectMemory  # noqa: E402

# ==================== CONFIGURATION ====================
# Change this to the issue ID you want to clear
ISSUE_ID = "KAN-27"
# =====================================================


def clear_filesystem_residue(issue_id: str):
    """Clear filesystem residue for the given issue ID."""
    print(f"🗑️  Clearing filesystem residue for {issue_id}...")

    # Clear runs directory (now inside worktrees/runs)
    from src.settings import settings

    runs_dir = Path(settings.worktree_base_path) / "runs" / issue_id
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
        print(f"✓ Deleted {runs_dir}")
    else:
        print(f"  No runs directory found for {issue_id}")

    # Clear execution logs
    logs_dir = PROJECT_ROOT / "logs" / "executions"
    if logs_dir.exists():
        for log_file in logs_dir.glob(f"*{issue_id}*"):
            log_file.unlink()
            print(f"✓ Deleted {log_file}")
    else:
        print("  No logs directory found")


def clear_database_residue(issue_id: str):
    """Clear database residue for the given issue ID."""
    print(f"🗑️  Clearing database residue for {issue_id}...")

    db = SessionLocal()
    try:
        # Get all execution IDs for the issue
        executions = db.query(Execution).filter(Execution.issue_key == issue_id).all()
        execution_ids = [str(e.execution_id) for e in executions]

        if not execution_ids:
            print(f"  No executions found for {issue_id}")
            return

        print(f"  Found {len(executions)} execution(s) for {issue_id}")

        # Delete from various tables
        deleted_checkpoints = 0
        deleted_phase_executions = 0
        deleted_execution_logs = 0
        deleted_execution_memory = 0
        deleted_executions = 0
        deleted_project_memory = 0

        for exec_id in execution_ids:
            deleted_checkpoints += (
                db.query(Checkpoint).filter(Checkpoint.thread_id == exec_id).delete()
            )
            deleted_phase_executions += (
                db.query(PhaseExecution).filter(PhaseExecution.execution_id == exec_id).delete()
            )
            deleted_execution_logs += (
                db.query(ExecutionLog).filter(ExecutionLog.execution_id == exec_id).delete()
            )

        deleted_execution_memory = (
            db.query(ExecutionMemory).filter(ExecutionMemory.issue_key == issue_id).delete()
        )
        deleted_executions = db.query(Execution).filter(Execution.issue_key == issue_id).delete()
        deleted_project_memory = (
            db.query(ProjectMemory).filter(ProjectMemory.repo.like(f"%{issue_id}%")).delete()
        )

        db.commit()

        print(f"✓ Deleted {deleted_checkpoints} checkpoint record(s)")
        print(f"✓ Deleted {deleted_phase_executions} phase execution record(s)")
        print(f"✓ Deleted {deleted_execution_logs} execution log record(s)")
        print(f"✓ Deleted {deleted_execution_memory} execution memory record(s)")
        print(f"✓ Deleted {deleted_executions} execution record(s)")
        print(f"✓ Deleted {deleted_project_memory} project memory record(s)")

    except Exception as e:
        db.rollback()
        print(f"✗ Database error: {e}")
        raise
    finally:
        db.close()


def main():
    """Clear all residue for the configured issue ID."""
    print(f"🧹 Starting cleanup for issue: {ISSUE_ID}")
    print("=" * 50)

    # Clear filesystem residue
    clear_filesystem_residue(ISSUE_ID)

    # Clear database residue
    clear_database_residue(ISSUE_ID)

    print("=" * 50)
    print(f"✅ Cleanup complete for {ISSUE_ID}")


if __name__ == "__main__":
    main()
