"""
Tool-Calling Agent Node — executes phase steps using repository tools.

See HLD §4.6 for specification.
Uses langchain.agents.create_agent pattern with middleware and structured output.
Executes ONE step at a time (like REFERENCE executor), then returns to routing.
"""

import json
import os
import re

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

# LangChain built-in tools
from langchain_community.tools import (
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool as make_tool

from src.core.llm.provider import (
    RateLimitError,
    SessionLimitError,
)
from src.core.logging.logger import get_logger
from src.core.prompt_loader import load_prompt
from src.graph.schemas import ToolAgentResponse
from src.graph.state.execution_state import ExecutionState
from src.services.tools.file_tools import edit_file as _edit_file
from src.services.tools.search_tools import ast_query as _ast_query
from src.services.tools.search_tools import git_diff as _git_diff
from src.services.tools.search_tools import grep_search as _grep_search
from src.services.tools.shell_tools import run_shell as _run_shell
from src.settings import settings
from src.utils.constants import MAX_WRITE_FILE_CHARS, MAX_WRITE_FILE_LINES
from src.utils.middleware import get_middleware

logger = get_logger("tool_agent_node")


def _map_provider_for_init_chat_model(provider: str) -> str:
    """Map internal provider names to init_chat_model expected names."""
    provider_mapping = {
        "claude": "anthropic",
        "gemini": "google_genai",
        "groq": "groq",
        "ollama": "ollama",
    }
    return provider_mapping.get(provider, provider)


def _parse_json_from_content(content: str) -> dict | None:
    """Extract JSON from message content, handling markdown code fences and common
    formatting issues."""
    try:
        # Try to find JSON in markdown code fences
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # Try to find JSON without fences
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            content = json_match.group(0)

        # Fix common JSON formatting issues
        # Replace single quotes with double quotes (but careful with strings containing quotes)
        content = re.sub(r"'([^']*)'", r'"\1"', content)
        # Remove trailing commas before closing brackets/braces
        content = re.sub(r",\s*([}\]])", r"\1", content)
        # Remove comments (// style)
        content = re.sub(r"//.*", "", content)

        return dict(json.loads(content))
    except (json.JSONDecodeError, AttributeError) as e:
        logger.debug(f"Failed to parse JSON from content: {e}")
        logger.debug(f"Content preview: {content[:200]}...")
        return None


def _make_bound_tools(worktree_path: str):
    """
    Return tool instances with worktree_path pre-filled.

    The agent never has to pass it explicitly in every call.
    """
    wt = worktree_path

    # Use LangChain built-in tools
    read_tool = ReadFileTool()
    write_tool = WriteFileTool()
    list_tool = ListDirectoryTool()

    @make_tool
    def read_file(path: str) -> str:
        """Read a file from the repository. path is relative to repo root."""
        try:
            full_path = os.path.join(wt, path)
            return read_tool.run({"file_path": full_path})  # type: ignore[no-any-return]
        except FileNotFoundError:
            return f"ERROR: File '{path}' does not exist. Use list_dir('.') to see available files."

    @make_tool
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a file. path is relative to repo root.

        Keep new file contents small and focused. Large files should be created
        as a minimal scaffold first, then expanded with edit_file().
        """
        try:
            line_count = content.count("\n") + (1 if content else 0)
            if len(content) > MAX_WRITE_FILE_CHARS or line_count > MAX_WRITE_FILE_LINES:
                err_msg = (
                    f"ERROR: write_file payload is too large "
                    f"({len(content)} chars, {line_count} lines). "
                    "Create a smaller scaffold first, then expand it with edit_file()."
                )
                return err_msg
            full_path = os.path.join(wt, path)
            return write_tool.run({"file_path": full_path, "text": content})  # type: ignore[no-any-return]
        except Exception as e:
            return f"ERROR writing file: {e}"

    @make_tool
    def edit_file(path: str, old_string: str, new_string: str) -> str:
        """Replace old_string with new_string in a file. old_string must be unique."""
        try:
            return _edit_file.invoke(  # type: ignore[no-any-return]
                {
                    "path": path,
                    "old_string": old_string,
                    "new_string": new_string,
                    "worktree_path": wt,
                }
            )
        except FileNotFoundError:
            return f"ERROR: File '{path}' does not exist. Use list_dir('.') to see available files."
        except ValueError as e:
            return f"ERROR: {e}"

    @make_tool
    def list_dir(path: str = ".") -> str:
        """List directory contents. path is relative to repo root, default is root."""
        try:
            full_path = os.path.join(wt, path)
            return list_tool.run({"dir_path": full_path})  # type: ignore[no-any-return]
        except FileNotFoundError:
            return (
                f"ERROR: Directory '{path}' does not exist. "
                "Use list_dir('.') to see the repo root."
            )

    @make_tool
    def grep_search(pattern: str, path: str = ".") -> str:
        """Search for a regex pattern across the repo. Returns matching lines with line numbers."""
        try:
            return _grep_search.invoke(  # type: ignore[no-any-return]
                {"pattern": pattern, "path": path, "worktree_path": wt}
            )
        except FileNotFoundError:
            return f"ERROR: Path '{path}' does not exist."

    @make_tool
    def ast_query(path: str, query: str, language: str = None) -> str:
        """Perform structural code search using tree-sitter.

        Query for functions, classes, imports, etc.
        """
        try:
            params = {"path": path, "query": query, "worktree_path": wt}
            if language:
                params["language"] = language
            return _ast_query.invoke(params)  # type: ignore[no-any-return]
        except FileNotFoundError:
            return f"ERROR: File '{path}' does not exist."
        except Exception as e:
            return f"ERROR: {e}"

    @make_tool
    def run_shell(command: str) -> str:
        """Run a shell command in the repo root (e.g. for lint/format/build)."""
        try:
            return _run_shell.invoke(  # type: ignore[no-any-return]
                {"command": command, "worktree_path": wt}
            )
        except Exception as e:
            return f"ERROR running command: {e}"

    @make_tool
    def git_diff() -> str:
        """Show accumulated git diff so far — use to avoid redundant edits."""
        try:
            return _git_diff.invoke({"worktree_path": wt})  # type: ignore[no-any-return]
        except Exception as e:
            return f"ERROR getting diff: {e}"

    return [read_file, write_file, edit_file, list_dir, grep_search, ast_query, run_shell, git_diff]


def tool_agent_node(state: ExecutionState) -> ExecutionState:
    """Execute ONE step of the current phase using LangChain create_agent.

    Executes one step at a time (like REFERENCE executor), then returns to routing.
    """
    logger.info("📍 [STEP 4/6] Starting tool agent execution")
    worktree_path = state.get("worktree_path", "")

    # Get current phase and step
    roadmap = state.get("roadmap") or {}
    phases = roadmap.get("phases", [])
    current_phase_index = state.get("current_phase_index", 0)
    phase_plan = state.get("current_phase_plan") or {}
    steps = phase_plan.get("steps", [])
    current_step_index = state.get("current_step_index", 0)

    if current_phase_index >= len(phases):
        logger.warning("No phases to execute")
        state["status"] = "sanity_check"
        logger.info("✅ [STEP 4/6] Tool agent node completed (no phases)")
        return state

    # Check if this is a retry due to sanity check failure
    sanity_check_result = state.get("sanity_check_result") or {}
    retry_count = state.get("retry_count", 0)
    is_fixing_sanity = state.get("fixing_sanity", False)
    sanity_failures = []
    
    if retry_count > 0 and not sanity_check_result.get("passed", True) and not is_fixing_sanity:
        diagnostics = sanity_check_result.get("diagnostics", [])
        for diag in diagnostics:
            if diag.get("level") == "error":
                check_name = diag.get("check", "unknown")
                message = diag.get("message", "")
                sanity_failures.append(f"- {check_name}: {message}")
        
        if sanity_failures:
            logger.warning(f"Retrying due to sanity check failures: {sanity_failures}")
            # Mark that we're now in fix mode
            state["fixing_sanity"] = True
            # Execute a single fix step instead of re-running all steps
            step_title = "Fix Sanity Check Failures"
            step_action = f"Fix the following sanity check failures:\n" + "\n".join(sanity_failures)
            step_number = 1
            logger.info(f"Entering fix mode to address sanity check failures")
            
            # Execute the fix step and go directly to sanity check
            phase = phases[current_phase_index]
            phase_name = phase.get("name", f"Phase {current_phase_index + 1}")
            
            logger.info(f"🚀 Executing fix step: {step_title}")
            logger.debug(f"Step action content: {step_action[:500]}...")
            logger.debug(f"LLM model: {settings.llm_model}, provider: {settings.llm_provider}")

            # Build tools
            tools = _make_bound_tools(worktree_path)

            # Build system prompt
            system_prompt_ref = load_prompt("tool_agent_system_prompt.md")
            
            # Add sanity check failure context
            sanity_context = "\n\n## Previous Sanity Check Failures (CRITICAL - MUST FIX)\n"
            sanity_context += "The previous execution of this phase failed sanity checks. You MUST fix these issues:\n"
            sanity_context += "\n".join(sanity_failures)
            sanity_context += "\n\nFix these specific issues. Use edit_file or write_file as needed to resolve the linting errors."
            
            system_prompt = system_prompt_ref.compile(
                issue_key=state.get("issue_key", ""),
                issue_summary=state.get("issue_summary", ""),
                phase_name=phase_name,
                phase_goal=phase_plan.get("goal", ""),
                steps_description=step_action + sanity_context,
            )

            # Use create_agent with middleware
            if settings.llm_provider == "ollama":
                from src.core.llm.provider import get_chat_model
                llm = get_chat_model(
                    provider="ollama",
                    model=settings.llm_model,
                    temperature=0.1,
                    max_tokens=8192,
                )
                logger.debug("Using custom get_chat_model for Ollama")
            else:
                mapped_provider = _map_provider_for_init_chat_model(settings.llm_provider)
                logger.debug(f"Mapped provider: {settings.llm_provider} -> {mapped_provider}")
                llm = init_chat_model(
                    settings.llm_model,
                    model_provider=mapped_provider,
                    temperature=0.1,
                    max_retries=1,
                    max_tokens=8192,
                )

            agent_kwargs = {
                "model": llm,
                "tools": tools,
                "middleware": get_middleware(settings.llm_provider),
                "system_prompt": system_prompt,
                "name": "ToolAgent-FixStep",
            }

            if settings.llm_provider == "claude":
                agent_kwargs["response_format"] = ToolAgentResponse
                logger.debug("Using response_format for Claude/Anthropic")
            else:
                logger.debug(f"Skipping response_format for {settings.llm_provider} (not supported)")

            agent = create_agent(**agent_kwargs)

            try:
                result = agent.invoke(
                    {"messages": [HumanMessage(content=step_action)]},
                )

                messages = result.get("messages", [])
                output = messages[-1].content if messages and len(messages) > 0 else "No output"
                logger.info(f"✅ Fix step completed")
                logger.info(f"   Output: {output[:200]}...")

            except Exception as e:
                logger.error(f"Fix step failed: {e}")
                # Still proceed to sanity check to see if anything improved

            # After fix step, go directly to sanity check
            state["status"] = "sanity_check"
            logger.info("✅ [STEP 4/6] Tool agent node completed (fix step done)")
            return state

    if current_step_index >= len(steps):
        logger.info("All steps in current phase completed")
        state["status"] = "sanity_check"
        logger.info("✅ [STEP 4/6] Tool agent node completed (phase done)")
        return state

    phase = phases[current_phase_index]
    phase_name = phase.get("name", f"Phase {current_phase_index + 1}")
    step = steps[current_step_index]
    step_title = step.get("title", f"Step {current_step_index + 1}")
    step_action = step.get("details", step.get("action", ""))
    step_number = current_step_index + 1

    logger.info(f"🚀 Executing step {step_number}/{len(steps)}: {step_title}")
    logger.debug(f"Step action content: {step_action[:500]}...")
    logger.debug(f"Step keys: {list(step.keys())}")
    logger.debug(f"LLM model: {settings.llm_model}, provider: {settings.llm_provider}")

    # Build tools
    tools = _make_bound_tools(worktree_path)

    # Build system prompt
    system_prompt_ref = load_prompt("tool_agent_system_prompt.md")
    
    # Add sanity check failure context if retrying
    sanity_context = ""
    if sanity_failures:
        sanity_context = "\n\n## Previous Sanity Check Failures (CRITICAL - MUST FIX)\n"
        sanity_context += "The previous execution of this phase failed sanity checks. You MUST fix these issues:\n"
        sanity_context += "\n".join(sanity_failures)
        sanity_context += "\n\nFix these specific issues before completing the step."
    
    system_prompt = system_prompt_ref.compile(
        issue_key=state.get("issue_key", ""),
        issue_summary=state.get("issue_summary", ""),
        phase_name=phase_name,
        phase_goal=phase_plan.get("goal", ""),
        steps_description=step_action + sanity_context,
    )

    # Use create_agent with middleware (like REFERENCE)
    # For Ollama, use custom get_chat_model to support Ollama Cloud
    if settings.llm_provider == "ollama":
        from src.core.llm.provider import get_chat_model
        llm = get_chat_model(
            provider="ollama",
            model=settings.llm_model,
            temperature=0.1,
            max_tokens=8192,
        )
        logger.debug("Using custom get_chat_model for Ollama")
    else:
        # Use init_chat_model for other providers
        mapped_provider = _map_provider_for_init_chat_model(settings.llm_provider)
        logger.debug(f"Mapped provider: {settings.llm_provider} -> {mapped_provider}")
        llm = init_chat_model(
            settings.llm_model,
            model_provider=mapped_provider,
            temperature=0.1,
            max_retries=1,
            max_tokens=8192,
        )

    # Add response_format for providers that support structured output (Claude/Anthropic)
    agent_kwargs = {
        "model": llm,
        "tools": tools,
        "middleware": get_middleware(settings.llm_provider),
        "system_prompt": system_prompt,
        "name": f"ToolAgent-Step-{step_number}",
    }

    # Only use response_format for Claude/Anthropic (supports structured output)
    # Ollama and other providers don't support it, so we rely on manual JSON parsing
    if settings.llm_provider == "claude":
        agent_kwargs["response_format"] = ToolAgentResponse
        logger.debug("Using response_format for Claude/Anthropic")
    else:
        logger.debug(f"Skipping response_format for {settings.llm_provider} (not supported)")

    agent = create_agent(**agent_kwargs)

    try:
        logger.debug(f"Invoking agent with step_action: {step_action[:200]}...")
        result = agent.invoke(
            {"messages": [HumanMessage(content=step_action)]},
        )

        logger.debug(f"Agent result keys: {list(result.keys())}")
        logger.debug(f"Agent result type: {type(result)}")

        # Extract structured response (with fallback like REFERENCE)
        structured_response = result.get("structured_response")
        logger.debug(f"Structured response found: {structured_response is not None}")

        if structured_response:
            logger.info(f"✅ Step {step_number} completed: {structured_response.status}")
            logger.info(f"   Files created: {len(structured_response.files_created)}")
            logger.info(f"   Files modified: {len(structured_response.files_modified)}")
        else:
            # Fallback: extract from messages if structured_response is not available
            logger.debug("Extracting fallback from messages...")
            messages = result.get("messages", [])
            logger.debug(f"Number of messages: {len(messages)}")

            output = messages[-1].content if messages and len(messages) > 0 else "No output"
            logger.info(f"⚠️ Step {step_number} completed without structured response")
            logger.info(f"   Output: {output[:200]}...")
            logger.debug(f"Full output: {output}")

            # Try to parse JSON from the output
            parsed_json = _parse_json_from_content(output)
            if parsed_json:
                logger.info("✅ Successfully parsed JSON from output")
                structured_response = ToolAgentResponse(**parsed_json)
            else:
                logger.warning("⚠️ Could not parse JSON, using fallback")
                # Create a fallback structured response
                structured_response = ToolAgentResponse(
                    step=step_number,
                    status="success",
                    files_created=[],
                    files_modified=[],
                    details=output,
                    verification_results=[],
                )

        # Increment step index
        state["current_step_index"] = current_step_index + 1

        # Check if phase is complete
        if state["current_step_index"] >= len(steps):
            logger.info(f"🎉 Phase {phase_name} completed")
            state["status"] = "sanity_check"
        else:
            # Continue to next step
            state["status"] = "tool_agent"

        logger.info("✅ [STEP 4/6] Tool agent node completed")
        return state

    except (RateLimitError, SessionLimitError) as e:
        logger.error(f"LLM rate limit hit: {e}")
        state["error"] = f"Rate limit error: {e}"
        state["status"] = "failed"
        logger.info("✅ [STEP 4/6] Tool agent node completed (error)")
        return state
    except Exception as e:
        logger.error(f"Tool agent error: {e}")
        state["error"] = f"Tool agent error: {e}"
        state["status"] = "failed"
        logger.info("✅ [STEP 4/6] Tool agent node completed (error)")
        return state
