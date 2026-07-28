"""Jira integration utilities for CLI."""

import httpx

from src.integrations.jira.jira_client import (
    _coerce_text_values,
    _dedupe_strings,
    _extract_adf_text,
    _extract_first_text_field,
    _extract_text_list,
    _parse_description_metadata,
    get_jira_client,
)
from src.settings import settings
from src.utils.constants import BOLD, CYAN, RESET
from src.utils.repo_url import parse_repo_url


def _split_candidates(raw: str) -> list[str]:
    """Split comma-separated candidate field names."""
    return [item.strip() for item in raw.split(",") if item.strip()]


async def fetch_ai_ready_issues(repo_url_override: str | None = None) -> list[dict]:
    """Fetch all issues with the ai-ready label from the configured Jira project."""
    client = get_jira_client()
    label = settings.jira_ready_label
    project = settings.jira_project_key

    print(
        f"\n  {CYAN}🔍{RESET}  Fetching {BOLD}'{label}'{RESET} issues "
        f"from project {BOLD}{project}{RESET}..."
    )

    _sc = _split_candidates
    custom_fields = (
        _sc(settings.jira_repo_url_fields)
        + _sc(settings.jira_repo_name_fields)
        + _sc(settings.jira_reviewer_fields)
    )
    base_fields = ["summary", "description", "labels", "status", "priority", "assignee"]
    fields_list = list(dict.fromkeys(base_fields + custom_fields))

    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(
            f"{client.base_url}/rest/api/3/search/jql",
            headers=client.headers,
            json={
                "jql": (
                    f'project = "{project}" AND labels = "{label}" '
                    f"AND statusCategory != Done ORDER BY priority DESC"
                ),
                "fields": fields_list,
                "maxResults": 50,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    issues = []
    for item in data.get("issues", []):
        fields = item.get("fields", {})

        repo_url = repo_url_override or _extract_first_text_field(
            fields, _split_candidates(settings.jira_repo_url_fields)
        )
        repo_name = _extract_first_text_field(
            fields, _split_candidates(settings.jira_repo_name_fields)
        )
        reviewers = _extract_text_list(fields, _split_candidates(settings.jira_reviewer_fields))

        desc_text = _extract_adf_text(fields.get("description"))

        # Fallback description parsing
        branch: str | None = None
        if not repo_url or not reviewers:
            metadata = _parse_description_metadata(desc_text)
            if not repo_url and metadata.get("repo_url"):
                repo_url = metadata.get("repo_url")
            if not reviewers and metadata.get("desc_reviewers"):
                reviewers = metadata.get("desc_reviewers")
            if not branch and metadata.get("branch"):
                branch = metadata.get("branch")

        # Extract branch from repo_url if not already present
        if repo_url and not branch:
            clean_url, parsed_branch = parse_repo_url(repo_url)
            repo_url = clean_url
            branch = parsed_branch

        if not reviewers:
            reviewers = _coerce_text_values(fields.get("assignee"))
        reviewers = _dedupe_strings(reviewers)

        issues.append(
            {
                "key": item["key"],
                "summary": fields.get("summary", ""),
                "description": desc_text[:120],
                "status": fields.get("status", {}).get("name", ""),
                "priority": fields.get("priority", {}).get("name", ""),
                "repo_url": repo_url,
                "repo_name": repo_name,
                "requested_reviewers": reviewers,
                "branch": branch,
            }
        )

    return issues


async def fetch_manual_issue(issue_key: str, repo_url_override: str | None = None) -> dict:
    """Fetch a specific issue by key from Jira."""
    from src.integrations.jira.jira_client import get_jira_issue

    print(f"\n  {CYAN}📥{RESET}  Fetching {BOLD}{issue_key}{RESET} from Jira...")
    data = await get_jira_issue(issue_key)

    # Extract branch from repo_url if not already present
    repo_url = repo_url_override or data.get("repo_url")
    branch = data.get("branch")
    if repo_url and not branch:
        clean_url, parsed_branch = parse_repo_url(repo_url)
        repo_url = clean_url
        branch = parsed_branch

    return {
        "key": issue_key,
        "summary": data.get("summary", ""),
        "description": data.get("description", "")[:120],
        "status": data.get("status", ""),
        "priority": "?",
        "repo_url": repo_url,
        "repo_name": data.get("repo_name"),
        "requested_reviewers": data.get("requested_reviewers", []),
        "branch": branch,
    }
