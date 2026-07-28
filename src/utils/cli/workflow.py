"""Workflow execution utilities for CLI."""

import uuid

from src.utils.cli.formatter import print_box
from src.utils.constants import BOLD, DIM, GREEN, RED, RESET, YELLOW


async def list_checkpoints(execution_id: str) -> None:
    """List available checkpoints for an execution."""
    from src.core.checkpointer.postgres_checkpointer import create_postgres_checkpointer
    from src.core.database.base import SessionLocal
    from src.models.execution import Execution
    from src.utils.thread_manager import ThreadManager

    db = SessionLocal()

    print(f"\n  {GREEN}📋{RESET}  Listing checkpoints for {BOLD}{execution_id}{RESET}...")

    try:
        # Fetch execution from database
        execution = (
            db.query(Execution).filter(Execution.execution_id == uuid.UUID(execution_id)).first()
        )

        if not execution:
            print(f"  {RED}❌{RESET}  Execution not found: {execution_id}")
            return

        print(f"  {DIM}Issue: {execution.issue_key}{RESET}")
        print(f"  {DIM}Status: {execution.status.value}{RESET}")

        # Initialize ThreadManager with checkpointer and graph
        from src.graph.graph import execution_graph

        checkpointer = create_postgres_checkpointer()
        thread_manager = ThreadManager(checkpointer, execution_graph)

        # Get checkpoint history
        print(f"\n  {DIM}⏱  Fetching checkpoint history...{RESET}\n")
        history = await thread_manager.get_thread_history(execution_id)

        if not history:
            print(f"  {YELLOW}⚠️{RESET}  No checkpoints found for this execution")
            return

        print(f"  {GREEN}✓{RESET}  Found {len(history)} checkpoint(s)\n")

        # Display checkpoints in reverse order (newest first)
        for i, snapshot in enumerate(reversed(history), 1):
            checkpoint_id = snapshot.config.get("configurable", {}).get("checkpoint_id", "N/A")
            next_node = snapshot.next if snapshot.next else "End"
            metadata = snapshot.metadata or {}

            print(f"  {BOLD}Checkpoint {i}:{RESET}")
            print(f"    ID: {checkpoint_id}")
            print(f"    Next: {next_node}")
            if metadata:
                step = metadata.get("step", "N/A")
                source = metadata.get("source", "N/A")
                print(f"    Step: {step}")
                print(f"    Source: {source}")
            print()

        print(
            f"  {DIM}Use --resume {execution_id} --checkpoint <ID> "
            f"to resume from a specific checkpoint{RESET}"
        )

    except Exception as e:
        print(f"  {RED}❌{RESET}  Failed to list checkpoints: {e}")
        raise
    finally:
        db.close()


async def resume_execution(execution_id: str, checkpoint_id: str | None = None) -> str:
    """Resume a paused or failed execution from the database."""
    from src.core.checkpointer.postgres_checkpointer import create_postgres_checkpointer
    from src.core.database.base import SessionLocal
    from src.core.logging.logger import get_logger, set_execution_id, setup_execution_file_logger
    from src.graph.graph import execution_graph
    from src.models.execution import Execution, ExecutionStatus
    from src.utils.thread_manager import ThreadManager

    logger = get_logger("resume_execution")
    db = SessionLocal()

    print(f"\n  {GREEN}🔄{RESET}  Resuming execution {BOLD}{execution_id}{RESET}...")

    try:
        # Fetch execution from database
        execution = (
            db.query(Execution).filter(Execution.execution_id == uuid.UUID(execution_id)).first()
        )

        if not execution:
            print(f"  {RED}❌{RESET}  Execution not found: {execution_id}")
            return execution_id

        if execution.status not in [ExecutionStatus.PAUSED, ExecutionStatus.FAILED, ExecutionStatus.RUNNING]:
            print(
                f"  {YELLOW}⚠️{RESET}  Execution status is {execution.status.value}, cannot resume"
            )
            return execution_id

        print(f"  {DIM}Issue: {execution.issue_key}{RESET}")
        print(f"  {DIM}Status: {execution.status.value}{RESET}")
        print(f"  {DIM}Worktree: {execution.worktree_path}{RESET}")

        # Setup logging
        set_execution_id(execution_id)
        if execution.worktree_path:
            log_file = setup_execution_file_logger(execution_id)
            logger.info(f"Execution logs will be written to {log_file}")

        # Initialize ThreadManager with checkpointer and graph
        checkpointer = create_postgres_checkpointer()
        thread_manager = ThreadManager(checkpointer, execution_graph)

        # Get current thread state to see where we left off
        print(f"\n  {DIM}⏱  Checking thread state...{RESET}\n")
        current_state = await thread_manager.get_thread_state(execution_id)
        if current_state:
            print(f"  {DIM}Current state: {current_state.next}{RESET}")
            current_checkpoint_id = current_state.config.get("configurable", {}).get(
                "checkpoint_id", "N/A"
            )
            print(f"  {DIM}Current Checkpoint ID: {current_checkpoint_id}{RESET}")

        # Resume from checkpoint
        if checkpoint_id:
            print(f"\n  {DIM}⏱  Resuming from specific checkpoint: {checkpoint_id}{RESET}")
            print(f"  {DIM}Streaming logs below:{RESET}\n")
        else:
            print(
                f"\n  {DIM}⏱  Resuming workflow from latest checkpoint "
                f"— streaming logs below:{RESET}\n"
            )

        await thread_manager.resume_from_checkpoint(
            thread_id=execution_id,
            checkpoint_id=checkpoint_id,  # Use provided checkpoint_id or None for latest
        )

        print(f"\n  {GREEN}✓{RESET}  Execution resumed successfully")
        return execution_id

    except Exception as e:
        print(f"\n  {RED}❌{RESET}  Resume failed: {e}")
        logger.error(f"Resume failed: {e}")
        raise
    finally:
        db.close()


async def run_workflow(issue: dict) -> str:
    """Run the LangGraph workflow directly for an issue."""
    from src.workflow.executor import run_workflow

    execution_id = str(uuid.uuid4())
    from src.settings import settings

    repo_url = issue.get("repo_url") or settings.default_repo_url
    branch = issue.get("branch")

    print(f"\n  {GREEN}🚀{RESET}  Starting workflow for {BOLD}{issue['key']}{RESET}...")
    print(f"  {DIM}Execution ID: {execution_id}{RESET}")
    print(f"\n  {DIM}⏱  Running workflow — streaming logs below:{RESET}\n")

    try:
        await run_workflow(
            execution_id=execution_id,
            issue_key=issue["key"],
            repo_url=repo_url,
            branch=branch,
            is_resume=False,
        )
        print(f"\n  {GREEN}✓{RESET}  Workflow completed successfully")
        return execution_id
    except Exception as e:
        print(f"\n  {RED}❌{RESET}  Workflow failed: {e}")
        raise


def confirm(prompt: str) -> bool:
    """Ask user for confirmation."""
    while True:
        ans = input(f"\n{prompt} [y/n] ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  please type y or n")


async def run_issue(issue: dict, auto: bool = False) -> None:
    """Show issue info, optionally confirm, then run workflow."""
    reviewers_str = ", ".join(issue.get("requested_reviewers", [])) or "none"
    print_box(
        f"{issue['key']} — {issue['summary']}",
        [
            f"Status    : {issue.get('status', '?')}",
            f"Priority  : {issue.get('priority', '?')}",
            f"Desc      : {issue.get('description') or '(no description)'}",
            f"Repo      : {issue.get('repo_url') or issue.get('repo_name') or '(not set)'}",
            f"Reviewers : {reviewers_str}",
        ],
    )

    if not auto:
        if not confirm(f"Run workflow for {issue['key']}?"):
            print(f"  ⏭  Skipped {issue['key']}")
            return

    execution_id = await run_workflow(issue)

    print()
    print(f"  {GREEN}✓{RESET}  Execution completed: {execution_id}")
