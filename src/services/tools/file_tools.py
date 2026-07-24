"""
Repository file tools for the tool-calling agent.

These tools are scoped to the worktree and provide read/write/edit capabilities.
See HLD §4.6 for the full tool specification.
"""

from pathlib import Path

from langchain_core.tools import tool

from src.settings import settings


def get_worktree_path(worktree_path: str | None = None) -> Path:
    """
    Get validated worktree path.

    Accepts anything under WORKTREE_BASE_PATH.
    """
    base_path = Path(settings.worktree_base_path)
    worktree = Path(worktree_path) if worktree_path else base_path

    try:
        worktree.resolve().relative_to(base_path.resolve())
    except ValueError:
        raise ValueError(f"Worktree path {worktree} is outside allowed base {base_path}")

    return worktree


def resolve_path(file_path: str, worktree: Path) -> Path:
    """
    Resolve a file path relative to the worktree.

    Handles both absolute paths (must be inside worktree) and relative paths.
    """
    p = Path(file_path)
    if p.is_absolute():
        # Strip the worktree prefix if present, otherwise use as-is if inside worktree
        try:
            rel = p.relative_to(worktree)
            return worktree / rel
        except ValueError:
            raise ValueError(f"Absolute path {p} is outside worktree {worktree}")
    return worktree / p


@tool
def read_file(path: str, worktree_path: str | None = None) -> str:
    """
    Read a file's contents from the worktree.

    path can be relative to the worktree or an absolute path inside it.
    """
    worktree = get_worktree_path(worktree_path)
    file_path = resolve_path(path, worktree)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    return file_path.read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str, worktree_path: str | None = None) -> str:
    """
    Create or overwrite a file in the worktree.

    path can be relative to the worktree or an absolute path inside it.
    """
    worktree = get_worktree_path(worktree_path)
    file_path = resolve_path(path, worktree)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    return f"Successfully wrote {len(content)} characters to {file_path.relative_to(worktree)}"


@tool
def edit_file(path: str, old_string: str, new_string: str, worktree_path: str | None = None) -> str:
    """
    Perform a targeted find/replace edit on a file.

    path can be relative to the worktree or an absolute path inside it.
    """
    worktree = get_worktree_path(worktree_path)
    file_path = resolve_path(path, worktree)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = file_path.read_text(encoding="utf-8")

    if old_string not in content:
        raise ValueError(f"String not found in file: {path}")

    if content.count(old_string) > 1:
        raise ValueError(f"String appears {content.count(old_string)} times — must be unique")

    file_path.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
    return f"Successfully edited {file_path.relative_to(worktree)}"


@tool
def list_dir(path: str = ".", worktree_path: str | None = None) -> str:
    """
    List directory contents in the worktree.

    path can be relative to the worktree or an absolute path inside it.
    """
    worktree = get_worktree_path(worktree_path)
    dir_path = resolve_path(path, worktree)

    if not dir_path.exists():
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {dir_path}")

    items = []
    for item in sorted(dir_path.iterdir()):
        item_type = "DIR" if item.is_dir() else "FILE"
        items.append(f"{item_type}: {item.name}")

    return "\n".join(items) if items else "(empty directory)"
