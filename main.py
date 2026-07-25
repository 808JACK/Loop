#!/usr/bin/env python3
"""
AI SDLC Runner — interactive Jira issue picker + executor.

Usage:
  uv run python main.py                              # interactive picker (arrow keys to select)
  uv run python main.py --manual KAN-4                # run a specific issue key
  uv run python main.py --manual KAN-4 --repo https://github.com/org/repo.git
  uv run python main.py --resume <execution_id>      # resume from latest checkpoint
  uv run python main.py --resume <execution_id>      # resume from latest checkpoint
  uv run python main.py --resume <execution_id> --checkpoint <checkpoint_id>
  # resume from specific checkpoint
  uv run python main.py --list-checkpoints <execution_id>  # list available checkpoints
  uv run python main.py --auto --yes                 # non-interactive: run all ai-ready issues
  uv run python main.py --serve                      # run webhook server for PR merge events
"""

import argparse
import asyncio
import os
import sys

# ── Load environment variables directly from .env ─────────────────────────────
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)

# ── bootstrap settings and app imports ───────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from src.settings import settings  # noqa: E402

# ── Initialize LangSmith tracing from environment variables ──────────────────
if os.getenv("LANGSMITH_TRACING", "false").lower() == "true":
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    langsmith_api_key = os.getenv("LANGSMITH_API_KEY")
    if langsmith_api_key:
        os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
    langsmith_project = os.getenv("LANGSMITH_PROJECT")
    if langsmith_project:
        os.environ["LANGCHAIN_PROJECT"] = langsmith_project

# ── One-time setup: ensure worktree directories exist ────────────────────────
for _subdir in ("repos", "runs"):
    _dir = os.path.join(settings.worktree_base_path, _subdir)
    os.makedirs(_dir, exist_ok=True)

from src.utils.cli import (  # noqa: E402
    confirm,
    interactive_picker,
    print_banner,
    run_issue,
)
from src.utils.cli.jira import fetch_ai_ready_issues, fetch_manual_issue  # noqa: E402
from src.utils.constants import BOLD, DIM, GREEN, RED, RESET, YELLOW  # noqa: E402

# ── main ──────────────────────────────────────────────────────────────────────


async def main() -> None:
    """Run the AI SDLC runner CLI."""
    parser = argparse.ArgumentParser(
        description="AI SDLC Runner — Jira issue picker and executor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--auto",
        action="store_true",
        help="Fetch all ai-ready issues, process one by one (non-interactive)",
    )
    mode.add_argument("--manual", metavar="ISSUE_KEY", help="Run a specific issue key (e.g. KAN-4)")
    mode.add_argument(
        "--resume", metavar="EXECUTION_ID", help="Resume a failed or paused execution"
    )
    mode.add_argument(
        "--list-checkpoints",
        metavar="EXECUTION_ID",
        help="List available checkpoints for an execution",
    )
    mode.add_argument(
        "--serve",
        action="store_true",
        help="Run webhook server for PR merge events",
    )
    parser.add_argument(
        "--repo", metavar="URL", help="GitHub repo URL (overrides Jira custom field)"
    )
    parser.add_argument("--yes", action="store_true", help="Skip all confirmation prompts")
    parser.add_argument(
        "--port",
        type=int,
        default=8090,
        help="Port to run the webhook server on (default: 8090)",
    )
    args = parser.parse_args()

    # ── serve mode: run webhook server ───────────────────────────────────────────
    if args.serve:
        import uvicorn

        from src.api.server import app

        print_banner()
        print(f"  {GREEN}🚀{RESET}  Starting webhook server on port {args.port}...")
        print(f"  {DIM}Webhook endpoints:{RESET}")
        print(f"    - GitHub: http://localhost:{args.port}/api/v1/webhook/github")
        print(f"    - GitLab: http://localhost:{args.port}/api/v1/webhook/gitlab")
        print(f"    - Bitbucket: http://localhost:{args.port}/api/v1/webhook/bitbucket")
        print(f"  {DIM}Health check: http://localhost:{args.port}/health{RESET}\n")

        config = uvicorn.Config(app, host="0.0.0.0", port=args.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
        return

    # ── banner ────────────────────────────────────────────────────────────────
    print_banner()

    # ── manual mode ───────────────────────────────────────────────────────────
    if args.manual:
        try:
            issue = await fetch_manual_issue(args.manual, args.repo)
        except Exception as e:
            print(f"  {RED}❌{RESET}  Could not fetch issue: {e}")
            sys.exit(1)
        await run_issue(issue, auto=args.yes)

    # ── resume mode ───────────────────────────────────────────────────────────
    elif args.resume:
        from src.utils.cli.workflow import resume_execution

        await resume_execution(args.resume, checkpoint_id=args.checkpoint)

    # ── list checkpoints mode ─────────────────────────────────────────────────
    elif args.list_checkpoints:
        from src.utils.cli.workflow import list_checkpoints

        await list_checkpoints(args.list_checkpoints)

    # ── auto mode (non-interactive batch) ─────────────────────────────────────
    elif args.auto:
        try:
            issues = await fetch_ai_ready_issues(args.repo)
        except Exception as e:
            print(f"  {RED}❌{RESET}  Could not fetch issues: {e}")
            sys.exit(1)

        if not issues:
            print(
                f"\n  {YELLOW}✨{RESET}  No '{settings.jira_ready_label}' issues "
                f"found in {settings.jira_project_key}"
            )
            sys.exit(0)

        print(f"\n  {GREEN}📋{RESET}  Found {BOLD}{len(issues)}{RESET} issue(s)\n")

        for i, issue in enumerate(issues, 1):
            print(f"\n{'═' * 60}")
            print(f"  Issue {i}/{len(issues)}")
            await run_issue(issue, auto=args.yes)

            if i < len(issues) and not args.yes:
                if not confirm(f"Continue to next issue ({issues[i]['key']})?"):
                    print(f"\n  {YELLOW}👋{RESET}  Stopped.")
                    break

        print(f"\n{'═' * 60}")
        print(f"  {GREEN}✓{RESET}  All done.")

    # ── default: interactive picker ───────────────────────────────────────────
    else:
        try:
            issues = await fetch_ai_ready_issues(args.repo)
        except Exception as e:
            print(f"  {RED}❌{RESET}  Could not fetch Jira issues: {e}")
            sys.exit(1)

        if not issues:
            print(
                f"\n  {YELLOW}✨{RESET}  No '{settings.jira_ready_label}' issues "
                f"found in {settings.jira_project_key}"
            )
            print(
                f"  {DIM}Label issues with '{settings.jira_ready_label}' in Jira "
                f"to have them appear here.{RESET}"
            )
            sys.exit(0)

        selected_issue = interactive_picker(issues)

        if selected_issue is None:
            print(f"\n  {YELLOW}👋{RESET}  No issue selected. Exiting.")
            sys.exit(0)

        # Show selected issue and run
        os.system("clear")  # nosec B605 B607
        print_banner()
        selected_msg = (
            f"\n  {GREEN}✓{RESET}  Selected: {BOLD}{selected_issue['key']}{RESET} "
            f"— {selected_issue['summary']}"
        )
        print(selected_msg)
        await run_issue(selected_issue, auto=True)


if __name__ == "__main__":
    asyncio.run(main())
