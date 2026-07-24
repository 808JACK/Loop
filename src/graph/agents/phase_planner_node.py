"""
Phase Planner Node — converts a roadmap phase into concrete steps.

See HLD §4.4 for specification.
Uses langchain.agents.create_agent pattern with middleware and structured output.
"""

import asyncio
import os

from langchain_core.messages import HumanMessage

from src.core.llm.provider import (
    RateLimitError,
    SessionLimitError,
)
from src.core.logging.logger import get_logger
from src.core.prompt_loader import load_prompt
from src.graph.state.execution_state import ExecutionState
from src.integrations.jira.jira_client import post_jira_comment
from src.utils.json_parser import parse_llm_json

logger = get_logger("phase_planner_node")

_SYSTEM = load_prompt("phase_planner_system_prompt.md")

_HUMAN = """Phase {phase_index}: {phase_name}
Description: {phase_description}
Goal: {phase_goal}
Complexity: {complexity}
Estimated steps: {estimated_steps}

Issue: {issue_key} — {issue_summary}

Actual files in the repository (ONLY reference these, do not invent paths):
{repo_files}

Generate the detailed phase plan steps."""


def phase_planner_node(state: ExecutionState) -> ExecutionState:
    """Plan steps for all phases up-front, and retrieve the current phase plan."""
    logger.info("📍 [STEP 3/6] Starting phase planning")
    roadmap = state.get("roadmap") or {}
    phases = roadmap.get("phases", [])
    current_index = state.get("current_phase_index", 0)

    if current_index >= len(phases):
        # All phases done — transition to git manager
        state["status"] = "git_manager"
        state["current_phase_plan"] = None
        logger.info("✅ [STEP 3/6] Phase planner node completed (all phases done)")
        return state

    from langchain.agents import create_agent

    from src.core.llm.provider import get_chat_model_for_node

    issue_key = state.get("issue_key", "")
    issue_summary = state.get("issue_summary", "")
    worktree_path = state.get("worktree_path", "")

    # Check if we need to plan phases up-front, or fill in any missing plans on resume.
    phase_plans = state.get("phase_plans") or []
    if len(phase_plans) < len(phases):
        if not phase_plans:
            logger.info(f"🧭 Generating plans for all {len(phases)} phases up-front...")
        else:
            logger.warning(
                f"🧭 Phase plan list is incomplete ({len(phase_plans)}/{len(phases)}); "
                "generating missing plans before continuing"
            )

        # Get actual repo files to prevent hallucinated paths.
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

        for idx in range(len(phase_plans), len(phases)):
            phase = phases[idx]
            logger.info(f"📝 Planning Phase {idx + 1}/{len(phases)}: [{phase.get('name')}]")
            try:
                # Build system prompt using compilation
                system_prompt = _SYSTEM.compile(
                    phase_name=phase.get("name", ""),
                    phase_description=phase.get("description", ""),
                    phase_goal=phase.get("goal", ""),
                    complexity=phase.get("complexity", "medium"),
                    estimated_steps=phase.get("estimated_steps", 3),
                    issue_key=issue_key,
                    issue_summary=issue_summary,
                    repo_files=repo_files,
                )

                # Build task prompt
                task = _HUMAN.format(
                    phase_index=idx + 1,
                    phase_name=phase.get("name", ""),
                    phase_description=phase.get("description", ""),
                    phase_goal=phase.get("goal", ""),
                    complexity=phase.get("complexity", "medium"),
                    estimated_steps=phase.get("estimated_steps", 3),
                    issue_key=issue_key,
                    issue_summary=issue_summary,
                    repo_files=repo_files,
                )

                # Use direct create_agent (no tools for planner)
                llm = get_chat_model_for_node(f"PhasePlanner-{idx + 1}", temperature=0.3)
                agent = create_agent(
                    model=llm,
                    tools=[],
                    middleware=[],  # Planners don't need middleware
                    system_prompt=system_prompt,
                    name=f"PhasePlanner-{idx + 1}",
                    response_format=None,
                )

                result = agent.invoke(
                    {"messages": [HumanMessage(content=task)]},
                    config={"recursion_limit": 10},
                )

                # Extract structured response with fallback
                structured_response = (
                    result.get("structured_response") if hasattr(result, "get") else None
                )
                if structured_response:
                    plan = {
                        "phase_id": structured_response.phase_id,
                        "phase_name": structured_response.phase_name,
                        "goal": structured_response.goal,
                        "acceptance_criteria": structured_response.acceptance_criteria,
                        "steps": structured_response.steps,
                    }
                else:
                    # Fallback to parsing messages
                    messages = result.get("messages", []) if hasattr(result, "get") else []
                    content = messages[-1].content if messages else ""
                    try:
                        plan = parse_llm_json(content)
                    except Exception:
                        raise

                if not plan.get("steps"):
                    raise ValueError("No steps in plan")
                steps_count = len(plan.get("steps", []))
                logger.info(
                    f"🗂️ Phase {idx + 1} plan: [{plan.get('phase_name')}] — " f"{steps_count} steps"
                )
                for s in plan.get("steps", []):
                    desc = s.get("description")
                    hint = s.get("target_path", "?")
                    logger.info(f"  • {s.get('step_id')}: {desc} (hint: {hint})")
            except SessionLimitError:
                logger.error("Phase planner: API session usage limit hit — pausing workflow")
                raise
            except RateLimitError:
                logger.error("Phase planner: rate limit exhausted — propagating to executor")
                raise
            except Exception as e:
                logger.warning(
                    f"Phase planner LLM failed for phase {idx + 1} ({e}), using fallback"
                )
                plan = {
                    "phase_id": f"phase-{idx + 1}",
                    "phase_name": phase.get("name", "Unknown"),
                    "steps": [
                        {
                            "step_id": "step-1",
                            "description": (
                                f"Implement: "
                                f"{phase.get('description', phase.get('name', 'phase'))}"
                            ),
                            "target_path": ".",
                            "tool_hint": "list_dir",
                        }
                    ],
                }

            phase_plans.append(plan)

            # Write phase plan into the worktree and mirror it into runs/<issue_key>/.
            if worktree_path and os.path.exists(worktree_path):
                _write_phase_md(worktree_path, idx + 1, plan)
            _write_runs_phase_output(issue_key, idx + 1, plan)

        state["phase_plans"] = phase_plans

    if current_index >= len(phase_plans):
        logger.warning(
            f"🧭 Current phase index {current_index} is beyond available plans "
            f"({len(phase_plans)}); handing off to git manager"
        )
        state["status"] = "git_manager"
        state["current_phase_plan"] = None
        return state

    # Get the plan for the current phase
    plan = phase_plans[current_index]
    state["current_phase_plan"] = plan
    state["current_step_index"] = 0  # Initialize step index for new phase
    state["status"] = "coding"

    # Post phase plan to Jira
    _post_phase_comment(issue_key, current_index + 1, plan)

    logger.info("✅ [STEP 3/6] Phase planner node completed")
    return state


def _write_phase_md(worktree_path: str, phase_num: int, plan: dict) -> None:
    """Write .ai-sdlc/phase-N.md into the worktree."""
    try:
        ai_dir = os.path.join(worktree_path, ".ai-sdlc")
        os.makedirs(ai_dir, exist_ok=True)

        lines = [
            f"# Phase {phase_num}: {plan.get('phase_name', '')}",
            "",
        ]
        if plan.get("goal"):
            lines += [
                f"**Goal:** {plan.get('goal')}",
                "",
            ]

        if plan.get("acceptance_criteria"):
            lines += [
                "## Acceptance Criteria",
                "",
            ]
            for ac in plan["acceptance_criteria"]:
                lines.append(f"- {ac}")
            lines.append("")

        lines += [
            "## Steps",
            "",
        ]
        for step in plan.get("steps", []):
            lines += [
                f"### {step.get('step_id', '')}: {step.get('description', '')}",
                "",
            ]
            if step.get("details"):
                lines += [
                    f"**Details:** {step.get('details')}",
                    "",
                ]
            lines += [
                f"- **Target:** `{step.get('target_path', '?')}`",
                f"- **Tool hint:** `{step.get('tool_hint', '?')}`",
                "",
            ]

        with open(os.path.join(ai_dir, f"phase-{phase_num}.md"), "w") as f:
            f.write("\n".join(lines))
    except Exception as e:
        logger.warning(f"Could not write phase-{phase_num}.md: {e}")


def _write_runs_phase_output(issue_key: str, phase_num: int, plan: dict) -> None:
    """Write phase-N.md directly to runs/<issue_key>/ for easy viewing outside the project."""
    try:
        from src.settings import settings

        runs_dir = os.path.join(settings.worktree_base_path, "runs", issue_key)
        os.makedirs(runs_dir, exist_ok=True)

        filename = f"phase-{phase_num}.md"
        dst = os.path.join(runs_dir, filename)
        lines = [
            f"# Phase {phase_num}: {plan.get('phase_name', '')}",
            "",
        ]
        if plan.get("goal"):
            lines += [
                f"**Goal:** {plan.get('goal')}",
                "",
            ]

        if plan.get("acceptance_criteria"):
            lines += [
                "## Acceptance Criteria",
                "",
            ]
            for ac in plan["acceptance_criteria"]:
                lines.append(f"- {ac}")
            lines.append("")

        lines += [
            "## Steps",
            "",
        ]
        for step in plan.get("steps", []):
            lines += [
                f"### {step.get('step_id', '')}: {step.get('description', '')}",
                "",
            ]
            if step.get("details"):
                lines += [
                    f"**Details:** {step.get('details')}",
                    "",
                ]
            lines += [
                f"- **Target:** `{step.get('target_path', '?')}`",
                f"- **Tool hint:** `{step.get('tool_hint', '?')}`",
                "",
            ]

        with open(dst, "w") as f:
            f.write("\n".join(lines))

        logger.info(f"\n{'═' * 60}")
        logger.info(f"  📄  {filename} written to:")
        logger.info(f"  👉  {dst}")
        logger.info(f"{'═' * 60}\n")
    except Exception as e:
        logger.warning(f"Could not write runs phase output: {e}")


def _post_phase_comment(issue_key: str, phase_num: int, plan: dict) -> None:
    try:
        lines = [f"Phase {phase_num} plan — {plan.get('phase_name', '')}:", ""]
        for step in plan.get("steps", []):
            lines.append(f"  • {step.get('step_id', '')}: {step.get('description', '')}")
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
        logger.warning(f"Could not post phase plan to Jira: {e}")
