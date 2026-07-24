"""
Git Manager Node — commit, push, create draft PR, update Jira.

See HLD §4.8 for specification.
"""

import os
import subprocess  # nosec B404

from src.core.database.base import SessionLocal
from src.core.logging.logger import get_logger
from src.graph.state.execution_state import ExecutionState
from src.integrations.jira.jira_client import post_jira_comment, transition_jira_issue
from src.services.memory.memory_service import MemoryService
from src.services.memory.project_memory_service import add_execution_to_project_memory
from src.settings import settings
from src.utils import format_commit_message, infer_commit_type, normalize_reviewers
from src.utils.constants import IGNORED_STAGE_PATHS

logger = get_logger("git_manager_node")


def _ensure_git_identity(worktree_path: str) -> None:
    """Set a local git identity if the repo/worktree does not already have one."""
    name = subprocess.run(  # nosec B603, B607
        ["git", "-C", worktree_path, "config", "--get", "user.name"],
        capture_output=True,
        text=True,
    )
    email = subprocess.run(  # nosec B603, B607
        ["git", "-C", worktree_path, "config", "--get", "user.email"],
        capture_output=True,
        text=True,
    )
    if not name.stdout.strip():
        subprocess.run(  # nosec B603, B607
            ["git", "-C", worktree_path, "config", "user.name", "AI SDLC Bot"],
            check=True,
        )
    if not email.stdout.strip():
        subprocess.run(  # nosec B603, B607
            ["git", "-C", worktree_path, "config", "user.email", "ai-sdlc-bot@example.com"],
            check=True,
        )


def git_manager_node(state: ExecutionState) -> ExecutionState:
    """Commit changes, push branch, create draft PR, post link to Jira."""
    logger.info("📍 [STEP 6/6] Starting git operations (commit, push, PR)")
    worktree_path = state.get("worktree_path", "")
    issue_key = state.get("issue_key", "")
    issue_summary = state.get("issue_summary", "")
    branch_name = state.get("branch_name", issue_key.strip())
    roadmap = state.get("roadmap") or {}
    repo_url = state.get("repo_url", "")
    requested_reviewers = normalize_reviewers(state.get("requested_reviewers") or [])

    if not worktree_path or not os.path.exists(worktree_path):
        logger.warning("No worktree found — skipping git operations")
        state["status"] = "completed"
        return state

    # 1. Stage + commit
    commit_msg = format_commit_message(issue_key, issue_summary, roadmap)
    try:
        add_cmd = ["git", "-C", worktree_path, "add", "-A", "--", "."]
        for pathspec in IGNORED_STAGE_PATHS:
            add_cmd.append(f":(exclude){pathspec}")
        subprocess.run(  # nosec B603, B607
            add_cmd,
            check=True,
            capture_output=True,
        )
        status = subprocess.run(  # nosec B603, B607
            ["git", "-C", worktree_path, "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        changed_paths = []
        for line in status.stdout.splitlines():
            if len(line) >= 4:
                pathspec = line[3:].strip()
                if pathspec and pathspec not in changed_paths:
                    changed_paths.append(pathspec)
        if not changed_paths:
            logger.info("No changes to commit")
            state["status"] = "completed"
            return state
        _ensure_git_identity(worktree_path)
        subprocess.run(  # nosec B603, B607
            ["git", "-C", worktree_path, "commit", "-m", commit_msg],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info(f"Committed: {commit_msg}")
    except subprocess.CalledProcessError as e:
        err = e.stderr or e.stdout or str(e)
        logger.error(f"Git commit failed: {err}")
        state["error"] = f"Git commit failed: {err}"
        state["status"] = "failed"
        return state

    # 2. Push branch
    try:
        subprocess.run(  # nosec B603, B607
            ["git", "-C", worktree_path, "push", "-u", "origin", branch_name],
            check=True,
            capture_output=True,
        )
        logger.info(f"Pushed: {branch_name}")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if e.stderr else str(e)
        logger.error(f"Git push failed: {err}")
        state["error"] = f"Git push failed: {err}"
        state["status"] = "failed"
        return state

    # 3. Create draft PR
    pr_url = _create_pr(
        repo_url, branch_name, issue_key, issue_summary, roadmap, changed_paths, requested_reviewers
    )
    state["pr_url"] = pr_url
    state["status"] = "pr_open"

    # 4. Post PR link to Jira and transition to "In Review"
    if pr_url:
        try:
            import asyncio

            async def _post_and_transition():
                # Post PR comment
                await post_jira_comment(issue_key, f"Pull request created: {pr_url}\n\nReady for review.")
                # Transition issue to "In Review"
                transition_success = await transition_jira_issue(issue_key, "In Review")
                if transition_success:
                    logger.info(f"Transitioned {issue_key} to 'In Review'")
                else:
                    logger.warning(f"Could not transition {issue_key} to 'In Review'")

            try:
                # Check if there's already a running loop
                loop = asyncio.get_running_loop()
                # If there is, create a task
                asyncio.create_task(_post_and_transition())
            except RuntimeError:
                # No running loop, safe to use asyncio.run
                asyncio.run(_post_and_transition())
        except Exception as e:
            logger.warning(f"Could not post PR comment or transition Jira issue: {e}")

    # NOTE: Jira status transition to "Done" on merge requires webhook handler
    # This should be implemented in a webhook endpoint that receives merge events
    # from GitHub/GitLab and calls transition_jira_issue(issue_key, "Done")

    # 5. Update project memory with this execution
    if repo_url and pr_url:
        execution_data = {
            "issue_key": issue_key,
            "issue_summary": issue_summary,
            "pr_url": pr_url,
            "roadmap_summary": roadmap.get("summary", ""),
            "risk_level": roadmap.get("risk_level", "medium"),
            "phases_count": len(roadmap.get("phases", [])),
            "phase_summaries": [
                {
                    "phase_index": idx + 1,
                    "name": phase.get("name", ""),
                    "goal": phase.get("goal", ""),
                    "complexity": phase.get("complexity", ""),
                }
                for idx, phase in enumerate(roadmap.get("phases", []))
            ],
            "touched_paths": changed_paths,
            "commit_message": commit_msg,
            "status": "pr_open",
            "timestamp": state.get("execution_id", ""),
        }
        try:
            add_execution_to_project_memory(repo_url, execution_data)
            logger.info(f"Added execution to project memory for {repo_url}")
        except Exception as e:
            logger.warning(f"Could not update project memory: {e}")

        try:
            db = SessionLocal()
            try:
                memory_service = MemoryService(db)
                memory_service.add_execution_to_memory(
                    issue_key=issue_key,
                    repo=repo_url,
                    execution_id=str(state.get("execution_id", "")),
                    outcome="pr_open",
                    roadmap_summary=roadmap.get("summary", ""),
                    phases=[
                        {
                            "phase_index": idx + 1,
                            "name": phase.get("name", ""),
                            "goal": phase.get("goal", ""),
                            "complexity": phase.get("complexity", ""),
                        }
                        for idx, phase in enumerate(roadmap.get("phases", []))
                    ],
                    review_comments=[],
                    pr_url=pr_url,
                )
                logger.info(f"Added execution memory for {issue_key}")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not update execution memory: {e}")

    logger.info("✅ [STEP 6/6] Git manager node completed")
    return state


def _build_pr_body(
    issue_key: str, issue_summary: str, roadmap: dict, touched_paths: list[str] | None = None
) -> str:
    lines = [
        f"## {issue_key}: {issue_summary}",
        "",
        f"**Summary:** {roadmap.get('summary', 'AI-generated implementation')}",
        f"**Risk level:** {roadmap.get('risk_level', 'medium')}",
        "",
        "### Changes",
    ]
    for phase in roadmap.get("phases", []):
        phase_name = phase.get("name", "Phase")
        description = " ".join((phase.get("description", "") or "").split()).strip()
        if len(description) > 140:
            description = description[:137].rstrip() + "..."
        if description:
            lines.append(f"- {phase_name}: {description}")
        else:
            lines.append(f"- {phase_name}")

    if touched_paths:
        lines += ["", "### Files"]
        for path in touched_paths:
            lines.append(f"- `{path}`")

    lines += ["", f"> Auto-generated for {issue_key}. Please review before merging."]
    return "\n".join(lines)


def _parse_github_repo(repo_url: str):
    parts = repo_url.rstrip("/").removesuffix(".git").split("/")
    return parts[-2], parts[-1]


def _create_pr(
    repo_url, branch_name, issue_key, issue_summary, roadmap, touched_paths=None, reviewers=None
):
    platform = settings.git_platform
    if platform == "github":
        return _github_pr(
            repo_url, branch_name, issue_key, issue_summary, roadmap, touched_paths, reviewers
        )
    if platform == "gitlab":
        return _gitlab_pr(
            repo_url, branch_name, issue_key, issue_summary, roadmap, touched_paths, reviewers
        )
    if platform == "bitbucket":
        return _bitbucket_pr(
            repo_url, branch_name, issue_key, issue_summary, roadmap, touched_paths, reviewers
        )
    logger.warning(f"Unknown git_platform '{platform}'")
    return None


def _github_pr(
    repo_url, branch_name, issue_key, issue_summary, roadmap, touched_paths=None, reviewers=None
):
    token = settings.github_token
    if not token:
        logger.warning("GITHUB_TOKEN not set — skipping PR creation")
        return None
    import httpx

    try:
        owner, repo = _parse_github_repo(repo_url)
    except Exception:
        logger.error(f"Cannot parse GitHub URL: {repo_url}")
        return None
    try:
        summary_text = " ".join((issue_summary or "").split()).strip()
        summary_text = summary_text or "AI-generated implementation"
        title_str = f"{infer_commit_type(issue_summary, roadmap)}: {summary_text}"
        resp = httpx.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "title": title_str[:200],
                "body": _build_pr_body(issue_key, issue_summary, roadmap, touched_paths),
                "head": branch_name,
                "base": "main",
                "draft": True,
            },
            timeout=20,
        )
        resp.raise_for_status()
        pr_data = resp.json()
        pr_url = pr_data.get("html_url", "")
        pr_number = pr_data.get("number")
        logger.info(f"GitHub draft PR: {pr_url}")

        # Add reviewers in a separate request — GitHub ignores reviewers on draft PR creation
        # and also silently drops the PR author from the reviewer list.
        cleaned_reviewers = normalize_reviewers(reviewers)
        if cleaned_reviewers and pr_number:
            req_url = (
                f"https://api.github.com/repos/{owner}/{repo}/pulls/"
                f"{pr_number}/requested_reviewers"
            )
            rev_resp = httpx.post(
                req_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"reviewers": cleaned_reviewers},
                timeout=20,
            )
            if rev_resp.status_code in (200, 201):
                logger.info(f"Reviewers requested: {cleaned_reviewers}")
            else:
                logger.warning(
                    f"Could not add reviewers {cleaned_reviewers}: "
                    f"{rev_resp.status_code} {rev_resp.text}"
                )

        return pr_url
    except Exception as e:
        logger.error(f"GitHub PR creation failed: {e}")
        return None


def _gitlab_pr(
    repo_url, branch_name, issue_key, issue_summary, roadmap, touched_paths=None, reviewers=None
):
    token = settings.gitlab_token
    if not token:
        logger.warning("GITLAB_TOKEN not set")
        return None
    import urllib.parse

    import httpx

    parts = repo_url.rstrip("/").removesuffix(".git").split("gitlab.com/")
    if len(parts) < 2:
        return None
    project_path = urllib.parse.quote(parts[1], safe="")
    summary_text = " ".join((issue_summary or "").split()).strip()
    summary_text = summary_text or "AI-generated implementation"
    commit_type = infer_commit_type(issue_summary, roadmap)
    title_str = f"Draft: {commit_type}: {summary_text}"
    try:
        resp = httpx.post(
            f"https://gitlab.com/api/v4/projects/{project_path}/merge_requests",
            headers={"PRIVATE-TOKEN": token},
            json={
                "title": title_str[:200],
                "description": _build_pr_body(issue_key, issue_summary, roadmap, touched_paths),
                "source_branch": branch_name,
                "target_branch": "main",
                "draft": True,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("web_url", "")
    except Exception as e:
        logger.error(f"GitLab MR creation failed: {e}")
        return None


def _bitbucket_pr(
    repo_url, branch_name, issue_key, issue_summary, roadmap, touched_paths=None, reviewers=None
):
    token = settings.bitbucket_token
    username = settings.bitbucket_username
    if not token or not username:
        logger.warning("BITBUCKET_TOKEN/USERNAME not set")
        return None
    import base64

    import httpx

    parts = repo_url.rstrip("/").removesuffix(".git").split("/")
    workspace, repo_slug = parts[-2], parts[-1]
    auth = base64.b64encode(f"{username}:{token}".encode()).decode()
    summary_text = " ".join((issue_summary or "").split()).strip()
    summary_text = summary_text or "AI-generated implementation"
    title_str = f"{infer_commit_type(issue_summary, roadmap)}: {summary_text}"
    try:
        resp = httpx.post(
            f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests",
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            json={
                "title": title_str[:200],
                "description": _build_pr_body(issue_key, issue_summary, roadmap, touched_paths),
                "source": {"branch": {"name": branch_name}},
                "destination": {"branch": {"name": "main"}},
                "draft": True,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("links", {}).get("html", {}).get("href", "")
    except Exception as e:
        logger.error(f"Bitbucket PR creation failed: {e}")
        return None
