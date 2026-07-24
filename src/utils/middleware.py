"""Middleware configuration for LangChain agents.

Following the plan.txt pattern for multi-layered guardrails and optimization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
    hook_config,
)
from langchain.agents.middleware.model_call_limit import ModelCallLimitState

if TYPE_CHECKING:
    from langgraph.runtime import Runtime  # type-hint only

model_call_limit = 45


def get_middleware(llm_provider: str = "ollama") -> list[Any]:
    """Get the standard middleware for all agents.

    Multi-layered guardrails following the blueprint pattern:
    - Model call limits to prevent runaway execution
    - Tool call limits to prevent excessive resource usage (with continue behavior)
    - Todo tracking for observability
    - Graceful warning before hard limit
    - Anthropic prompt caching for Claude API (when applicable)

    Args:
        llm_provider: The LLM provider being used (claude, gemini, groq, ollama)

    Returns:
        List of middleware instances
    """
    middleware = [
        FinalizeNearLimitMiddleware(run_limit=model_call_limit, warn_at=42),
        ModelCallLimitMiddleware(
            run_limit=model_call_limit,
            exit_behavior="end",
        ),
        ToolCallLimitMiddleware(tool_name="read_file", run_limit=25, exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="write_file", run_limit=25, exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="edit_file", run_limit=25, exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="list_dir", run_limit=25, exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="grep_search", run_limit=15, exit_behavior="continue"),
        ToolCallLimitMiddleware(tool_name="ast_query", run_limit=15, exit_behavior="continue"),
        TodoListMiddleware(),
    ]

    # Add Anthropic prompt caching middleware when using Claude
    if llm_provider == "claude":
        try:
            from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

            middleware.insert(1, AnthropicPromptCachingMiddleware(ttl="5m"))
        except ImportError:
            # If langchain-anthropic is not installed, skip this middleware
            pass

    return middleware


class FinalizeNearLimitMiddleware(AgentMiddleware[ModelCallLimitState[Any], Any, Any]):
    """Warns the agent before ModelCallLimitMiddleware terminates it,
    giving it a chance to finalize and return a partial response.
    """

    state_schema = ModelCallLimitState  # grants typed access to run_model_call_count

    def __init__(self, run_limit: int, warn_at: int):
        super().__init__()
        self.run_limit = run_limit
        self.warn_at = warn_at

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: ModelCallLimitState, runtime: Runtime) -> dict[str, Any] | None:  # type: ignore[override]
        call_count = state.get("run_model_call_count", 0)

        if call_count >= self.run_limit:
            return {
                "messages": ["Model call limit reached."],
                "jump_to": "end",
            }

        if call_count == self.warn_at:
            return {
                "messages": [
                    "WARNING: You are approaching your model call limit. "
                    "Do not make any more tool calls. "
                    "Return your final structured output now "
                    "based on what you have done so far."
                ]
            }

        return None
