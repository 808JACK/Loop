"""
Webhook handler for Git platform events (GitHub/GitLab/Bitbucket) and Jira status changes.

Handles PR merge events to transition Jira issues to "Done" status.
Handles Jira status changes to update frontend in real-time.
"""

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.core.database.base import SessionLocal
from src.core.logging.logger import get_logger
from src.integrations.jira.jira_client import transition_jira_issue
from src.models.execution import Execution
from src.api.websocket import manager

logger = get_logger("webhook")
router = APIRouter()


def _extract_issue_key_from_pr_title(title: str) -> str | None:
    """Extract Jira issue key from PR title (e.g., 'feat: KAN-123 Add feature')."""
    # Match pattern like KAN-123, PROJ-456, etc.
    match = re.search(r"[A-Z]+-\d+", title)
    return match.group(0) if match else None


def _extract_issue_key_from_branch(branch: str) -> str | None:
    """Extract Jira issue key from branch name (e.g., 'KAN-123/feature' or 'KAN-123')."""
    # Match pattern like KAN-123, PROJ-456, etc.
    match = re.search(r"[A-Z]+-\d+", branch)
    return match.group(0) if match else None


def _find_issue_key_from_pr_url(pr_url: str) -> str | None:
    """Find issue key by searching database for execution with matching PR URL."""
    try:
        db = SessionLocal()
        execution = db.query(Execution).filter(Execution.pr_url == pr_url).first()
        if execution:
            return execution.issue_key
        return None
    except Exception as e:
        logger.error(f"Error querying execution by PR URL: {e}")
        return None
    finally:
        db.close()


@router.post("/webhook/github")
async def github_webhook(request: Request):
    """
    Handle GitHub webhook events.

    Specifically handles 'pull_request' events with action 'closed' (merged).
    """
    try:
        payload = await request.json()
        event_type = request.headers.get("X-GitHub-Event", "")

        logger.info(f"Received GitHub webhook: {event_type}")

        # Only handle PR merge events
        if event_type != "pull_request":
            logger.info(f"Ignoring non-PR event: {event_type}")
            return {"status": "ignored"}

        action = payload.get("action")
        if action != "closed":
            logger.info(f"Ignoring PR action: {action}")
            return {"status": "ignored"}

        # Check if PR was merged
        pr_data = payload.get("pull_request", {})
        if not pr_data.get("merged", False):
            logger.info("PR was closed but not merged")
            return {"status": "ignored"}

        # Extract issue key
        pr_title = pr_data.get("title", "")
        pr_url = pr_data.get("html_url", "")
        branch = pr_data.get("head", {}).get("ref", "")

        issue_key = (
            _extract_issue_key_from_pr_title(pr_title)
            or _extract_issue_key_from_branch(branch)
            or _find_issue_key_from_pr_url(pr_url)
        )

        if not issue_key:
            logger.warning(f"Could not extract issue key from PR: {pr_title}")
            return {"status": "error", "message": "No issue key found"}

        logger.info(f"PR merged for issue {issue_key}, transitioning to 'Done'")

        # Transition Jira issue to "Done"
        success = await transition_jira_issue(issue_key, "Done")
        if success:
            logger.info(f"Successfully transitioned {issue_key} to 'Done'")
            # Broadcast status update to frontend
            await manager.broadcast_jira_update(issue_key, "Done")
            return {"status": "success", "issue_key": issue_key}
        else:
            logger.warning(f"Failed to transition {issue_key} to 'Done'")
            return {"status": "error", "message": "Failed to transition Jira issue"}

    except Exception as e:
        logger.error(f"Error handling GitHub webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webhook/gitlab")
async def gitlab_webhook(request: Request):
    """
    Handle GitLab webhook events.

    Specifically handles 'Merge Request' events with action 'merge'.
    """
    try:
        payload = await request.json()
        event_type = request.headers.get("X-GitLab-Event", "")

        logger.info(f"Received GitLab webhook: {event_type}")

        # Only handle MR merge events
        if event_type != "Merge Request Hook":
            logger.info(f"Ignoring non-MR event: {event_type}")
            return {"status": "ignored"}

        object_attributes = payload.get("object_attributes", {})
        action = object_attributes.get("action")

        if action != "merge":
            logger.info(f"Ignoring MR action: {action}")
            return {"status": "ignored"}

        # Extract issue key
        mr_title = object_attributes.get("title", "")
        mr_url = object_attributes.get("url", "")
        source_branch = object_attributes.get("source_branch", "")

        issue_key = (
            _extract_issue_key_from_pr_title(mr_title)
            or _extract_issue_key_from_branch(source_branch)
            or _find_issue_key_from_pr_url(mr_url)
        )

        if not issue_key:
            logger.warning(f"Could not extract issue key from MR: {mr_title}")
            return {"status": "error", "message": "No issue key found"}

        logger.info(f"MR merged for issue {issue_key}, transitioning to 'Done'")

        # Transition Jira issue to "Done"
        success = await transition_jira_issue(issue_key, "Done")
        if success:
            logger.info(f"Successfully transitioned {issue_key} to 'Done'")
            # Broadcast status update to frontend
            await manager.broadcast_jira_update(issue_key, "Done")
            return {"status": "success", "issue_key": issue_key}
        else:
            logger.warning(f"Failed to transition {issue_key} to 'Done'")
            return {"status": "error", "message": "Failed to transition Jira issue"}

    except Exception as e:
        logger.error(f"Error handling GitLab webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webhook/bitbucket")
async def bitbucket_webhook(request: Request):
    """
    Handle Bitbucket webhook events.

    Specifically handles 'pullrequest:fulfilled' events (merged).
    """
    try:
        payload = await request.json()
        event_key = request.headers.get("X-Event-Key", "")

        logger.info(f"Received Bitbucket webhook: {event_key}")

        # Only handle PR merge events
        if event_key != "pullrequest:fulfilled":
            logger.info(f"Ignoring non-merge event: {event_key}")
            return {"status": "ignored"}

        # Extract issue key
        pr_data = payload.get("pullrequest", {})
        pr_title = pr_data.get("title", "")
        pr_url = pr_data.get("links", {}).get("html", {}).get("href", "")
        source_branch = pr_data.get("source", {}).get("branch", {}).get("name", "")

        issue_key = (
            _extract_issue_key_from_pr_title(pr_title)
            or _extract_issue_key_from_branch(source_branch)
            or _find_issue_key_from_pr_url(pr_url)
        )

        if not issue_key:
            logger.warning(f"Could not extract issue key from PR: {pr_title}")
            return {"status": "error", "message": "No issue key found"}

        logger.info(f"PR merged for issue {issue_key}, transitioning to 'Done'")

        # Transition Jira issue to "Done"
        success = await transition_jira_issue(issue_key, "Done")
        if success:
            logger.info(f"Successfully transitioned {issue_key} to 'Done'")
            # Broadcast status update to frontend
            await manager.broadcast_jira_update(issue_key, "Done")
            return {"status": "success", "issue_key": issue_key}
        else:
            logger.warning(f"Failed to transition {issue_key} to 'Done'")
            return {"status": "error", "message": "Failed to transition Jira issue"}

    except Exception as e:
        logger.error(f"Error handling Bitbucket webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webhook/jira")
async def jira_webhook(request: Request):
    """
    Handle Jira webhook events for status changes.

    Specifically handles issue status transitions to update frontend in real-time.
    """
    try:
        payload = await request.json()
        event_type = request.headers.get("X-Atlassian-Token", "")

        logger.info(f"Received Jira webhook: {event_type}")

        # Extract issue key and status
        issue = payload.get("issue", {})
        issue_key = issue.get("key", "")
        fields = issue.get("fields", {})
        status = fields.get("status", {}).get("name", "")

        if not issue_key:
            logger.warning("No issue key in Jira webhook payload")
            return {"status": "error", "message": "No issue key found"}

        logger.info(f"Jira issue {issue_key} status changed to: {status}")

        # Broadcast status update to all connected WebSocket clients
        await manager.broadcast_jira_update(issue_key, status)

        # If status is "Done", also update execution record in database
        if status == "Done":
            try:
                db = SessionLocal()
                execution = db.query(Execution).filter(Execution.issue_key == issue_key).first()
                if execution:
                    execution.status = "completed"
                    db.commit()
                    logger.info(f"Updated execution {execution.execution_id} to completed")
                db.close()
            except Exception as e:
                logger.error(f"Error updating execution record: {e}")

        return {"status": "success", "issue_key": issue_key, "status": status}

    except Exception as e:
        logger.error(f"Error handling Jira webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
