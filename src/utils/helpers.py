"""Common utility functions for the application."""

from typing import Any


def normalize_key(value: str) -> str:
    """Normalize a key by stripping whitespace and quotes."""
    return value.strip().strip('"').strip("'")


def normalize_reviewers(reviewers: Any) -> list[str]:
    """Normalize reviewers input to a list of strings."""
    if reviewers is None:
        return []
    if isinstance(reviewers, str):
        return [r.strip() for r in reviewers.split(",") if r.strip()]
    if isinstance(reviewers, list):
        return [str(r).strip() for r in reviewers if r]
    return []


def normalize_paths(paths: Any) -> list[str]:
    """Normalize paths input to a list of strings."""
    if not isinstance(paths, list):
        return []
    cleaned: list[str] = []
    for path in paths:
        if isinstance(path, str):
            path = path.strip()
            if path:
                cleaned.append(path)
    return cleaned


def infer_commit_type(issue_summary: str, roadmap: dict) -> str:
    """Infer conventional commit type from issue summary and roadmap."""
    summary_lower = (issue_summary or "").lower()

    # Check for common patterns
    if any(word in summary_lower for word in ["fix", "bug", "error", "crash", "fail"]):
        return "fix"
    if any(word in summary_lower for word in ["feat", "add", "new", "implement", "create"]):
        return "feat"
    if any(word in summary_lower for word in ["refactor", "clean", "restructure"]):
        return "refactor"
    if any(word in summary_lower for word in ["test", "spec", "validate"]):
        return "test"
    if any(word in summary_lower for word in ["doc", "readme", "comment"]):
        return "docs"
    if any(word in summary_lower for word in ["style", "format", "lint"]):
        return "style"
    if any(word in summary_lower for word in ["perf", "optimize", "speed"]):
        return "perf"

    # Default to feat
    return "feat"


def format_commit_message(issue_key: str, issue_summary: str, roadmap: dict) -> str:
    """Create a readable conventional-commit style message."""
    commit_type = infer_commit_type(issue_summary, roadmap)
    summary = " ".join((issue_summary or "").split()).strip()

    if summary:
        return f"{commit_type}: {summary} ({issue_key})"
    return f"{commit_type}: {issue_key}"
