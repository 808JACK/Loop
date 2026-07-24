"""
Roadmap Node — converts a Jira issue into ordered phases.

See HLD §4.3 for specification.
Uses langchain.agents.create_agent pattern with middleware and structured output.
"""

import asyncio
import os

from langchain_core.messages import HumanMessage

from src.core.context.summarizer import summarize_project_memory
from src.core.llm.provider import (
    RateLimitError,
    SessionLimitError,
)
from src.core.logging.logger import get_logger
from src.core.prompt_loader import load_prompt
from src.graph.state.execution_state import ExecutionState
from src.integrations.jira.jira_client import post_jira_comment
from src.utils.json_parser import parse_llm_json

logger = get_logger("roadmap_node")

_SYSTEM = load_prompt("roadmap_system_prompt.md")

_HUMAN = """Issue Key: {issue_key}
Summary: {issue_summary}
Description:
{issue_description}

Actual files in the repository (ONLY reference these in touched_paths_hint, do not invent paths):
{repo_files}

Generate the roadmap."""


def roadmap_node(state: ExecutionState) -> ExecutionState:
    """Generate a phased roadmap from the issue and post it to Jira."""
    logger.info("📍 [STEP 1/6] Starting roadmap generation")
    existing_roadmap = state.get("roadmap") or {}
    if existing_roadmap.get("phases"):
        logger.info("Reusing roadmap from checkpoint instead of regenerating it")
        state["risk_level"] = str(
            existing_roadmap.get("risk_level", state.get("risk_level", "medium"))
        )
        state["status"] = "phase_planning"
        # Keep the saved phase index so resume continues from the correct phase.
        state["current_phase_index"] = state.get("current_phase_index", 0)
        logger.info("✅ [STEP 1/6] Roadmap node completed (reused from checkpoint)")
        return state

    from langchain.agents import create_agent

    from src.core.llm.provider import get_chat_model_for_node

    issue_key = state.get("issue_key", "")
    issue_summary = state.get("issue_summary", "")
    # Cap description at 1500 chars — the model doesn't need the full wall of text
    issue_description = (state.get("issue_description", "") or "")[:1500]
    worktree_path = state.get("worktree_path", "")
    project_memory = state.get("project_memory")

    # Get actual repo files so the roadmap doesn't hallucinate paths
    repo_files = "(repo not cloned yet)"
    if worktree_path and os.path.exists(worktree_path):
        try:
            import subprocess as _sp  # nosec B404

            r = _sp.run(  # nosec B603, B607
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
                    "-type",
                    "f",
                ],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = r.stdout.strip().splitlines()
            if len(lines) > 150:
                repo_files = (
                    "\n".join(lines[:150]) + f"\n... ({len(lines) - 150} more files not shown)"
                )
            else:
                repo_files = "\n".join(lines) or "(empty repo)"
        except Exception:  # nosec B110
            pass

    # Build context from project memory using a summarizer instead of dumping raw state.
    project_context = summarize_project_memory(project_memory)
    if project_memory:
        logger.info("Using summarized project memory for context")
    else:
        logger.info("No project memory available")

    # Build system prompt using compilation
    system_prompt = _SYSTEM.compile(
        project_context=project_context or "No project context available"
    )

    # Build task prompt
    task = _HUMAN.format(
        issue_key=issue_key,
        issue_summary=issue_summary,
        issue_description=issue_description,
        repo_files=repo_files,
    )

    try:
        # Use direct create_agent (no tools for planner)
        llm = get_chat_model_for_node("RoadmapAgent", temperature=0.3)
        agent = create_agent(
            model=llm,
            tools=[],
            middleware=[],  # Planners don't need middleware
            system_prompt=system_prompt,
            name="RoadmapAgent",
            response_format=None,
        )

        result = agent.invoke(
            {"messages": [HumanMessage(content=task)]},
            config={"recursion_limit": 10},
        )

        # Extract structured response with fallback
        structured_response = result.get("structured_response") if hasattr(result, "get") else None
        if structured_response:
            roadmap = {
                "summary": structured_response.summary,
                "risk_level": structured_response.risk_level,
                "confidence": structured_response.confidence,
                "phases": structured_response.phases,
            }
        else:
            # Fallback to parsing messages
            messages = result.get("messages", []) if hasattr(result, "get") else []
            content = messages[-1].content if messages else ""
            roadmap = parse_llm_json(content)

        if not roadmap.get("phases"):
            raise ValueError("No phases in roadmap")
        logger.info(f"Roadmap generated for {issue_key}: {len(roadmap.get('phases', []))} phases")
        # Log the full roadmap so it's visible in terminal
        for i, phase in enumerate(roadmap.get("phases", []), 1):
            comp = phase.get("complexity")
            est = phase.get("estimated_steps")
            logger.info(
                f"  Phase {i}: [{phase.get('name')}] complexity={comp} " f"estimated_steps={est}"
            )
    except SessionLimitError:
        # Session quota exhausted — stop immediately, don't fallback to dummy roadmap
        logger.error("Roadmap node: API session usage limit hit — pausing workflow")
        raise
    except RateLimitError:
        # Per-minute throttle exhausted after retries — propagate to executor
        logger.error("Roadmap node: rate limit exhausted after retries — propagating to executor")
        raise
    except Exception as e:
        logger.warning(f"LLM roadmap generation failed ({e}), using fallback")
        roadmap = {
            "summary": f"Implementation plan for {issue_key}",
            "risk_level": "medium",
            "phases": [
                {
                    "name": "Analysis & exploration",
                    "description": "Understand the codebase and issue scope",
                    "complexity": "low",
                    "estimated_steps": 2,
                    "touched_paths_hint": [],
                },
                {
                    "name": "Implementation",
                    "description": "Make the required code changes",
                    "complexity": "medium",
                    "estimated_steps": 4,
                    "touched_paths_hint": [],
                },
                {
                    "name": "Cleanup & docs",
                    "description": "Update docs, configs, and clean up",
                    "complexity": "low",
                    "estimated_steps": 2,
                    "touched_paths_hint": [],
                },
            ],
            "confidence": 0.5,
        }

    state["roadmap"] = roadmap
    state["risk_level"] = str(roadmap.get("risk_level", "medium"))
    state["status"] = "phase_planning"
    state["current_phase_index"] = 0

    # Write roadmap.md into the worktree so it's visible in the file tree
    worktree_path = state.get("worktree_path", "")
    if worktree_path and os.path.exists(worktree_path):
        _write_roadmap_md(worktree_path, state.get("issue_key", ""), roadmap)

    # ALSO write to runs/<issue_key>/ outside the project for easy viewing
    _write_runs_output(issue_key, "roadmap.md", worktree_path, roadmap)

    # Post roadmap to Jira (non-blocking)
    _post_roadmap_comment(issue_key, roadmap)

    logger.info("✅ [STEP 1/6] Roadmap node completed")
    return state


def _write_roadmap_md(worktree_path: str, issue_key: str, roadmap: dict) -> None:
    """Write .ai-sdlc/roadmap.md into the worktree."""
    try:
        ai_dir = os.path.join(worktree_path, ".ai-sdlc")
        os.makedirs(ai_dir, exist_ok=True)

        lines = [
            f"# Roadmap — {issue_key}",
            "",
            f"**Summary:** {roadmap.get('summary', '')}",
            f"**Risk level:** {roadmap.get('risk_level', 'medium')}",
            f"**Confidence:** {roadmap.get('confidence', 0):.0%}",
            "",
            "## Phases",
            "",
        ]
        for i, phase in enumerate(roadmap.get("phases", []), 1):
            lines += [
                f"### Phase {i}: {phase.get('name', '')}",
                "",
            ]
            if phase.get("goal"):
                lines += [
                    f"**Goal:** {phase.get('goal')}",
                    "",
                ]
            lines += [
                f"**Description:** {phase.get('description', '')}",
                "",
                f"- Complexity: `{phase.get('complexity', 'medium')}`",
                f"- Estimated steps: {phase.get('estimated_steps', '?')}",
                "",
            ]
            if phase.get("touched_paths_hint"):
                lines.append("**Files likely touched:**")
                for p in phase["touched_paths_hint"]:
                    lines.append(f"- `{p}`")
                lines.append("")

            if phase.get("acceptance_criteria"):
                lines.append("**Acceptance Criteria:**")
                for ac in phase["acceptance_criteria"]:
                    lines.append(f"- {ac}")
                lines.append("")

        with open(os.path.join(ai_dir, "roadmap.md"), "w") as f:
            f.write("\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not write roadmap.md: {e}")


def _write_runs_output(issue_key: str, filename: str, worktree_path: str, data: dict) -> None:
    """Write a copy of the generated .md to runs/ for easy viewing."""
    try:
        from src.settings import settings

        # runs/ lives inside worktrees/runs/<issue_key>/
        runs_dir = os.path.join(settings.worktree_base_path, "runs", issue_key)
        os.makedirs(runs_dir, exist_ok=True)

        dst = os.path.join(runs_dir, filename)

        # Prefer the worktree copy when available, but never skip the runs/ mirror.
        if worktree_path and os.path.exists(worktree_path):
            src = os.path.join(worktree_path, ".ai-sdlc", filename)
            if os.path.exists(src):
                import shutil

                shutil.copy2(src, dst)
            elif filename == "roadmap.md":
                with open(dst, "w") as f:
                    f.write("\n".join(_build_roadmap_md_lines(issue_key, data)))
        elif filename == "roadmap.md":
            with open(dst, "w") as f:
                f.write("\n".join(_build_roadmap_md_lines(issue_key, data)))

        if os.path.exists(dst):
            border = "\u2550" * 60
            logger.info(f"\n{border}")
            logger.info(f"  📄  {filename} written to:")
            logger.info(f"  👉  {dst}")
            logger.info(f"{border}\n")
    except Exception as e:
        logger.warning(f"Could not write runs output {filename}: {e}")


def _build_roadmap_md_lines(issue_key: str, roadmap: dict) -> list[str]:
    """Build roadmap markdown content independently of worktree state."""
    lines = [
        f"# Roadmap — {issue_key}",
        "",
        f"**Summary:** {roadmap.get('summary', '')}",
        f"**Risk level:** {roadmap.get('risk_level', 'medium')}",
        f"**Confidence:** {roadmap.get('confidence', 0):.0%}",
        "",
        "## Phases",
        "",
    ]

    for i, phase in enumerate(roadmap.get("phases", []), 1):
        lines += [
            f"### Phase {i}: {phase.get('name', '')}",
            "",
        ]
        if phase.get("goal"):
            lines += [
                f"**Goal:** {phase.get('goal')}",
                "",
            ]
        lines += [
            f"**Description:** {phase.get('description', '')}",
            "",
            f"- Complexity: `{phase.get('complexity', 'medium')}`",
            f"- Estimated steps: {phase.get('estimated_steps', '?')}",
            "",
        ]
        if phase.get("touched_paths_hint"):
            lines.append("**Files likely touched:**")
            for p in phase["touched_paths_hint"]:
                lines.append(f"- `{p}`")
            lines.append("")

        if phase.get("acceptance_criteria"):
            lines.append("**Acceptance Criteria:**")
            for ac in phase["acceptance_criteria"]:
                lines.append(f"- {ac}")
            lines.append("")

    return lines


def _post_roadmap_comment(issue_key: str, roadmap: dict) -> None:
    """Fire-and-forget: post roadmap summary to Jira."""
    try:
        risk = roadmap.get("risk_level", "medium")
        conf = roadmap.get("confidence", 0)
        lines = [
            f"Roadmap generated — risk: {risk} | confidence: {conf:.0%}",
            "",
            f"Summary: {roadmap.get('summary', '')}",
            "",
            "Phases:",
        ]
        for i, phase in enumerate(roadmap.get("phases", []), 1):
            lines.append(f"  {i}. {phase.get('name')} — {phase.get('description', '')}")

        comment = "\n".join(lines)

        # Handle async call properly - check if there's already a running loop
        try:
            asyncio.get_running_loop()
            # If there's already a running loop, create a task
            asyncio.create_task(post_jira_comment(issue_key, comment))
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            asyncio.run(post_jira_comment(issue_key, comment))
    except Exception as e:
        logger.warning(f"Could not post roadmap to Jira: {e}")
