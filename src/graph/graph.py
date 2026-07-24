"""
LangGraph execution workflow.

See HLD §7.1 for specification.
"""

from langgraph.graph import END, StateGraph

from src.core.checkpointer.postgres_checkpointer import create_postgres_checkpointer
from src.graph.agents.context_builder_node import context_builder_node
from src.graph.agents.error_handler_node import error_handler_node
from src.graph.agents.git_manager_node import git_manager_node
from src.graph.agents.phase_planner_node import phase_planner_node
from src.graph.agents.roadmap_node import roadmap_node
from src.graph.agents.sanity_check_node import sanity_check_node
from src.graph.agents.tool_agent_node import tool_agent_node
from src.graph.state.execution_state import ExecutionState

# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def _route_after_sanity_check(state: ExecutionState) -> str:
    """Route the execution flow after a sanity check is run.

    Determines whether to retry the current phase, transition to the error
    handler, proceed to the next phase, or complete the execution.
    """
    from src.core.logging.logger import get_logger

    logger = get_logger("workflow_routing")

    result = state.get("sanity_check_result") or {}
    if not result.get("passed", False):
        retry = state.get("retry_count", 0)
        if retry >= 2:
            # Hard fail after 2 retries
            logger.warning(f"Sanity check failed after {retry} retries, routing to error handler")
            return "error"
        logger.warning(f"Sanity check failed, retrying (attempt {retry + 1}/2)")
        return "retry"

    # Sanity passed — check for more phases
    roadmap = state.get("roadmap") or {}
    phases = roadmap.get("phases", [])
    current_index = state.get("current_phase_index", 0)

    if current_index < len(phases):
        logger.info(
            f"Sanity check passed, proceeding to next phase ({current_index + 1}/{len(phases)})"
        )
        return "next_phase"
    logger.info("All phases completed, routing to git manager")
    return "done"


def _route_after_phase_planner(state: ExecutionState) -> str:
    """Route the execution flow after the phase planner node.

    Determines whether to transition directly to the git manager or
    continue with building the context and executing the phase.
    """
    from src.core.logging.logger import get_logger

    logger = get_logger("workflow_routing")

    if state.get("status") == "git_manager":
        logger.info("All phases planned, routing to git manager")
        return "done"
    logger.info("Phase plan ready, routing to tool agent for execution")
    return "continue"


def _route_after_tool_agent(state: ExecutionState) -> str:
    """Route the execution flow after the tool agent node.

    Determines whether to continue with more steps in the current phase
    or proceed to sanity check when the phase is complete.
    """
    from src.core.logging.logger import get_logger

    logger = get_logger("workflow_routing")

    status = state.get("status", "")
    if status == "tool_agent":
        # More steps in current phase
        logger.info("More steps in current phase, continuing execution")
        return "continue"
    elif status == "sanity_check":
        # Phase complete
        logger.info("Phase complete, routing to sanity check")
        return "sanity_check"
    else:
        # Error or other status
        logger.info(f"Tool agent status: {status}, routing to sanity check")
        return "sanity_check"


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------


def create_execution_graph():
    """Create and compile the StateGraph execution workflow."""
    workflow = StateGraph(ExecutionState)  # type: ignore[type-var]

    # Nodes
    workflow.add_node("roadmap_node", roadmap_node)
    workflow.add_node("context_builder_node", context_builder_node)
    workflow.add_node("phase_planner_node", phase_planner_node)
    workflow.add_node("tool_agent_node", tool_agent_node)
    workflow.add_node("sanity_check_node", sanity_check_node)
    workflow.add_node("git_manager_node", git_manager_node)
    workflow.add_node("error_handler_node", error_handler_node)

    # Entry point
    workflow.set_entry_point("roadmap_node")

    # roadmap → context_builder (build repo context first)
    workflow.add_edge("roadmap_node", "context_builder_node")

    # context_builder → phase_planner (now we have code context for planning)
    workflow.add_edge("context_builder_node", "phase_planner_node")

    # phase_planner → conditional: all phases done → git_manager, else → tool_agent
    workflow.add_conditional_edges(
        "phase_planner_node",
        _route_after_phase_planner,
        {
            "done": "git_manager_node",  # all phases exhausted
            "continue": "tool_agent_node",  # more phases to run
        },
    )

    # tool_agent → conditional: more steps → continue, phase done → sanity_check
    workflow.add_conditional_edges(
        "tool_agent_node",
        _route_after_tool_agent,
        {
            "continue": "tool_agent_node",  # more steps in current phase
            "sanity_check": "sanity_check_node",  # phase complete
        },
    )

    # Conditional routing after sanity check
    workflow.add_conditional_edges(
        "sanity_check_node",
        _route_after_sanity_check,
        {
            "retry": "tool_agent_node",  # sanity failed, retry
            "error": "error_handler_node",  # sanity failed, retries exhausted
            "next_phase": "phase_planner_node",  # more phases to run
            "done": "git_manager_node",  # all phases done
        },
    )

    # Terminal edges
    workflow.add_edge("git_manager_node", END)
    workflow.add_edge("error_handler_node", END)

    checkpointer = create_postgres_checkpointer()
    return workflow.compile(checkpointer=checkpointer)


execution_graph = create_execution_graph()
