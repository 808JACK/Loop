"""
Project type detection and caching service.

Detects and caches project type per repository to avoid re-detection on each execution.
"""

from pathlib import Path

from src.services.memory.project_memory_service import (
    get_project_memory,
    update_project_memory,
)


def _exists(worktree_path: str, *relative_paths: str) -> bool:
    root = Path(worktree_path)
    return any((root / rel).exists() for rel in relative_paths)


def _looks_like_spring_boot(worktree_path: str) -> bool:
    root = Path(worktree_path)
    if not _exists(worktree_path, "src/main/java", "src/test/java"):
        return False

    pom_path = root / "pom.xml"
    if pom_path.exists():
        try:
            content = pom_path.read_text(errors="ignore").lower()
            if "spring-boot" in content or "spring.boot" in content:
                return True
        except Exception:  # nosec B110
            # File read errors are expected for some project types
            pass

    gradle_files = [root / "build.gradle", root / "build.gradle.kts"]
    for gradle_path in gradle_files:
        if gradle_path.exists():
            try:
                content = gradle_path.read_text(errors="ignore").lower()
                if "spring-boot" in content or "org.springframework.boot" in content:
                    return True
            except Exception:  # nosec B110
                # File read errors are expected for some project types
                pass

    return _exists(worktree_path, "application.properties", "application.yml", "application.yaml")


def detect_project_type(worktree_path: str) -> str:
    """
    Detect the project type based on files in the worktree.

    Args:
        worktree_path: Path to the worktree

    Returns:
        Project type: 'python', 'javascript', 'go', 'java', 'springboot', 'legacy', or 'unknown'
    """
    if _exists(worktree_path, "pyproject.toml", "setup.py", "requirements.txt", "Pipfile"):
        return "python"

    if _exists(worktree_path, "go.mod", "go.sum") or any(Path(worktree_path).rglob("*.go")):
        return "go"

    if _exists(
        worktree_path,
        "pom.xml",
        "mvnw",
        "mvnw.cmd",
        "build.gradle",
        "build.gradle.kts",
        "gradlew",
        "gradlew.bat",
    ):
        if _looks_like_spring_boot(worktree_path):
            return "springboot"
        return "java"

    if _exists(worktree_path, "package.json"):
        return "javascript"

    if _exists(
        worktree_path, "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml"
    ):
        return "legacy"

    return "unknown"


def get_cached_project_type(repo_url: str) -> str | None:
    """
    Get cached project type for a repository.

    Args:
        repo_url: Repository URL

    Returns:
        Cached project type or None if not cached
    """
    memory = get_project_memory(repo_url)
    if memory:
        return memory.get("project_type")  # type: ignore[no-any-return]
    return None


def cache_project_type(repo_url: str, project_type: str) -> bool:
    """
    Cache project type for a repository.

    Args:
        repo_url: Repository URL
        project_type: Detected project type

    Returns:
        True if successful, False otherwise
    """
    try:
        return update_project_memory(repo_url, {"project_type": project_type})
    except Exception as e:
        print(f"Error caching project type: {e}")
        return False


def get_or_detect_project_type(repo_url: str, worktree_path: str) -> str:
    """
    Get cached project type or detect and cache it.

    Args:
        repo_url: Repository URL
        worktree_path: Path to worktree for detection

    Returns:
        Project type
    """
    cached = get_cached_project_type(repo_url)
    if cached and cached not in {"unknown", "legacy"}:
        return cached

    detected = detect_project_type(worktree_path)
    cache_project_type(repo_url, detected)
    return detected
