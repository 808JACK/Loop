"""
Logging configuration with structured logging.

Uses structlog for structured, context-aware logging.
See HLD §10 for observability requirements.
"""

import logging
import os
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict

from src.settings import settings

# Context variable for execution ID
execution_id_var: ContextVar[str] = ContextVar("execution_id", default="")


def add_execution_id(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """
    Add execution ID to log context.

    Args:
        logger: Logger instance
        method_name: Method name
        event_dict: Event dictionary

    Returns:
        EventDict: Updated event dictionary
    """
    execution_id = execution_id_var.get()
    if execution_id:
        event_dict["execution_id"] = execution_id
    return event_dict


def drop_color_message_key(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """
    Drop color message key for cleaner logs.

    Args:
        logger: Logger instance
        method_name: Method name
        event_dict: Event dictionary

    Returns:
        EventDict: Updated event dictionary
    """
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging():
    """Configure structured logging for the application."""
    # Configure standard logging
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)

    # Keep noisy third-party client output out of the main terminal stream.
    for noisy_logger in (
        "httpx",
        "httpcore",
        "langchain",
        "langchain_ollama",
        "langsmith",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Configure structlog processors
    processors = [
        # Add context
        structlog.contextvars.merge_contextvars,
        add_execution_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        # Format
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Output
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.is_development:
        # Pretty console output for development
        processors.extend(
            [
                structlog.dev.ConsoleRenderer(colors=True),
            ]
        )
    else:
        # JSON output for production
        processors.extend(
            [
                drop_color_message_key,
                structlog.processors.JSONRenderer(),
            ]
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _remove_file_handlers(root_logger: logging.Logger) -> None:
    """Detach and close any existing file handlers before starting a new execution log."""
    for handler in list(root_logger.handlers):
        if isinstance(handler, logging.FileHandler):
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # nosec B110
                pass


def setup_execution_file_logger(execution_id: str, worktree_path: str = None, mode: str = "w"):
    """
    Set up file-based logging for a specific execution.

    Creates a log file in the worktree directory (if available) or in a logs directory.

    Args:
        execution_id: Execution ID for the log file
        worktree_path: Optional worktree path for log file location
    """
    # Determine log directory
    if worktree_path and os.path.exists(worktree_path):
        log_dir = os.path.join(worktree_path, ".ai-sdlc")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "execution.log")
    else:
        # Fallback to global logs directory
        log_dir = os.path.join(os.getcwd(), "logs", "executions")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"{execution_id}.log")

    # Create file handler for this execution
    root_logger = logging.getLogger()
    _remove_file_handlers(root_logger)

    file_handler = logging.FileHandler(log_file, mode=mode)
    file_handler.setLevel(getattr(logging, settings.log_level.upper()))
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    # Add handler to root logger
    root_logger.addHandler(file_handler)

    return log_file


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def set_execution_id(execution_id: str):
    """
    Set the execution ID in the logging context.

    Args:
        execution_id: Execution ID to include in logs
    """
    execution_id_var.set(execution_id)


def clear_execution_id():
    """Clear the execution ID from logging context."""
    execution_id_var.set("")
