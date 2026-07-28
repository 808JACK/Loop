"""
Jira/Confluence integration client.

See HLD §4.10 for full specification.
"""

import base64
import re
from collections.abc import Iterable
from typing import Any

import httpx

from src.settings import settings
from src.utils.repo_url import parse_repo_url


class JiraClient:
    """Client for interacting with Jira API using direct HTTP calls."""

    def __init__(self):
        """Initialize the Jira client with credentials from settings."""
        jira_url = settings.jira_url or ""
        if jira_url and not jira_url.startswith(("http://", "https://")):
            jira_url = f"https://{jira_url}"
        self.base_url = jira_url.rstrip("/")
        self.auth = base64.b64encode(
            f"{settings.jira_username}:{settings.jira_api_token}".encode()
        ).decode()
        self.headers = {
            "Authorization": f"Basic {self.auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Fetch issue details from Jira REST API v3."""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                f"{self.base_url}/rest/api/3/issue/{issue_key}",
                headers=self.headers,
            )
            response.raise_for_status()
            data = response.json()

        fields = data.get("fields", {})

        # Extract plain-text description from ADF (Atlassian Document Format)
        description_raw = fields.get("description")
        description = _extract_adf_text(description_raw)

        repo_url = _extract_first_text_field(
            fields, _split_candidates(settings.jira_repo_url_fields)
        )
        repo_name = _extract_first_text_field(
            fields, _split_candidates(settings.jira_repo_name_fields)
        )
        requested_reviewers = _extract_text_list(
            fields, _split_candidates(settings.jira_reviewer_fields)
        )

        # ── Fallback: parse repo_url / requested_reviewers from description body ──
        # Supports bullet-point format written directly in the Jira description:
        #   • repo_url: https://github.com/org/repo
        #   • requested_reviewers: alice, bob
        branch: str | None = None
        metadata = _parse_description_metadata(description)
        if not repo_url and metadata.get("repo_url"):
            repo_url = metadata.get("repo_url")
        if not requested_reviewers and metadata.get("desc_reviewers"):
            requested_reviewers = metadata.get("desc_reviewers")
        if not branch and metadata.get("branch"):
            branch = metadata.get("branch")

        assignee = fields.get("assignee")
        if not requested_reviewers:
            requested_reviewers = _coerce_text_values(assignee)

        requested_reviewers = _dedupe_strings(requested_reviewers)

        return {
            "key": data.get("key", issue_key),
            "summary": fields.get("summary", ""),
            "description": description,
            "labels": fields.get("labels", []),
            "status": fields.get("status", {}).get("name", ""),
            "issue_type": fields.get("issuetype", {}).get("name", ""),
            # Teams can store either the repo URL or repo name in Jira custom fields.
            "repo_url": repo_url,
            "repo_name": repo_name,
            "requested_reviewers": requested_reviewers,
            "branch": branch,
        }

    async def post_comment(self, issue_key: str, comment: str) -> dict[str, Any]:
        """
        Post a plain-text comment to a Jira issue.

        Jira Cloud REST v3 requires ADF (Atlassian Document Format) for comment body.
        """
        adf_body = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": comment}],
                }
            ],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.base_url}/rest/api/3/issue/{issue_key}/comment",
                headers=self.headers,
                json={"body": adf_body},
            )
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]

    async def has_label(self, issue_key: str, label: str) -> bool:
        """Check if an issue has a specific label."""
        try:
            issue = await self.get_issue(issue_key)
            return label in issue.get("labels", [])
        except Exception as e:
            print(f"Error checking label for {issue_key}: {e}")
            return False

    async def transition_issue(self, issue_key: str, transition_name: str) -> bool:
        """Transition an issue to a new status by transition name."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions",
                    headers=self.headers,
                )
                resp.raise_for_status()
                transitions = resp.json().get("transitions", [])

            match = next(
                (t for t in transitions if t["name"].lower() == transition_name.lower()),
                None,
            )
            if not match:
                available = [t["name"] for t in transitions]
                print(f"Transition '{transition_name}' not found. Available: {available}")
                return False

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions",
                    headers=self.headers,
                    json={"transition": {"id": match["id"]}},
                )
                resp.raise_for_status()
            return True
        except Exception as e:
            print(f"Error transitioning issue {issue_key}: {e}")
            return False

    async def remove_label(self, issue_key: str, label: str) -> bool:
        """Remove a specific label from an issue."""
        try:
            issue = await self.get_issue(issue_key)
            current_labels = issue.get("labels", [])
            if label not in current_labels:
                return True
            new_labels = [lbl for lbl in current_labels if lbl != label]
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.put(
                    f"{self.base_url}/rest/api/3/issue/{issue_key}",
                    headers=self.headers,
                    json={"fields": {"labels": new_labels}},
                )
                resp.raise_for_status()
            return True
        except Exception as e:
            print(f"Error removing label from {issue_key}: {e}")
            return False

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Bug",
        labels: list[str] | None = None,
        priority: str = "High",
    ) -> dict[str, Any]:
        """Create a new Jira issue."""
        adf_description = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }

        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": adf_description,
                "issuetype": {"name": issue_type},
                "priority": {"name": priority},
            }
        }

        if labels:
            payload["fields"]["labels"] = labels

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.base_url}/rest/api/3/issue",
                headers=self.headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()


def _split_candidates(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_description_metadata(description: str) -> dict:
    """
    Extract repo_url, branch, and requested_reviewers from a plain-text Jira description.

    Supports bullet-point format written directly in the issue body:
        • repo_url: https://github.com/org/repo
        • requested_reviewers: alice, bob

    Returns dict with keys: repo_url, branch, desc_reviewers
    """
    repo_url: str | None = None
    branch: str | None = None
    reviewers: list[str] = []

    if not description:
        return {"repo_url": None, "branch": None, "desc_reviewers": None}

    # Search for repo_url anywhere in the description text
    # Supports both "repo_url:" and "Repo:" formats
    m_repo = re.search(
        r"(?:repo_url|repo)\s*[:=]\s*(https?://[^\s\n\r]+)", description, re.IGNORECASE
    )
    if m_repo:
        candidate = m_repo.group(1).strip()
        if candidate:
            # Clean up trailing text but preserve #branch
            repo_url = re.sub(r'[^\w\-./:#@?=&%]', '', candidate)
            # Parse branch from URL
            clean_repo_url, branch = parse_repo_url(repo_url)
            repo_url = clean_repo_url

    # Search for requested_reviewers anywhere in the description text
    m_rev = re.search(r"requested_reviewers?\s*[:=]\s*([^\n\r]+)", description, re.IGNORECASE)
    if m_rev:
        val = m_rev.group(1).strip()
        # Truncate before any next tag / stop word
        for stop_word in ("Acceptance criteria", "repo_url", "Additional info"):
            idx = val.lower().find(stop_word.lower())
            if idx != -1:
                val = val[:idx].strip()
        # Split by comma and clean up trailing colons or symbols
        reviewers = [r.strip().rstrip(":") for r in val.split(",") if r.strip()]

    return {
        "repo_url": repo_url,
        "branch": branch,
        "desc_reviewers": reviewers if reviewers else None
    }


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in items:
            items.append(value)
    return items


def _coerce_text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        for key in (
            "displayName",
            "name",
            "value",
            "accountId",
            "emailAddress",
            "key",
            "display_name",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return [candidate.strip()]
        nested: list[str] = []
        for sub_value in value.values():
            nested.extend(_coerce_text_values(sub_value))
        return _dedupe_strings(nested)
    if isinstance(value, list):
        nested_list: list[str] = []
        for item in value:
            nested_list.extend(_coerce_text_values(item))
        return _dedupe_strings(nested_list)
    text = str(value).strip()
    return [text] if text else []


def _extract_first_text_field(fields: dict[str, Any], candidates: list[str]) -> str | None:
    for candidate in candidates:
        values = _coerce_text_values(fields.get(candidate))
        if values:
            return values[0]
    return None


def _extract_text_list(fields: dict[str, Any], candidates: list[str]) -> list[str]:
    collected: list[str] = []
    for candidate in candidates:
        collected.extend(_coerce_text_values(fields.get(candidate)))
    return _dedupe_strings(collected)


def _extract_adf_text(adf: Any) -> str:
    """Recursively extract plain text from an ADF node."""
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf
    if isinstance(adf, dict):
        node_type = adf.get("type", "")
        if node_type == "text":
            return adf.get("text", "")  # type: ignore[no-any-return]
        parts = [_extract_adf_text(child) for child in adf.get("content", [])]
        sep = "\n" if node_type in ("paragraph", "heading", "bulletList", "orderedList") else ""
        return sep.join(p for p in parts if p)
    if isinstance(adf, list):
        return " ".join(_extract_adf_text(item) for item in adf)
    return ""


# Singleton
_jira_client: JiraClient | None = None


def get_jira_client() -> JiraClient:
    """Return the singleton Jira client instance, creating it if needed."""
    global _jira_client
    if _jira_client is None:
        _jira_client = JiraClient()
    return _jira_client


# Convenience functions
async def post_jira_comment(issue_key: str, comment: str) -> dict[str, Any]:
    """Post a comment to a Jira issue."""
    return await get_jira_client().post_comment(issue_key, comment)


async def get_jira_issue(issue_key: str) -> dict[str, Any]:
    """Fetch a Jira issue by key."""
    return await get_jira_client().get_issue(issue_key)


async def transition_jira_issue(issue_key: str, transition_name: str) -> bool:
    """Transition a Jira issue to a new status."""
    return await get_jira_client().transition_issue(issue_key, transition_name)


async def remove_jira_label(issue_key: str, label: str) -> bool:
    """Remove a label from a Jira issue."""
    return await get_jira_client().remove_label(issue_key, label)
