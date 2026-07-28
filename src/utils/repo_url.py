"""Repo URL parsing utilities."""

import re


def _clean_branch(branch: str) -> str | None:
    """Strip ADF noise accidentally concatenated onto a branch suffix."""
    cleaned = re.sub(r"(?i)(?<=[\w-])requested.*$", "", branch.strip())
    return cleaned or None


def parse_repo_url(repo_url: str) -> tuple[str, str | None]:
    """
    Parse a repo URL and extract an optional branch.

    Supports:
    - https://github.com/org/repo.git
    - https://github.com/org/repo.git#feature-branch
    - https://github.com/org/repo/tree/feature-branch
    """
    url = repo_url.strip()
    branch: str | None = None

    if "#" in url:
        url, branch = url.split("#", 1)
        branch = _clean_branch(branch)
    elif "/tree/" in url:
        url, remainder = url.split("/tree/", 1)
        branch = _clean_branch(remainder.split("/")[0])

    url = url.rstrip("/").removesuffix(".git")
    return url, branch
