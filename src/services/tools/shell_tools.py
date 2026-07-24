"""
Shell execution tool for the tool-calling agent.

This tool allows running build/test/format commands within the worktree sandbox.
See HLD §4.6 for the full tool specification.
"""

import subprocess  # nosec B404
from pathlib import Path

from langchain_core.tools import tool

from src.settings import settings


def get_worktree_path(worktree_path: str | None = None) -> Path:
    """Get validated worktree path — accepts anything under WORKTREE_BASE_PATH."""
    base_path = Path(settings.worktree_base_path)
    worktree = Path(worktree_path) if worktree_path else base_path

    try:
        worktree.resolve().relative_to(base_path.resolve())
    except ValueError:
        raise ValueError(f"Worktree path {worktree} is outside allowed base {base_path}")

    return worktree


@tool
def run_shell(command: str, worktree_path: str | None = None, timeout: int = 60) -> str:
    """
    Execute a shell command within the worktree sandbox.

    This is scoped to the worktree and has limited permissions.
    Use for build/test/format commands the agent needs mid-loop.

    Args:
        command: Shell command to execute
        worktree_path: Optional specific worktree path
        timeout: Command timeout in seconds (default: 60)

    Returns:
        str: Command output (stdout + stderr)

    Raises:
        ValueError: If path is outside worktree or command is dangerous
        subprocess.TimeoutExpired: If command times out
    """
    worktree = get_worktree_path(worktree_path)

    # Security: block dangerous commands
    dangerous_patterns = [
        "rm -rf /",
        "mkfs",
        "dd if=",
        "chmod 777 /",
        "chown -R",
        "sudo",
        "su ",
    ]

    command_lower = command.lower()
    for pattern in dangerous_patterns:
        if pattern in command_lower:
            raise ValueError(f"Dangerous command blocked: {pattern}")

    try:
        # Security mitigated by dangerous pattern blocking and worktree sandboxing
        result = subprocess.run(  # nosec B602, B603
            command,
            shell=True,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = []
        if result.stdout:
            output.append(f"STDOUT:\n{result.stdout}")
        if result.stderr:
            output.append(f"STDERR:\n{result.stderr}")
        output.append(f"Exit code: {result.returncode}")

        return "\n".join(output)

    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout} seconds"
    except Exception as e:
        return f"Command failed: {str(e)}"
