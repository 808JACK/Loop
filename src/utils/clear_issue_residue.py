#!/usr/bin/env python3
"""Clear all residue (filesystem + Supabase DB) for a specific Jira issue.

Usage:
  uv run python src/utils/clear_issue_residue.py KAN-2
  uv run python src/utils/clear_issue_residue.py KAN-5
"""

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.database.base import SessionLocal  # noqa: E402
from src.models.checkpoint import Checkpoint  # noqa: E402
from src.models.execution import Execution  # noqa: E402
from src.models.execution_log import ExecutionLog  # noqa: E402
from src.models.execution_memory import ExecutionMemory  # noqa: E402
from src.models.phase_execution import PhaseExecution  # noqa: E402
from src.models.project_memory import ProjectMemory  # noqa: E402
from src.models.stage_event_log import StageEventLog  # noqa: E402
from src.settings import settings  # noqa: E402

# =====================================================
# Edit this to set the issue you want to clear
ISSUE_ID = "KAN-2"
# =====================================================


def clear_filesystem_residue(issue_id: str, execution_ids: list[str]):
    """Clear filesystem residue for the given issue ID and its execution IDs."""
    print(f"🗑️  Clearing filesystem residue for {issue_id}...")

    base = Path(settings.worktree_base_path)
    runs_dir = base / "runs"

    # Delete per-issue named folder (e.g. runs/KAN-2)
    issue_run_dir = runs_dir / issue_id
    if issue_run_dir.exists():
        shutil.rmtree(issue_run_dir)
        print(f"  ✓ Deleted {issue_run_dir}")

    # Delete per-execution worktree folders (e.g. runs/worktree-<exec_id>)
    for exec_id in execution_ids:
        worktree_dir = runs_dir / f"worktree-{exec_id}"
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir)
            print(f"  ✓ Deleted {worktree_dir}")

    if not issue_run_dir.exists() and not any(
        (runs_dir / f"worktree-{e}").exists() for e in execution_ids
    ):
        print(f"  No filesystem residue found for {issue_id}")

    # Clear execution log files
    logs_dir = PROJECT_ROOT / "logs" / "executions"
    if logs_dir.exists():
        for log_file in logs_dir.glob(f"*{issue_id}*"):
            log_file.unlink()
            print(f"  ✓ Deleted log {log_file}")


def clear_database_residue(issue_id: str) -> list[str]:
    """Clear all Supabase DB records for the given issue. Returns list of execution IDs."""
    print(f"🗑️  Clearing Supabase DB residue for {issue_id}...")

    db = SessionLocal()
    try:
        executions = db.query(Execution).filter(Execution.issue_key == issue_id).all()
        execution_ids = [str(e.execution_id) for e in executions]

        if not execution_ids:
            print(f"  No executions found in DB for {issue_id}")
            return []

        print(f"  Found {len(executions)} execution(s)")

        counts = {
            "checkpoints": 0,
            "stage_event_logs": 0,
            "phase_executions": 0,
            "execution_logs": 0,
        }

        for exec_id in execution_ids:
            counts["checkpoints"] += (
                db.query(Checkpoint).filter(Checkpoint.thread_id == exec_id).delete()
            )
            counts["stage_event_logs"] += (
                db.query(StageEventLog).filter(StageEventLog.execution_id == exec_id).delete()
            )
            counts["phase_executions"] += (
                db.query(PhaseExecution).filter(PhaseExecution.execution_id == exec_id).delete()
            )
            counts["execution_logs"] += (
                db.query(ExecutionLog).filter(ExecutionLog.execution_id == exec_id).delete()
            )

        # Also clear project memory records
        repo_urls = [e.repo_url for e in executions if e.repo_url]
        pm = 0
        if repo_urls:
            pm = db.query(ProjectMemory).filter(ProjectMemory.repo.in_(repo_urls)).delete(synchronize_session=False)

        em = db.query(ExecutionMemory).filter(ExecutionMemory.issue_key == issue_id).delete()
        ex = db.query(Execution).filter(Execution.issue_key == issue_id).delete()

        db.commit()

        for label, count in counts.items():
            print(f"  ✓ Deleted {count} {label} record(s)")
        print(f"  ✓ Deleted {em} execution_memory record(s)")
        print(f"  ✓ Deleted {ex} execution record(s)")
        print(f"  ✓ Deleted {pm} project_memory record(s)")

        return execution_ids

    except Exception as e:
        db.rollback()
        print(f"  ✗ Database error: {e}")
        raise
    finally:
        db.close()


def main():
    issue_id = sys.argv[1] if len(sys.argv) > 1 else ISSUE_ID

    print(f"🧹 Starting cleanup for issue: {issue_id}")
    print("=" * 50)

    # DB first so we have execution IDs to match filesystem paths
    execution_ids = clear_database_residue(issue_id)
    clear_filesystem_residue(issue_id, execution_ids)

    print("=" * 50)
    print(f"✅ Cleanup complete for {issue_id} — ready for a fresh run")


if __name__ == "__main__":
    main()
