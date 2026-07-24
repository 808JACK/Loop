"""
Context Builder Node - assembles context for the tool-calling agent.

See HLD §4.5 for full specification.
"""

from src.core.context.summarizer import build_context_primer
from src.core.logging.logger import get_logger
from src.graph.state.execution_state import ExecutionState

logger = get_logger("context_builder_node")


def context_builder_node(state: ExecutionState) -> ExecutionState:
    """
    Build context primer for the tool-calling agent.

    This node:
    - Reads Project Memory (architecture, conventions)
    - Reads the current PhasePlan
    - Assembles a context primer
    - Makes search tools available to the agent

    Args:
        state: Current execution state

    Returns:
        Updated execution state with context primer
    """
    logger.info("📍 [STEP 2/6] Building context primer from project memory and phase plan")
    state["context_primer"] = build_context_primer(dict(state))
    logger.info("✅ [STEP 2/6] Context builder node completed")
    return state
