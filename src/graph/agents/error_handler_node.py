"""
Error Handler Node - handles failures and cleanup.

See HLD §9 for full specification.
"""

from src.graph.state.execution_state import ExecutionState


def error_handler_node(state: ExecutionState) -> ExecutionState:
    """
    Handle execution failure with cleanup and notification.

    This node:
    - Posts diagnostic comment to Jira
    - Releases repo lock
    - Preserves worktree/branch for debugging (on sanity check failures)
    - Writes failure state to Execution Memory
    - Updates execution status to paused (for manual fix) or failed

    Args:
        state: Current execution state

    Returns:
        Updated execution state with failure status
    """
    # TODO: Implement error handler logic
    # - Post diagnostic to Jira
    # - Release lock
    # - Cleanup worktree/branch (only on true failures, not sanity check issues)
    # - Write to Execution Memory
    # - Update status

    # Check if this is a sanity check failure (allow manual fix)
    sanity_result = state.get("sanity_check_result") or {}
    if not sanity_result.get("passed", False):
        # Sanity check failed - pause for manual fix instead of failing
        state["status"] = "paused"
        diagnostics = sanity_result.get("diagnostics") or []
        error_msg = diagnostics[0].get("message") if diagnostics else "Unknown linting error"
        state["error"] = f"Sanity check failed - manual fix required: {error_msg}"
        # Preserve worktree for manual fixes
    else:
        # True failure - set to failed
        state["status"] = "failed"
        state["error"] = state.get("error", "Unknown error")

    return state
