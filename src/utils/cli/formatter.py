"""UI formatting utilities for CLI output."""

from src.utils.constants import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
)


def print_banner() -> None:
    """Print the FLUX banner."""
    print(f"\n{CYAN}{BOLD}  FLUX — AI SDLC Automation{RESET}\n")


def print_box(title: str, lines: list[str], color: str = CYAN) -> None:
    """Print a formatted box with title and content lines."""
    width = max(len(title), max((len(ln) for ln in lines), default=0)) + 4
    bar = "─" * width
    print(f"\n{color}┌{bar}┐{RESET}")
    print(f"{color}│{RESET}  {BOLD}{title.ljust(width - 2)}{RESET}{color}│{RESET}")
    print(f"{color}├{bar}┤{RESET}")
    for line in lines:
        print(f"{color}│{RESET}  {line.ljust(width - 2)}{color}│{RESET}")
    print(f"{color}└{bar}┘{RESET}")


def priority_color(priority: str) -> str:
    """Get color for priority level."""
    p = priority.lower()
    if p == "highest" or p == "critical":
        return RED
    if p == "high":
        return YELLOW
    if p == "medium":
        return CYAN
    if p == "low":
        return GREEN
    return DIM


def status_color(status: str) -> str:
    """Get color for status."""
    s = status.lower()
    if "done" in s or "closed" in s:
        return GREEN
    if "progress" in s or "review" in s:
        return YELLOW
    if "todo" in s or "open" in s or "backlog" in s:
        return CYAN
    return DIM
