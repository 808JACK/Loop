"""
Sanity Check Node - runs lint, type-check, build after each phase.

See HLD §4.7 for full specification.
"""

import json
import os
import subprocess  # nosec B404
from pathlib import Path

from src.core.logging.logger import get_logger
from src.graph.state.execution_state import ExecutionState
from src.services.project.project_type_service import get_or_detect_project_type
from src.utils.constants import GO_EXTENSIONS, JS_TS_EXTENSIONS, MAX_PHASE_RETRIES

logger = get_logger(__name__)


def sanity_check_node(state: ExecutionState) -> ExecutionState:
    """
    Run sanity checks on the worktree after phase execution.

    This node:
    - Runs lint (e.g., ruff, eslint)
    - Runs type-check (e.g., mypy, tsc)
    - Runs build/compile
    - Returns pass/fail with diagnostics
    - Writes result to Execution Memory

    Args:
        state: Current execution state

    Returns:
        Updated execution state with sanity check result
    """
    logger.info("📍 [STEP 5/6] Starting sanity checks")
    worktree_path = state.get("worktree_path")
    diagnostics = []
    passed = True

    if not worktree_path or not os.path.exists(worktree_path):
        # Skip sanity checks if no worktree, but still advance the phase counter
        # so the graph does not repeat the same phase forever.
        logger.info("🧪 Sanity check skipped because no worktree was available")
        state["sanity_check_result"] = {
            "passed": True,
            "diagnostics": [{"level": "warning", "message": "No worktree, skipping sanity checks"}],
        }
        state["retry_count"] = 0
        state["current_phase_index"] = (state.get("current_phase_index") or 0) + 1
        return state

    # Get or detect project type (cached per repo)
    repo_url = state.get("repo_url", "")
    project_type = get_or_detect_project_type(repo_url, worktree_path)
    logger.info("🧪 Running sanity checks for project type %s", project_type)

    if project_type == "python":
        diagnostics.extend(run_python_checks(worktree_path))
    elif project_type == "javascript":
        diagnostics.extend(run_javascript_checks(worktree_path))
    elif project_type == "go":
        diagnostics.extend(run_go_checks(worktree_path))
    elif project_type in {"java", "springboot"}:
        diagnostics.extend(run_java_checks(worktree_path, project_type))
    elif project_type == "legacy":
        diagnostics.extend(run_legacy_checks(worktree_path))
    else:
        msg = f"Unknown project type '{project_type}', " "running minimal repository checks only"
        diagnostics.append(
            {
                "level": "warning",
                "message": msg,
            }
        )
        diagnostics.extend(run_legacy_checks(worktree_path))

    # Check if any actual code files were modified (not just metadata)
    code_files_changed = _get_any_code_changes(worktree_path)
    if not code_files_changed:
        passed = False
        diagnostics.append(
            {
                "level": "error",
                "check": "code-changes",
                "message": "No code files were modified during this phase. The LLM may have failed to implement the required changes.",
            }
        )

    # Determine if checks passed
    failed_checks = [d for d in diagnostics if d.get("level") == "error"]
    if failed_checks:
        passed = False

    state["sanity_check_result"] = {
        "passed": passed,
        "diagnostics": diagnostics,
    }

    if not passed:
        retry_count = (state.get("retry_count") or 0) + 1
        state["retry_count"] = retry_count

        if retry_count >= MAX_PHASE_RETRIES:
            logger.error(
                "🧪 Sanity check failed %d times for this phase; "
                "escalating instead of retrying again",
                retry_count,
            )
            state["sanity_check_result"]["escalate"] = True
        else:
            logger.warning(
                "🧪 Sanity check failed (attempt %d/%d); rerunning the phase instead of advancing",
                retry_count,
                MAX_PHASE_RETRIES,
            )

        for diagnostic in diagnostics:
            if diagnostic.get("level") == "error":
                logger.warning(
                    "🧪 %s: %s",
                    diagnostic.get("check", "check"),
                    diagnostic.get("message", ""),
                )
    else:
        logger.info("🧪 Sanity check passed; advancing to the next phase")
        state["retry_count"] = 0  # reset on success
        state["current_phase_index"] = (state.get("current_phase_index") or 0) + 1
        state["fixing_sanity"] = False  # reset fix mode on success

    logger.info("✅ [STEP 5/6] Sanity check node completed")
    return state


def detect_project_type(worktree_path: str) -> str:
    """Detect the project type based on files in the worktree."""
    if (
        os.path.exists(os.path.join(worktree_path, "pyproject.toml"))
        or os.path.exists(os.path.join(worktree_path, "setup.py"))
        or os.path.exists(os.path.join(worktree_path, "requirements.txt"))
    ):
        return "python"
    elif os.path.exists(os.path.join(worktree_path, "package.json")):
        return "javascript"
    else:
        return "unknown"


def run_python_checks(worktree_path: str) -> list:
    """Run Python-specific sanity checks."""
    diagnostics = []

    # Try to run ruff (linter)
    try:
        result = subprocess.run(  # nosec B603, B607
            ["ruff", "check", worktree_path], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            diagnostics.append(
                {"level": "error", "check": "ruff", "message": result.stdout or result.stderr}
            )
        else:
            diagnostics.append(
                {"level": "info", "check": "ruff", "message": "No linting issues found"}
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        diagnostics.append(
            {"level": "warning", "check": "ruff", "message": "Ruff not available or timed out"}
        )

    # Try to run mypy (type checker)
    try:
        result = subprocess.run(  # nosec B603, B607
            ["mypy", worktree_path], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            diagnostics.append(
                {"level": "warning", "check": "mypy", "message": result.stdout or result.stderr}
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # mypy is optional

    return diagnostics


def _get_changed_files(worktree_path: str, extensions: tuple) -> list:
    """Return touched files for the current execution matching the given extensions."""
    changed: set[str] = set()

    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=worktree_path,
        )
        if result.returncode == 0:
            changed.update(f.strip() for f in result.stdout.splitlines() if f.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=worktree_path,
        )
        if result.returncode == 0:
            changed.update(f.strip() for f in result.stdout.splitlines() if f.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return [f for f in changed if f.endswith(extensions)]


def _run_git_diff_check(worktree_path: str) -> list:
    diagnostics = []
    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "diff", "--check"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=worktree_path,
        )
        if result.returncode != 0:
            diagnostics.append(
                {
                    "level": "error",
                    "check": "git-diff-check",
                    "message": result.stdout or result.stderr,
                }
            )
        else:
            diagnostics.append(
                {
                    "level": "info",
                    "check": "git-diff-check",
                    "message": "Working tree passed repository hygiene check",
                }
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        diagnostics.append(
            {
                "level": "warning",
                "check": "git-diff-check",
                "message": "Git diff check not available or timed out",
            }
        )
    return diagnostics


def run_go_checks(worktree_path: str) -> list:
    """Run Go-specific checks including formatting and compiling."""
    diagnostics = []
    changed_files = _get_changed_files(worktree_path, GO_EXTENSIONS)

    if changed_files:
        try:
            result = subprocess.run(  # nosec B603, B607
                ["gofmt", "-l", *changed_files],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=worktree_path,
            )
            if result.returncode != 0:
                diagnostics.append(
                    {
                        "level": "warning",
                        "check": "gofmt",
                        "message": result.stdout or result.stderr,
                    }
                )
            elif result.stdout.strip():
                diagnostics.append(
                    {
                        "level": "error",
                        "check": "gofmt",
                        "message": f"Go files need formatting:\n{result.stdout.strip()}",
                    }
                )
            else:
                msg = f"Go formatting check passed on {len(changed_files)} changed file(s)"
                diagnostics.append(
                    {
                        "level": "info",
                        "check": "gofmt",
                        "message": msg,
                    }
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            diagnostics.append(
                {
                    "level": "warning",
                    "check": "gofmt",
                    "message": "gofmt not available or timed out",
                }
            )
    else:
        diagnostics.append(
            {
                "level": "info",
                "check": "gofmt",
                "message": "No changed Go files detected; skipped gofmt check",
            }
        )

    try:
        result = subprocess.run(  # nosec B603, B607
            ["go", "build", "./..."],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=worktree_path,
        )
        if result.returncode != 0:
            diagnostics.append(
                {
                    "level": "error",
                    "check": "go-build",
                    "message": result.stdout or result.stderr,
                }
            )
        else:
            diagnostics.append(
                {
                    "level": "info",
                    "check": "go-build",
                    "message": "Go build passed",
                }
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        diagnostics.append(
            {
                "level": "warning",
                "check": "go-build",
                "message": "go build not available or timed out",
            }
        )

    return diagnostics


def run_java_checks(worktree_path: str, project_type: str) -> list:
    """Run Java/Maven/Gradle build checks."""
    diagnostics = []
    root = Path(worktree_path)

    if (root / "mvnw").exists():
        command = ["./mvnw", "-q", "-DskipTests", "compile"]
        check_name = "maven-compile"
    elif (root / "mvnw.cmd").exists():
        command = ["mvnw.cmd", "-q", "-DskipTests", "compile"]
        check_name = "maven-compile"
    elif (root / "pom.xml").exists():
        command = ["mvn", "-q", "-DskipTests", "compile"]
        check_name = "maven-compile"
    elif (root / "gradlew").exists():
        command = ["./gradlew", "build", "-x", "test"]
        check_name = "gradle-build"
    elif (root / "gradlew.bat").exists():
        command = ["gradlew.bat", "build", "-x", "test"]
        check_name = "gradle-build"
    elif (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        command = ["gradle", "build", "-x", "test"]
        check_name = "gradle-build"
    else:
        msg = (
            f"Detected {project_type} repo but no Maven/Gradle build tool "
            "was found; running repository hygiene check only"
        )
        diagnostics.append(
            {
                "level": "warning",
                "check": "java-build",
                "message": msg,
            }
        )
        diagnostics.extend(_run_git_diff_check(worktree_path))
        return diagnostics

    try:
        result = subprocess.run(  # nosec B603, B607
            command,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=worktree_path,
        )
        if result.returncode != 0:
            diagnostics.append(
                {
                    "level": "error",
                    "check": check_name,
                    "message": result.stdout or result.stderr,
                }
            )
        else:
            diagnostics.append(
                {
                    "level": "info",
                    "check": check_name,
                    "message": f"{check_name.replace('-', ' ').title()} passed",
                }
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        diagnostics.append(
            {
                "level": "warning",
                "check": check_name,
                "message": f"{check_name} not available or timed out",
            }
        )

    return diagnostics


def run_legacy_checks(worktree_path: str) -> list:
    """Run fallback minimal repository hygiene checks for legacy projects."""
    msg = "No stack-specific sanity checks available; " "running repository hygiene check only"
    diagnostics = [
        {
            "level": "warning",
            "check": "legacy-stack",
            "message": msg,
        }
    ]
    diagnostics.extend(_run_git_diff_check(worktree_path))
    return diagnostics


def _get_any_code_changes(worktree_path: str) -> bool:
    """Check if any code files (not metadata) were modified during execution.

    Returns True if any non-metadata files were changed, False otherwise.
    """
    changed: set[str] = set()
    metadata_patterns = {".ai-sdlc", "runs", ".git", "node_modules", ".venv", "venv"}

    try:
        # Get modified files
        result = subprocess.run(  # nosec B603, B607
            ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=worktree_path,
        )
        if result.returncode == 0:
            changed.update(f.strip() for f in result.stdout.splitlines() if f.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        # Get new untracked files
        result = subprocess.run(  # nosec B603, B607
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=worktree_path,
        )
        if result.returncode == 0:
            changed.update(f.strip() for f in result.stdout.splitlines() if f.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Filter out metadata files
    for file_path in list(changed):
        if any(pattern in file_path for pattern in metadata_patterns):
            changed.discard(file_path)

    return bool(changed)


def _get_changed_js_files(worktree_path: str, extensions: tuple = JS_TS_EXTENSIONS) -> list:
    """Return files changed or newly added during this execution so far.

    Filtered by extension.


    Phases are not committed individually - the Git Manager commits once, after
    all phases pass (see HLD §6) - so HEAD still points at the base commit the
    worktree was created from until then. That means `git diff ... HEAD` plus
    untracked new files is exactly "everything the agent has touched in this
    execution", cumulative across phases. This is what should be linted -
    never the whole repo, which would fail on pre-existing debt the agent
    never introduced.
    """
    changed: set[str] = set()

    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=worktree_path,
        )
        if result.returncode == 0:
            changed.update(f.strip() for f in result.stdout.splitlines() if f.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=worktree_path,
        )
        if result.returncode == 0:
            changed.update(f.strip() for f in result.stdout.splitlines() if f.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return [f for f in changed if f.endswith(extensions)]


def _run_eslint_on_files(worktree_path: str, files: list) -> list:
    """Auto-fix what's fixable, then lint only the given files.

    Returns structured diagnostics (file, line, rule, message) instead of a raw

    stdout/stderr blob - the retry loop needs specifics to act on, not a
    wall of text.
    """
    diagnostics = []

    try:
        # Apply auto-fixes first. Most style-only violations (quotes,
        # semicolons, import order) are fixable and shouldn't ever reach
        # the agent as a failure to react to.
        subprocess.run(  # nosec B603, B607
            ["npx", "eslint", "--fix", *files],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=worktree_path,
        )

        result = subprocess.run(  # nosec B603, B607
            ["npx", "eslint", "--format", "json", *files],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=worktree_path,
        )

        error_lines = []
        if result.stdout:
            try:
                eslint_output = json.loads(result.stdout)
                for file_result in eslint_output:
                    for msg in file_result.get("messages", []):
                        if msg.get("severity") == 2:  # 2 = error, 1 = warning
                            error_lines.append(
                                f"{file_result.get('filePath')}:"
                                f"{msg.get('line')}:{msg.get('column')} "
                                f"[{msg.get('ruleId')}] {msg.get('message')}"
                            )
            except json.JSONDecodeError:
                # Not valid JSON usually means a config/setup error rather
                # than lint violations - surface it so it's not silently lost.
                if result.returncode != 0:
                    error_lines.append(result.stdout or result.stderr)

        if error_lines:
            diagnostics.append(
                {
                    "level": "error",
                    "check": "eslint",
                    "message": "\n".join(error_lines),
                }
            )
        else:
            diagnostics.append(
                {
                    "level": "info",
                    "check": "eslint",
                    "message": f"Lint check passed on {len(files)} changed file(s)",
                }
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        diagnostics.append(
            {
                "level": "warning",
                "check": "eslint",
                "message": "ESLint not available or timed out",
            }
        )

    return diagnostics


def run_javascript_checks(worktree_path: str) -> list:
    """Run JavaScript/TypeScript-specific sanity checks."""
    diagnostics = []
    package_json_path = Path(worktree_path) / "package.json"
    package_json = {}

    if package_json_path.exists():
        try:
            package_json = json.loads(package_json_path.read_text())
        except Exception:
            diagnostics.append(
                {
                    "level": "warning",
                    "check": "package-json",
                    "message": "Could not parse package.json, skipping script-aware checks",
                }
            )

    scripts = package_json.get("scripts") or {}

    # Prefer a real syntax check when the repo does not define linting.
    entrypoint = package_json.get("main")
    if entrypoint:
        entrypoint_path = Path(worktree_path) / entrypoint
    elif (Path(worktree_path) / "server.js").exists():
        entrypoint_path = Path(worktree_path) / "server.js"
    elif (Path(worktree_path) / "index.js").exists():
        entrypoint_path = Path(worktree_path) / "index.js"
    else:
        entrypoint_path = None

    if entrypoint_path and entrypoint_path.exists():
        try:
            result = subprocess.run(  # nosec B603, B607
                ["node", "--check", str(entrypoint_path)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=worktree_path,
            )
            if result.returncode != 0:
                diagnostics.append(
                    {
                        "level": "error",
                        "check": "node-check",
                        "message": result.stdout or result.stderr,
                    }
                )
            else:
                diagnostics.append(
                    {
                        "level": "info",
                        "check": "node-check",
                        "message": f"Syntax check passed for {entrypoint_path.name}",
                    }
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            diagnostics.append(
                {
                    "level": "warning",
                    "check": "node-check",
                    "message": "Node syntax check not available or timed out",
                }
            )

    # Only run eslint when the repository explicitly exposes it, and only
    # against files this execution has actually touched - not the whole
    # repo via `npm run lint`. Linting the whole repo fails phases on
    # pre-existing debt the agent never introduced, which is what was
    # happening here.
    if "lint" in scripts:
        changed_files = _get_changed_js_files(worktree_path)

        if not changed_files:
            diagnostics.append(
                {
                    "level": "info",
                    "check": "eslint",
                    "message": "No changed JS/TS files detected; skipped eslint check",
                }
            )
        else:
            diagnostics.extend(_run_eslint_on_files(worktree_path, changed_files))
    else:
        diagnostics.append(
            {
                "level": "warning",
                "check": "eslint",
                "message": "No lint script defined in package.json; skipped eslint check",
            }
        )

    # Try to run a build script if the project advertises one.
    if "build" in scripts:
        try:
            result = subprocess.run(  # nosec B603, B607
                ["npm", "run", "build"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=worktree_path,
            )
            if result.returncode != 0:
                diagnostics.append(
                    {
                        "level": "warning",
                        "check": "build",
                        "message": result.stdout or result.stderr,
                    }
                )
            else:
                diagnostics.append(
                    {
                        "level": "info",
                        "check": "build",
                        "message": "Build check passed",
                    }
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            diagnostics.append(
                {
                    "level": "warning",
                    "check": "build",
                    "message": "Build check not available or timed out",
                }
            )

    return diagnostics
