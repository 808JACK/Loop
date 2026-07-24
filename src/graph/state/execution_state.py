"""LangGraph shared state for the execution workflow."""

from typing import Any

from typing_extensions import TypedDict


class ExecutionState(TypedDict, total=False):
    """Shared LangGraph state for the execution workflow."""

    # Execution metadata
    execution_id: str
    issue_key: str
    idempotency_key: str

    # Issue content (fetched from Jira)
    issue_summary: str
    issue_description: str

    # Repo / git
    repo_url: str
    branch: str
    worktree_path: str
    branch_name: str
    pr_url: str | None
    requested_reviewers: list[str] | None

    # Status & risk
    status: str
    risk_level: str

    # Roadmap & planning
    current_phase_index: int
    current_step_index: int
    roadmap: dict[str, Any] | None
    current_phase_plan: dict[str, Any] | None
    phase_plans: list[dict[str, Any]] | None

    # Memory
    project_memory: dict[str, Any] | None
    execution_memory: dict[str, Any] | None

    # Context passed to tool agent
    context_primer: dict[str, Any] | None

    # Retry / error
    retry_count: int
    error: str | None
    last_error: str | None

    # Human-in-the-loop
    awaiting_approval: bool
    review_comments: list[str] | None

    # Phase-level state
    phase_status: str | None
    phase_diff_ref: str | None
    sanity_check_result: dict[str, Any] | None
