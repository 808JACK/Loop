"""Project Memory Service - manages per-repo knowledge store."""

import os
import subprocess  # nosec B404
from typing import Any, cast

from sqlalchemy.orm import Session

from src.core.database.base import get_db
from src.models.project_memory import ProjectMemory
from src.utils import normalize_paths

# Compact the rolling window once we accumulate this many entries
PROJECT_MEMORY_COMPACT_AFTER: int = 3
# How many recent entries to keep after compaction
PROJECT_MEMORY_RECENT_WINDOW: int = 5


def analyze_project_structure(worktree_path: str) -> str:
    """
    Analyze the repository structure and create a compact summary.

    Args:
        worktree_path: Path to the repository worktree

    Returns:
        Compact string summary of project structure
    """
    if not worktree_path or not os.path.exists(worktree_path):
        return "Project structure not available."

    try:
        # Get directory tree structure
        result = subprocess.run(  # nosec B603, B607
            [
                "find",
                ".",
                "-not",
                "-path",
                "./.git*",
                "-not",
                "-path",
                "./node_modules*",
                "-not",
                "-path",
                "./.ai-sdlc*",
                "-not",
                "-path",
                "./venv*",
                "-not",
                "-path",
                "./.venv*",
                "-type",
                "d",
            ],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=10,
        )

        dirs = result.stdout.strip().splitlines()

        # Get file types by extension
        file_result = subprocess.run(  # nosec B603, B607
            [
                "find",
                ".",
                "-not",
                "-path",
                "./.git*",
                "-not",
                "-path",
                "./node_modules*",
                "-not",
                "-path",
                "./.ai-sdlc*",
                "-not",
                "-path",
                "./venv*",
                "-not",
                "-path",
                "./.venv*",
                "-type",
                "f",
            ],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=10,
        )

        files = file_result.stdout.strip().splitlines()

        # Analyze structure
        structure_parts = []

        # Top-level directories
        top_level = set()
        for d in dirs:
            parts = d.split("/")
            if len(parts) == 2 and parts[1]:  # ./dirname
                top_level.add(parts[1])

        if top_level:
            structure_parts.append(f"Top-level dirs: {', '.join(sorted(top_level))}")

        # File type analysis
        extensions: dict[str, int] = {}
        for f in files:
            ext = f.split(".")[-1] if "." in f else "no-ext"
            extensions[ext] = extensions.get(ext, 0) + 1

        if extensions:
            top_exts = sorted(extensions.items(), key=lambda x: -x[1])[:5]
            ext_summary = ", ".join(f".{ext} ({count})" for ext, count in top_exts)
            structure_parts.append(f"File types: {ext_summary}")

        # Total counts
        structure_parts.append(f"Total dirs: {len(dirs)}, files: {len(files)}")

        return ". ".join(structure_parts)

    except Exception as e:
        return f"Project structure analysis failed: {str(e)}"


def _path_bucket(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return "repo"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return "/".join(parts)
    return "/".join(parts[:2])


def _build_architecture_summary(
    project_type: str | None, recent_changes: list[dict[str, Any]]
) -> str:
    buckets: dict[str, int] = {}
    for change in recent_changes[:10]:
        for path in normalize_paths(change.get("touched_paths")):
            bucket = _path_bucket(path)
            buckets[bucket] = buckets.get(bucket, 0) + 1

    if buckets:
        top_buckets = sorted(buckets.items(), key=lambda item: (-item[1], item[0]))[:5]
        bucket_text = ", ".join(f"{bucket} ({count})" for bucket, count in top_buckets)
        return (
            f"{project_type or 'project'} repo with recurring work concentrated in {bucket_text}."
        )

    if project_type:
        return (
            f"{project_type} repo with compact merge-tracked memory "
            "and no stable hotspot summary yet."
        )
    return "Repo memory captured, but architecture summary has not been compacted yet."


def _build_compact_summary(
    recent_changes: list[dict[str, Any]], project_structure: str | None = None
) -> str:
    """Build a token-efficient text summary of recent patterns."""
    if not recent_changes:
        return "No execution history available."

    # Build summary
    summary_parts = []

    # Include project structure if available
    if project_structure:
        summary_parts.append(f"Structure: {project_structure}")

    # Extract patterns instead of detailed records
    risk_levels = []
    for change in recent_changes:
        risk = change.get("risk_level", "medium")
        if risk not in risk_levels:
            risk_levels.append(risk)

    # Count phases
    total_phases = sum(change.get("phases_count", 1) for change in recent_changes)
    avg_phases = total_phases / len(recent_changes) if recent_changes else 1

    summary_parts.append(f"Recent {len(recent_changes)} executions completed.")

    if risk_levels:
        risk_summary = f"Risk profile: {', '.join(risk_levels)}"
        summary_parts.append(risk_summary)

    if avg_phases > 1:
        summary_parts.append(f"Average {avg_phases:.1f} phases per execution.")

    # Pattern insights
    all_paths = []
    for change in recent_changes:
        all_paths.extend(normalize_paths(change.get("touched_paths", [])))

    if all_paths:
        path_counts: dict[str, int] = {}
        for path in all_paths:
            path_counts[path] = path_counts.get(path, 0) + 1
        top_paths = sorted(path_counts.items(), key=lambda x: (-x[1], x[0]))[:3]
        if top_paths:
            paths_text = ", ".join(f"{p} ({c})" for p, c in top_paths)
            summary_parts.append(f"Most touched: {paths_text}")

    return ". ".join(summary_parts) + "."


def _build_module_summaries(recent_changes: list[dict[str, Any]]) -> dict[str, str]:
    bucket_counts: dict[str, int] = {}
    for change in recent_changes[:20]:
        for path in normalize_paths(change.get("touched_paths")):
            bucket = _path_bucket(path)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    module_summaries: dict[str, str] = {}
    for bucket, count in sorted(bucket_counts.items(), key=lambda item: (-item[1], item[0]))[:8]:
        module_summaries[bucket] = (
            f"Seen in {count} merged execution(s); stable hotspot for this repo."
        )
    return module_summaries


def _merge_conventions(existing: Any, recent_changes: list[dict[str, Any]]) -> list[str]:
    conventions: list[str] = []
    if isinstance(existing, list):
        for item in existing:
            value = str(item).strip()
            if value and value not in conventions:
                conventions.append(value)

    buckets = set()
    for change in recent_changes[:20]:
        for path in normalize_paths(change.get("touched_paths")):
            buckets.add(_path_bucket(path))

    inferred = []
    if any(bucket.startswith("src") for bucket in buckets):
        inferred.append("Source changes usually land under src/* feature areas.")
    if any("test" in bucket.lower() for bucket in buckets):
        inferred.append("Tests are grouped near the touched feature area.")
    if any(bucket.startswith("app") or bucket.startswith("backend") for bucket in buckets):
        inferred.append("Backend-oriented changes stay grouped by feature module.")

    for item in inferred:
        if item not in conventions:
            conventions.append(item)

    return conventions[:10]


def _upsert_recent_change(
    recent_changes: Any, execution_data: dict[str, Any]
) -> list[dict[str, Any]]:
    records = list(recent_changes) if isinstance(recent_changes, list) else []
    issue_key = str(execution_data.get("issue_key") or "").strip()
    pr_url = str(execution_data.get("pr_url") or "").strip()

    updated = []
    for record in records:
        if not isinstance(record, dict):
            continue
        same_issue = issue_key and str(record.get("issue_key") or "").strip() == issue_key
        same_pr = pr_url and str(record.get("pr_url") or "").strip() == pr_url
        if same_issue or same_pr:
            continue
        updated.append(record)

    updated.insert(0, execution_data)
    return updated[:10]


def _compact_project_memory(memory: ProjectMemory) -> None:
    history_source = list(memory.recent_changes or [])
    if not history_source:
        return

    if (
        len(history_source) >= PROJECT_MEMORY_COMPACT_AFTER
        and len(history_source) > PROJECT_MEMORY_RECENT_WINDOW
    ):
        memory.recent_changes = history_source[-PROJECT_MEMORY_RECENT_WINDOW:]  # type: ignore[assignment]

    summary_source = list(memory.recent_changes or history_source)
    memory.architecture_summary = _build_architecture_summary(memory.project_type, summary_source)  # type: ignore[assignment, arg-type]

    module_summaries = _build_module_summaries(summary_source)
    if module_summaries:
        memory.module_summaries = module_summaries  # type: ignore[assignment]

    memory.conventions = _merge_conventions(memory.conventions, summary_source)  # type: ignore[assignment]

    # Add compact text summary for token efficiency (includes project structure)
    memory.compact_summary = _build_compact_summary(summary_source, memory.project_structure)  # type: ignore[assignment, arg-type]


def _get_project_memory_record(db: Session, repo_url: str) -> ProjectMemory | None:
    if not repo_url:
        return None
    url = str(repo_url).strip().rstrip("/")
    candidates = [url, url + ".git"]
    if url.endswith(".git"):
        candidates.append(url[:-4])

    # Also try with/without trailing slash for robustness
    additional_candidates = []
    for candidate in candidates:
        if not candidate.endswith("/"):
            additional_candidates.append(candidate + "/")
        if candidate.endswith("/"):
            additional_candidates.append(candidate.rstrip("/"))

    candidates.extend(additional_candidates)

    # Remove duplicates while preserving order
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    res = db.query(ProjectMemory).filter(ProjectMemory.repo.in_(unique_candidates)).first()
    return cast(ProjectMemory | None, res)


def get_project_memory(repo_url: str) -> dict[str, Any] | None:
    """
    Retrieve project memory for a repository.

    Args:
        repo_url: Repository URL

    Returns:
        Project memory dict or None if not found
    """
    db = next(get_db())
    try:
        memory = _get_project_memory_record(db, repo_url)
        if memory:
            return memory.to_dict()
        return None
    finally:
        db.close()


def update_project_memory(repo_url: str, updates: dict[str, Any]) -> bool:
    """
    Update project memory for a repository.

    Args:
        repo_url: Repository URL
        updates: Dictionary of fields to update

    Returns:
        True if successful, False otherwise
    """
    db = next(get_db())
    try:
        memory = _get_project_memory_record(db, repo_url)

        if not memory:
            # Create new memory entry
            memory = ProjectMemory(repo=repo_url)
            db.add(memory)

        # Update fields
        for key, value in updates.items():
            if hasattr(memory, key):
                setattr(memory, key, value)

        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def update_project_structure(repo_url: str, worktree_path: str) -> bool:
    """
    Update the static project structure in project memory.

    Args:
        repo_url: Repository URL
        worktree_path: Path to the repository worktree

    Returns:
        True if successful, False otherwise
    """
    structure = analyze_project_structure(worktree_path)
    return update_project_memory(repo_url, {"project_structure": structure})


def add_execution_to_project_memory(repo_url: str, execution_data: dict[str, Any]) -> bool:
    """
    Add execution data to project memory's recent_changes.

    This keeps the repo history compact by upserting the latest record per issue.

    Args:
        repo_url: Repository URL
        execution_data: Execution data to add

    Returns:
        True if successful, False otherwise
    """
    db = next(get_db())
    try:
        memory = _get_project_memory_record(db, repo_url)

        if not memory:
            memory = ProjectMemory(repo=repo_url)
            db.add(memory)
        else:
            pass

        if memory.recent_changes is None:
            memory.recent_changes = []  # type: ignore[assignment]

        execution_data = dict(execution_data)
        execution_data.setdefault("touched_paths", [])
        execution_data["touched_paths"] = normalize_paths(execution_data.get("touched_paths"))
        memory.recent_changes = _upsert_recent_change(memory.recent_changes, execution_data)  # type: ignore[assignment]
        _compact_project_memory(memory)

        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def compact_project_memory_on_merge(
    repo_url: str,
    issue_key: str,
    summary: str,
    touched_paths: list[str] | None = None,
    pr_url: str | None = None,
) -> bool:
    """Compact the repo memory after a successful merge.

    This rolls the latest execution into the durable project-level summary so
    future runs can reuse a concise repo brain instead of re-reading the whole
    codebase.
    """
    db = next(get_db())
    try:
        memory = _get_project_memory_record(db, repo_url)
        if not memory:
            memory = ProjectMemory(repo=repo_url)
            db.add(memory)

        if memory.recent_changes is None:
            memory.recent_changes = []  # type: ignore[assignment]

        touched_paths = normalize_paths(touched_paths or [])
        existing_changes = list(memory.recent_changes or [])
        if not touched_paths:
            for record in existing_changes:
                if (
                    isinstance(record, dict)
                    and str(record.get("issue_key") or "").strip() == issue_key
                ):
                    touched_paths = normalize_paths(record.get("touched_paths"))
                    break

        merged_record = {
            "issue_key": issue_key,
            "summary": summary,
            "touched_paths": touched_paths,
            "pr_url": pr_url,
            "merged_at": __import__("datetime").datetime.utcnow().isoformat(),
        }
        memory.recent_changes = _upsert_recent_change(existing_changes, merged_record)  # type: ignore[assignment]
        _compact_project_memory(memory)

        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()
