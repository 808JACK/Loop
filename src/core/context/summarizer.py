"""Context summarization helpers for execution workflow nodes."""

from __future__ import annotations

from typing import Any


def _stringify(value: Any, max_len: int = 240) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[: max_len - 3].rstrip() + "..."
    return text


def _summarize_list(values: Any, max_items: int = 4, max_len: int = 160) -> list[str]:
    if not isinstance(values, list):
        return []
    items: list[str] = []
    for value in values[:max_items]:
        if isinstance(value, dict):
            parts = []
            for key in ("name", "summary", "description", "goal", "outcome", "issue_key", "repo"):
                if value.get(key):
                    parts.append(_stringify(value.get(key), max_len=max_len))
            if not parts:
                parts.append(_stringify(value, max_len=max_len))
            items.append(" | ".join(parts))
        else:
            items.append(_stringify(value, max_len=max_len))
    return [item for item in items if item]


def summarize_project_memory(project_memory: dict[str, Any] | None) -> str:
    """Summarize the project memory dictionary into a concise text representation."""
    if not project_memory:
        return "No project memory available."

    parts: list[str] = []

    # Static project structure (always include at top for context)
    project_structure = project_memory.get("project_structure")
    if project_structure:
        parts.append(f"Project Structure: {_stringify(project_structure, 500)}")

    # Project type
    project_type = project_memory.get("project_type")
    if project_type:
        parts.append(f"Project type: {_stringify(project_type)}")

    # Use the pre-computed compact summary for token efficiency (execution patterns)
    compact_summary = project_memory.get("compact_summary")
    if compact_summary:
        parts.append(f"Execution Patterns: {_stringify(compact_summary, 400)}")

    # Architecture summary
    architecture = project_memory.get("architecture_summary")
    if architecture:
        parts.append(f"Architecture: {_stringify(architecture, 320)}")

    module_summaries = project_memory.get("module_summaries")
    if isinstance(module_summaries, dict) and module_summaries:
        summaries = []
        for name, summary in list(module_summaries.items())[:3]:
            summaries.append(f"{name}: {_stringify(summary, 160)}")
        parts.append("Modules: " + "; ".join(summaries))

    conventions = _summarize_list(project_memory.get("conventions"), max_items=4, max_len=120)
    if conventions:
        parts.append("Conventions: " + "; ".join(conventions))

    dependency_graph_ref = project_memory.get("dependency_graph_ref")
    if dependency_graph_ref:
        parts.append(f"Dependency graph ref: {_stringify(dependency_graph_ref, 200)}")

    return (
        "\n".join(parts)
        if parts
        else "Project memory present, but no concise summary could be derived."
    )


def summarize_execution_memory(execution_memory: dict[str, Any] | None) -> str:
    """Summarize the execution memory dictionary into a concise text representation."""
    if not execution_memory:
        return "No execution memory available."

    parts: list[str] = []
    issue_key = execution_memory.get("issue_key")
    if issue_key:
        parts.append(f"Issue: {_stringify(issue_key)}")

    executions = execution_memory.get("executions")
    if isinstance(executions, list) and executions:
        latest = executions[-1]
        if isinstance(latest, dict):
            outcome = latest.get("outcome")
            roadmap_summary = latest.get("roadmap_summary")
            completed_at = latest.get("completed_at")
            if outcome:
                parts.append(f"Latest outcome: {_stringify(outcome)}")
            if roadmap_summary:
                parts.append(f"Latest roadmap summary: {_stringify(roadmap_summary, 220)}")
            if completed_at:
                parts.append(f"Completed at: {_stringify(completed_at)}")
        parts.append(f"Prior executions: {len(executions)}")

    related_links = _summarize_list(
        execution_memory.get("related_issue_links"), max_items=5, max_len=80
    )
    if related_links:
        parts.append("Related issues: " + "; ".join(related_links))

    return (
        "\n".join(parts)
        if parts
        else "Execution memory present, but no concise summary could be derived."
    )


def summarize_phase_plan(phase_plan: dict[str, Any] | None) -> str:
    """Summarize the current phase plan dictionary into a concise text representation."""
    if not phase_plan:
        return "No current phase plan available."

    parts: list[str] = []
    phase_name = phase_plan.get("phase_name")
    if phase_name:
        parts.append(f"Phase: {_stringify(phase_name)}")

    goal = phase_plan.get("goal")
    if goal:
        parts.append(f"Goal: {_stringify(goal, 260)}")

    acceptance_criteria = _summarize_list(
        phase_plan.get("acceptance_criteria"), max_items=5, max_len=160
    )
    if acceptance_criteria:
        parts.append("Acceptance criteria: " + "; ".join(acceptance_criteria))

    steps = phase_plan.get("steps")
    if isinstance(steps, list) and steps:
        step_summaries = []
        for step in steps[:5]:
            if isinstance(step, dict):
                desc = _stringify(step.get("description"), 140)
                target = _stringify(step.get("target_path"), 80)
                if target:
                    step_summaries.append(f"{desc} -> {target}")
                else:
                    step_summaries.append(desc)
            else:
                step_summaries.append(_stringify(step, 140))
        parts.append("Steps: " + " | ".join(step_summaries))

    return (
        "\n".join(parts)
        if parts
        else "Phase plan present, but no concise summary could be derived."
    )


def build_context_primer(state: dict[str, Any]) -> dict[str, Any]:
    """Build a context primer from the execution state to pass to the agent."""
    roadmap = state.get("roadmap") or {}
    current_index = state.get("current_phase_index", 0)
    phases = roadmap.get("phases", []) if isinstance(roadmap, dict) else []
    current_phase = (
        phases[current_index] if isinstance(phases, list) and current_index < len(phases) else None
    )

    return {
        "issue": {
            "key": state.get("issue_key", ""),
            "summary": _stringify(state.get("issue_summary", ""), 220),
        },
        "project_memory_summary": summarize_project_memory(state.get("project_memory")),
        "roadmap_summary": (
            _stringify(roadmap.get("summary", ""), 240) if isinstance(roadmap, dict) else ""
        ),
        "phase_focus": {
            "index": current_index + 1,
            "total": len(phases) if isinstance(phases, list) else 0,
            "name": (
                _stringify(current_phase.get("name"), 160)
                if isinstance(current_phase, dict)
                else ""
            ),
            "goal": (
                _stringify(current_phase.get("goal"), 240)
                if isinstance(current_phase, dict)
                else ""
            ),
        },
        "current_phase_plan_summary": summarize_phase_plan(state.get("current_phase_plan")),
        "notes": [
            "Use the summary fields as the working context.",
            "Do not expand raw saved memory blobs into the agent prompt.",
            "Pull additional details via repository tools when needed.",
        ],
    }
