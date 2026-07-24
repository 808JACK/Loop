"""
Git Manager - handles git and PR operations.

See HLD §4.8 for full specification.
"""

import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from src.settings import settings


@dataclass
class PRInfo:
    """Information about a created PR."""

    pr_url: str
    branch_name: str
    reviewers: list[str]


class GitManager:
    """
    Manages git operations and PR creation.

    Supports GitHub, GitLab, and Bitbucket based on configuration.
    """

    def __init__(self, repo_url: str, worktree_path: str):
        """
        Initialize Git Manager.

        Args:
            repo_url: Git repository URL
            worktree_path: Path to the worktree
        """
        self.repo_url = repo_url
        self.worktree_path = Path(worktree_path)

    def create_branch(self, branch_name: str) -> str:
        """
        Create and checkout a new branch.

        Args:
            branch_name: Name of the branch to create

        Returns:
            str: Branch name
        """
        subprocess.run(  # nosec B603, B607
            ["git", "checkout", "-b", branch_name],
            cwd=self.worktree_path,
            check=True,
        )
        return branch_name

    def commit_changes(self, message: str) -> str:
        """
        Commit changes with a message.

        Args:
            message: Commit message

        Returns:
            str: Commit hash
        """
        add_cmd = [
            "git",
            "add",
            "-A",
            "--",
            ".",
            ":(exclude).ai-sdlc",
            ":(exclude).ai-sdlc/**",
            ":(exclude)runs",
            ":(exclude)runs/**",
        ]
        subprocess.run(  # nosec B603, B607
            add_cmd,
            cwd=self.worktree_path,
            check=True,
        )

        status = subprocess.run(  # nosec B603, B607
            ["git", "status", "--porcelain"],
            cwd=self.worktree_path,
            check=True,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            return "NO_CHANGES"

        # Ensure git identity is set before committing
        # This is handled by git_manager_node in the actual workflow

        result = subprocess.run(  # nosec B603, B607
            ["git", "commit", "-m", message],
            cwd=self.worktree_path,
            check=True,
            capture_output=True,
            text=True,
        )

        commit_hash = result.stdout.strip().split()[-1] if result.stdout.strip() else ""
        return commit_hash

    def push_branch(self, branch_name: str) -> str:
        """
        Push branch to remote.

        Args:
            branch_name: Branch name to push

        Returns:
            str: Push result
        """
        subprocess.run(  # nosec B603, B607
            ["git", "push", "-u", "origin", branch_name],
            cwd=self.worktree_path,
            check=True,
        )
        return f"Pushed {branch_name}"

    def create_pr(
        self,
        title: str,
        description: str,
        branch_name: str,
        reviewers: list[str] | None = None,
    ) -> PRInfo:
        """
        Create a draft PR.

        Args:
            title: PR title
            description: PR description (from roadmap)
            branch_name: Branch name
            reviewers: List of reviewer usernames

        Returns:
            PRInfo: PR information including URL
        """
        if settings.git_platform == "github":
            return self._create_github_pr(title, description, branch_name, reviewers)
        elif settings.git_platform == "gitlab":
            return self._create_gitlab_pr(title, description, branch_name, reviewers)
        elif settings.git_platform == "bitbucket":
            return self._create_bitbucket_pr(title, description, branch_name, reviewers)
        else:
            raise ValueError(f"Unsupported git platform: {settings.git_platform}")

    def _create_github_pr(
        self,
        title: str,
        description: str,
        branch_name: str,
        reviewers: list[str] | None = None,
    ) -> PRInfo:
        """Create a GitHub PR using the GitHub API."""
        raise NotImplementedError("GitHub API integration not yet implemented")

    def _create_gitlab_pr(
        self,
        title: str,
        description: str,
        branch_name: str,
        reviewers: list[str] | None = None,
    ) -> PRInfo:
        """Create a GitLab MR using the GitLab API."""
        raise NotImplementedError("GitLab API integration not yet implemented")

    def _create_bitbucket_pr(
        self,
        title: str,
        description: str,
        branch_name: str,
        reviewers: list[str] | None = None,
    ) -> PRInfo:
        """Create a Bitbucket PR using the Bitbucket API."""
        raise NotImplementedError("Bitbucket API integration not yet implemented")

    def get_reviewers(self, changed_files: list[str]) -> list[str]:
        """
        Determine reviewers based on changed files.

        Uses CODEOWNERS, git-blame, or round-robin logic.

        Args:
            changed_files: List of changed file paths

        Returns:
            List[str]: List of reviewer usernames
        """
        raise NotImplementedError("Reviewer assignment logic not yet implemented")
