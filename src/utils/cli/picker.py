"""Interactive issue picker utilities."""

import os
import signal
import sys
import termios
import tty

from src.utils.cli.formatter import print_banner, priority_color, status_color
from src.utils.constants import BG_CYAN, BOLD, CYAN, DIM, HIDE_CURSOR, MAGENTA, RESET, SHOW_CURSOR


def _getch() -> str:
    """Read a single character from stdin without echo."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            ch3 = sys.stdin.read(1)
            return ch + ch2 + ch3
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_picker(issues: list[dict], selected: int, scroll_offset: int, max_visible: int) -> None:
    """Render the issue picker UI."""
    total = len(issues)
    visible_end = min(scroll_offset + max_visible, total)
    visible = issues[scroll_offset:visible_end]

    print(f"\n{BOLD}{CYAN}  📋  Select a Jira issue to execute  {DIM}({total} issues found){RESET}")
    print(f"  {DIM}↑/↓ to navigate  •  Enter to run  •  q to quit{RESET}\n")

    for i, issue in enumerate(visible, start=scroll_offset):
        is_selected = i == selected
        pri_color = priority_color(issue.get("priority", ""))
        sta_color = status_color(issue.get("status", ""))
        key = issue["key"]
        summary = issue["summary"][:60] + ("…" if len(issue["summary"]) > 60 else "")
        priority = issue.get("priority", "?")
        status = issue.get("status", "?")
        repo = issue.get("repo_url") or issue.get("repo_name") or "–"
        reviewers = ", ".join(issue.get("requested_reviewers", [])) or "–"

        if is_selected:
            print(f"  {BG_CYAN}\033[30m ▶  {key:<10} {summary:<62}{RESET}")
            print(
                f"       {DIM}Priority:{RESET} {pri_color}{priority:<10}{RESET}  "
                f"{DIM}Status:{RESET} {sta_color}{status:<20}{RESET}"
            )
            print(f"       {DIM}Repo:{RESET} {CYAN}{repo}{RESET}")
            if reviewers != "–":
                print(f"       {DIM}Reviewers:{RESET} {MAGENTA}{reviewers}{RESET}")
            print()
        else:
            print(f"  {DIM}   {RESET}{BOLD}{key:<10}{RESET} {summary:<62}")
            print(
                f"       {DIM}Priority:{RESET} {pri_color}{priority:<10}{RESET}  "
                f"{DIM}Status:{RESET} {sta_color}{status:<20}{RESET}\n"
            )

    # Scroll indicator
    if total > max_visible:
        shown_pct = int((visible_end / total) * 100)
        scroll_info = f"  {DIM}── showing {scroll_offset + 1}–{visible_end} of {total}"
        print(f"{scroll_info} ({shown_pct}%) ──{RESET}")


def interactive_picker(issues: list[dict]) -> dict | None:
    """
    Display an interactive arrow-key issue picker.

    Returns the selected issue dict, or None if user quit.
    """
    if not issues:
        return None

    selected = 0
    scroll_offset = 0
    max_visible = 5  # issues visible at once

    # hide cursor
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()

    def cleanup(sig=None, frame=None):
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()
        if sig:
            sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)

    try:
        while True:
            # Clear screen below cursor and re-render
            os.system("clear")  # nosec B605 B607
            print_banner()
            _render_picker(issues, selected, scroll_offset, max_visible)

            ch = _getch()

            if ch in ("\x1b[A", "k"):  # up arrow or k
                if selected > 0:
                    selected -= 1
                    if selected < scroll_offset:
                        scroll_offset = selected

            elif ch in ("\x1b[B", "j"):  # down arrow or j
                if selected < len(issues) - 1:
                    selected += 1
                    if selected >= scroll_offset + max_visible:
                        scroll_offset = selected - max_visible + 1

            elif ch in ("\r", "\n"):  # Enter
                sys.stdout.write(SHOW_CURSOR)
                sys.stdout.flush()
                return issues[selected]

            elif ch.lower() == "q" or ch == "\x03":  # q or Ctrl+C
                sys.stdout.write(SHOW_CURSOR)
                sys.stdout.flush()
                return None

    except Exception:
        cleanup()
        raise
