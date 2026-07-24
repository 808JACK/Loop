"""
Repository search tools for the tool-calling agent.

These tools provide ripgrep and AST-based search capabilities.
See HLD §4.6 for the full tool specification.
"""

import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from src.settings import settings

# Tree-sitter imports
try:
    import tree_sitter
    import tree_sitter_bash
    import tree_sitter_c
    import tree_sitter_cpp
    import tree_sitter_css
    import tree_sitter_go
    import tree_sitter_html
    import tree_sitter_java
    import tree_sitter_javascript
    import tree_sitter_json
    import tree_sitter_markdown
    import tree_sitter_php
    import tree_sitter_python
    import tree_sitter_ruby
    import tree_sitter_rust
    import tree_sitter_typescript

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = None


# Language mapping for file extensions
LANGUAGE_MAP: dict[str, str] = {
    # Python
    ".py": "python",
    # JavaScript/TypeScript
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    # JSON
    ".json": "json",
    # Go
    ".go": "go",
    # Rust
    ".rs": "rust",
    # Java
    ".java": "java",
    # C/C++
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    # Ruby
    ".rb": "ruby",
    # PHP
    ".php": "php",
    # HTML
    ".html": "html",
    ".htm": "html",
    # CSS
    ".css": "css",
    ".scss": "css",
    ".sass": "css",
    # Shell/Bash
    ".sh": "bash",
    ".bash": "bash",
    # Markdown
    ".md": "markdown",
    ".markdown": "markdown",
}

# Parser cache
_PARSERS: dict[str, Any] = {}


def get_language_from_extension(file_path: Path) -> str | None:
    """Detect language from file extension."""
    ext = file_path.suffix.lower()
    return LANGUAGE_MAP.get(ext)


def get_parser(language: str):
    """Get or create tree-sitter parser for a language."""
    if not TREE_SITTER_AVAILABLE:
        raise NotImplementedError("Tree-sitter library not installed")

    if language in _PARSERS:
        return _PARSERS[language]

    language_obj = None
    if language == "python":
        language_obj = tree_sitter.Language(tree_sitter_python.language())
    elif language == "javascript":
        language_obj = tree_sitter.Language(tree_sitter_javascript.language())
    elif language == "typescript":
        language_obj = tree_sitter.Language(tree_sitter_typescript.language())
    elif language == "json":
        language_obj = tree_sitter.Language(tree_sitter_json.language())
    elif language == "go":
        language_obj = tree_sitter.Language(tree_sitter_go.language())
    elif language == "rust":
        language_obj = tree_sitter.Language(tree_sitter_rust.language())
    elif language == "java":
        language_obj = tree_sitter.Language(tree_sitter_java.language())
    elif language == "c":
        language_obj = tree_sitter.Language(tree_sitter_c.language())
    elif language == "cpp":
        language_obj = tree_sitter.Language(tree_sitter_cpp.language())
    elif language == "ruby":
        language_obj = tree_sitter.Language(tree_sitter_ruby.language())
    elif language == "php":
        language_obj = tree_sitter.Language(tree_sitter_php.language_php())
    elif language == "html":
        language_obj = tree_sitter.Language(tree_sitter_html.language())
    elif language == "css":
        language_obj = tree_sitter.Language(tree_sitter_css.language())
    elif language == "bash":
        language_obj = tree_sitter.Language(tree_sitter_bash.language())
    elif language == "markdown":
        language_obj = tree_sitter.Language(tree_sitter_markdown.language())
    else:
        raise ValueError(f"Unsupported language: {language}")

    parser = tree_sitter.Parser(language_obj)
    _PARSERS[language] = parser
    return parser


def get_worktree_path(worktree_path: str | None = None) -> Path:
    """Get validated worktree path — accepts anything under WORKTREE_BASE_PATH."""
    base_path = Path(settings.worktree_base_path)
    worktree = Path(worktree_path) if worktree_path else base_path

    try:
        worktree.resolve().relative_to(base_path.resolve())
    except ValueError:
        raise ValueError(f"Worktree path {worktree} is outside allowed base {base_path}")

    return worktree


def resolve_path(file_path: str, worktree: Path) -> Path:
    """Resolve relative or absolute path to an absolute path inside the worktree."""
    p = Path(file_path)
    if p.is_absolute():
        try:
            rel = p.relative_to(worktree)
            return worktree / rel
        except ValueError:
            raise ValueError(f"Path {p} is outside worktree {worktree}")
    return worktree / p


@tool
def grep_search(
    pattern: str,
    path: str = ".",
    worktree_path: str | None = None,
    case_sensitive: bool = False,
    regex: bool = True,
) -> str:
    """
    Search for text/regex patterns across the worktree using ripgrep.

    Args:
        pattern: Search pattern (regex if regex=True, literal otherwise)
        path: Relative path to search within (default: worktree root)
        worktree_path: Optional specific worktree path
        case_sensitive: Whether search is case-sensitive
        regex: Whether pattern is a regex (True) or literal string (False)

    Returns:
        str: Search results with line numbers and context

    Raises:
        ValueError: If path is outside worktree
    """
    worktree = get_worktree_path(worktree_path)
    search_path = resolve_path(path, worktree)

    if not search_path.exists():
        raise FileNotFoundError(f"Path not found: {search_path}")

    # Build ripgrep command
    cmd = ["rg", "--line-number", "--no-heading"]

    if not case_sensitive:
        cmd.append("--ignore-case")

    if not regex:
        cmd.append("--fixed-strings")

    cmd.extend(["--", pattern, str(search_path)])

    try:
        result = subprocess.run(  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return result.stdout or "No matches found"
        else:
            return f"Search failed: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "Search timed out"
    except FileNotFoundError:
        return "ripgrep (rg) not found - install it to use search tools"


@tool
def ast_query(
    path: str, query: str, language: str | None = None, worktree_path: str | None = None
) -> str:
    """
    Perform tree-sitter-based structural lookup on a file.

    This allows querying for functions, classes, imports, etc.

    Args:
        path: Relative path to the file within the worktree
        query: Tree-sitter query string (e.g., "(function_definition) @func")
        language: Language hint (auto-detected if None)
        worktree_path: Optional specific worktree path

    Returns:
        str: Query results with matches and line numbers

    Raises:
        ValueError: If path is outside worktree
        NotImplementedError: If tree-sitter not available
    """
    worktree = get_worktree_path(worktree_path)
    file_path = resolve_path(path, worktree)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if not TREE_SITTER_AVAILABLE:
        # Fallback to grep when tree-sitter is not available
        try:
            result = subprocess.run(  # nosec B603 B607
                ["grep", "-n", query, str(file_path)], capture_output=True, text=True, check=False
            )
            if result.returncode == 0:
                return f"Tree-sitter not available, using grep fallback:\n{result.stdout}"
            else:
                return "No matches found (grep fallback)"
        except Exception as e:
            return f"Tree-sitter not available and grep fallback failed: {e}"

    # Detect language if not provided
    if language is None:
        language = get_language_from_extension(file_path)
        if language is None:
            return f"Cannot detect language for file: {file_path.suffix}"

    try:
        # Get parser
        parser = get_parser(language)

        # Read file content
        with open(file_path, encoding="utf-8") as f:
            source_code = f.read()

        # Parse the source code
        tree = parser.parse(source_code.encode("utf-8"))

        # Parse the query using the parser's language
        query_obj = tree_sitter.Query(parser.language, query)

        # Execute query using QueryCursor
        cursor = tree_sitter.QueryCursor(query_obj)
        captures = cursor.captures(tree.root_node)

        # Format results (captures returns dict with capture names as keys)
        results = []
        for capture_name, nodes in captures.items():
            for node in nodes:
                line_number = node.start_point[0] + 1
                node_text = node.text.decode("utf-8", errors="ignore")
                # Truncate long nodes
                if len(node_text) > 100:
                    node_text = node_text[:97] + "..."
                results.append(f"Line {line_number} [{capture_name}]: {node_text}")

        if not results:
            return f"No matches found for query in {path}"

        return f"Found {len(results)} match(es) in {path}:\n" + "\n".join(results)

    except Exception as e:
        return f"Tree-sitter query failed: {str(e)}"


@tool
def git_diff(worktree_path: str | None = None) -> str:
    """
    Show the accumulated diff in the worktree.

    This helps the agent see what changes it has made so far.

    Args:
        worktree_path: Optional specific worktree path

    Returns:
        str: Git diff output

    Raises:
        ValueError: If path is outside worktree
    """
    worktree = get_worktree_path(worktree_path)

    cmd = ["git", "diff"]

    try:
        result = subprocess.run(  # nosec B603
            cmd,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return result.stdout or "No changes detected"
        else:
            return f"Git diff failed: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "Git diff timed out"
    except FileNotFoundError:
        return "Git not found"
