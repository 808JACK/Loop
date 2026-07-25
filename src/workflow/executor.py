"""
Workflow Executor — direct LangGraph execution without API layer.

Simplified executor that runs the workflow directly from CLI.
"""

import os
import shutil
import subprocess  # nosec B404
import uuid

from sqlalchemy.orm import Session

from src.core.database.base import SessionLocal
from src.core.logging.logger import (
    get_logger,
    set_execution_id,
    setup_execution_file_logger,
)
from src.graph.graph import execution_graph
from src.graph.state.execution_state import ExecutionState
from src.integrations.jira.jira_client import get_jira_issue
from src.services.locking.lock_manager import LockManager
from src.services.memory.project_memory_service import get_project_memory
from src.settings import settings

logger = get_logger("workflow_executor")


def _create_worktree(repo_url: str, branch: str, execution_id: str, issue_key: str) -> str:
    """
    Clone into base/repos/<name> and create the worktree in base/runs/worktree-<id>.
    Keeps worktrees completely separate from the repo clone.
    """
    base = settings.worktree_base_path
    repos_dir = os.path.join(base, "repos")
    runs_dir = os.path.join(base, "runs")
    os.makedirs(repos_dir, exist_ok=True)
    os.makedirs(runs_dir, exist_ok=True)

    repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    main_repo = os.path.join(repos_dir, repo_name)

    # Add auth if GitHub token is available
    auth_url = repo_url
    if settings.github_token and "github.com" in repo_url:
        auth_url = repo_url.replace("https://", f"https://oauth2:{settings.github_token}@")

    if not os.path.exists(main_repo):
        logger.info(f"Cloning {repo_url} → {main_repo}")
        subprocess.run(["git", "clone", auth_url, main_repo], check=True, capture_output=True)  # nosec B603, B607
        subprocess.run(  # nosec B603, B607
            ["git", "-C", main_repo, "config", "user.email", "ai-sdlc@automation.local"],
            capture_output=True,
        )
        subprocess.run(  # nosec B603, B607
            ["git", "-C", main_repo, "config", "user.name", "AI SDLC Bot"], capture_output=True
        )
    else:
        subprocess.run(  # nosec B603, B607
            ["git", "-C", main_repo, "remote", "set-url", "origin", auth_url], capture_output=True
        )
        subprocess.run(["git", "-C", main_repo, "fetch", "--all"], capture_output=True)  # nosec B603, B607
        subprocess.run(["git", "-C", main_repo, "worktree", "prune"], capture_output=True)  # nosec B603, B607

    worktree_path = os.path.join(runs_dir, f"worktree-{execution_id}")
    ai_branch = issue_key.strip() or f"ai/{execution_id[:8]}"

    logger.info(f"Creating worktree at {worktree_path}")

    # Clean up existing worktree if present
    if os.path.exists(worktree_path):
        try:
            subprocess.run(["git", "-C", worktree_path, "status"], check=True, capture_output=True)  # nosec B603, B607
            subprocess.run(["git", "-C", worktree_path, "reset", "--hard"], capture_output=True)  # nosec B603, B607
            subprocess.run(["git", "-C", worktree_path, "clean", "-fd"], capture_output=True)  # nosec B603, B607
            return worktree_path
        except Exception as e:
            logger.warning(
                f"Existing worktree at {worktree_path} is invalid or reset failed ({e}). "
                "Removing it to recreate."
            )
            shutil.rmtree(worktree_path, ignore_errors=True)
            try:
                subprocess.run(  # nosec B603, B607
                    ["git", "-C", main_repo, "worktree", "remove", "-f", worktree_path],
                    capture_output=True,
                )
            except Exception as e:
                # Worktree may not exist, which is fine
                logger.debug(f"Failed to remove worktree: {e}")

    # Also remove any old worktrees using the same branch
    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "-C", main_repo, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        # Parse porcelain format: worktree <path>, branch refs/heads/<branch>
        current_worktree = None
        current_branch = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_worktree = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current_branch = line.split("/")[-1]
                # Check if this worktree uses our target branch
                if current_branch == ai_branch and current_worktree:
                    logger.info(f"Removing old worktree using same branch: {current_worktree}")
                    subprocess.run(  # nosec B603, B607
                        ["git", "-C", main_repo, "worktree", "remove", "-f", current_worktree],
                        capture_output=True,
                    )
                    if os.path.exists(current_worktree):
                        shutil.rmtree(current_worktree, ignore_errors=True)
                    current_worktree = None
                    current_branch = None
    except Exception as e:
        logger.debug(f"Failed to clean old worktrees: {e}")

    branch_exists = (
        subprocess.run(  # nosec B603, B607
            ["git", "-C", main_repo, "show-ref", "--verify", "--quiet", f"refs/heads/{ai_branch}"],
            capture_output=True,
        ).returncode
        == 0
    )

    if branch_exists:
        cmd = ["git", "-C", main_repo, "worktree", "add", worktree_path, ai_branch]
        logger.info(f"Creating worktree from existing branch {ai_branch}")
    else:
        # Auto-detect the actual default remote branch (master or main)
        try:
            head_result = subprocess.run(  # nosec B603, B607
                ["git", "-C", main_repo, "symbolic-ref", "refs/remotes/origin/HEAD"],
                capture_output=True, text=True,
            )
            if head_result.returncode == 0:
                # refs/remotes/origin/master -> master
                detected = head_result.stdout.strip().split("/")[-1]
            else:
                # Fall back: check if origin/master or origin/main exists
                for candidate in ("master", "main", branch):
                    check = subprocess.run(  # nosec B603, B607
                        ["git", "-C", main_repo, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{candidate}"],
                        capture_output=True,
                    )
                    if check.returncode == 0:
                        detected = candidate
                        break
                else:
                    detected = branch
        except Exception:
            detected = branch

        origin_branch = f"origin/{detected}"
        cmd = [
            "git",
            "-C",
            main_repo,
            "worktree",
            "add",
            "-b",
            ai_branch,
            worktree_path,
            origin_branch,
        ]
        logger.info(f"Creating worktree from origin branch {origin_branch}")

    try:
        subprocess.run(cmd, check=True, capture_output=True)  # nosec B603, B607
        logger.info(f"Successfully created worktree at {worktree_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Worktree creation failed: {e}")
        logger.error(f"Command: {cmd}")
        logger.error(f"Stderr: {e.stderr.decode() if e.stderr else 'None'}")
        raise

    return worktree_path


async def run_workflow(
    execution_id: str,
    issue_key: str,
    repo_url: str | None,
    branch: str,
    is_resume: bool = False,
):
    """
    Run the LangGraph workflow directly without API layer.
    """
    set_execution_id(execution_id)
    db: Session = SessionLocal()
    worktree_path: str | None = None
    lock_manager: LockManager | None = None
    lock_acquired = False
    log_file = None

    try:
        # Fetch issue from Jira
        issue = await get_jira_issue(issue_key)
        issue_summary = issue.get("summary", "")
        issue_description = issue.get("description", "")
        requested_reviewers = issue.get("requested_reviewers", [])

        # Transition Jira issue to "In Progress" when starting execution
        from src.integrations.jira.jira_client import transition_jira_issue
        try:
            transition_success = await transition_jira_issue(issue_key, "In Progress")
            if transition_success:
                logger.info(f"Transitioned {issue_key} to 'In Progress'")
            else:
                logger.warning(f"Could not transition {issue_key} to 'In Progress'")
        except Exception as e:
            logger.warning(f"Could not transition Jira issue to 'In Progress': {e}")

        # Resolve repo_url
        if not repo_url:
            repo_url = issue.get("repo_url") or settings.default_repo_url

        # Create or update execution record in database
        from src.models.execution import Execution, ExecutionStatus, LLMProvider

        if is_resume:
            execution_rec = (
                db.query(Execution)
                .filter(Execution.execution_id == uuid.UUID(execution_id))
                .first()
            )
            if execution_rec:
                # Update status to running
                execution_rec.status = ExecutionStatus.RUNNING
                db.commit()
            else:
                logger.warning("Execution record not found, creating new one")
                execution_rec = Execution(
                    execution_id=uuid.UUID(execution_id),
                    issue_key=issue_key,
                    idempotency_key=execution_id,
                    status=ExecutionStatus.RUNNING,
                    repo_url=repo_url,
                    llm_provider=LLMProvider.CLAUDE,
                )
                db.add(execution_rec)
                db.commit()
        else:
            # Create new execution record
            execution_rec = Execution(
                execution_id=uuid.UUID(execution_id),
                issue_key=issue_key,
                idempotency_key=execution_id,
                status=ExecutionStatus.RUNNING,
                repo_url=repo_url,
                llm_provider=LLMProvider.CLAUDE,
            )
            db.add(execution_rec)
            db.commit()

        # Worktree + lock (skip if resuming)
        if repo_url and not is_resume:
            try:
                worktree_path = _create_worktree(repo_url, branch, execution_id, issue_key)
                logger.info(f"Created worktree at {worktree_path}")

                # Update execution record with worktree path
                execution_rec.worktree_path = worktree_path
                execution_rec.branch_name = issue_key.strip() or f"ai/{execution_id[:8]}"
                db.commit()

                # Try to acquire lock but don't fail if we can't
                lock_manager = LockManager(db)
                lock_acquired = lock_manager.acquire_lock(repo_url, branch, wait=False)
                if not lock_acquired:
                    logger.warning(
                        f"Could not acquire lock for {repo_url}:{branch} - proceeding without lock"
                    )
            except Exception as e:
                logger.warning(f"Worktree creation failed: {e}")
                logger.warning("Continuing without worktree - some features may be limited")
                worktree_path = None
        elif is_resume:
            # Fetch existing worktree from database
            if execution_rec:
                worktree_path = execution_rec.worktree_path
                logger.info(f"Resuming with existing worktree at {worktree_path}")
            else:
                logger.warning("Could not find execution record, will create new worktree")
                if repo_url:
                    worktree_path = _create_worktree(repo_url, branch, execution_id, issue_key)
                else:
                    logger.warning("No repo_url available, cannot create worktree")

        # Setup logging
        if worktree_path:
            log_file = setup_execution_file_logger(execution_id)
            logger.info(f"Execution logs will be written to {log_file}")

        # Build initial state
        ai_branch_name = issue_key.strip() or f"ai/{execution_id[:8]}"
        invoke_state: ExecutionState = {
            "execution_id": execution_id,
            "issue_key": issue_key,
            "idempotency_key": execution_id,
            "issue_summary": issue_summary,
            "issue_description": issue_description,
            "repo_url": repo_url or "",
            "branch": branch,
            "worktree_path": worktree_path or "",
            "branch_name": ai_branch_name,
            "status": "roadmapping",
            "risk_level": "medium",
            "current_phase_index": 0,
            "phase_plans": [],
            "sanity_check_result": {},
            "retry_count": 0,
            "last_error": None,
            "project_memory": get_project_memory(repo_url) if repo_url else None,
            "requested_reviewers": requested_reviewers,
        }

        # Run the graph
        config = {
            "configurable": {
                "thread_id": execution_id,
                "checkpoint_id": None,
            }
        }

        logger.info(f"Starting workflow execution for {issue_key}")
        result = execution_graph.invoke(invoke_state, config)

        logger.info(f"Workflow completed for {issue_key}")
        return result

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        raise
    finally:
        # Cleanup
        if lock_acquired and lock_manager and repo_url:
            try:
                db.rollback()
                lock_manager.release_lock(repo_url)
            except Exception as le:
                logger.warning(f"Could not release repository lock: {le}")

        if worktree_path and os.path.exists(worktree_path):
            try:
                # Only cleanup on success
                logger.info(f"Preserving worktree {worktree_path} for debugging")
            except Exception as e:
                logger.warning(f"Worktree cleanup failed: {e}")

        db.close()
