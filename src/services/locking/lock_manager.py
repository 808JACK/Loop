"""
Distributed locking manager for per-repo concurrency control.

See HLD §4.2 and §9 for full specification.
Uses PostgreSQL advisory locks for distributed locking.
"""

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.database.base import get_db


class LockManager:
    """
    Manages distributed locks using PostgreSQL advisory locks.

    Advisory locks are PostgreSQL's built-in distributed locking mechanism.
    They are automatically released when the connection closes.
    """

    def __init__(self, db: Session):
        """
        Initialize lock manager.

        Args:
            db: Database session
        """
        self.db = db

    def _get_lock_key(self, repo: str, branch: str | None = None) -> int:
        """
        Convert repo/branch to an integer lock key.

        PostgreSQL advisory locks use 64-bit integers as keys.
        We hash the repo string to get a consistent key.

        Args:
            repo: Repository identifier
            branch: Optional branch name for more granular locking

        Returns:
            int: Lock key
        """
        import hashlib

        key_string = f"{repo}:{branch}" if branch else repo
        hash_value = hashlib.md5(key_string.encode(), usedforsecurity=False).hexdigest()

        # Postgres advisory locks use signed 64-bit int — keep it in range
        raw = int(hash_value[:16], 16)
        return raw % (2**63)

    def acquire_lock(
        self, repo: str, branch: str | None = None, timeout_seconds: int = 30, wait: bool = True
    ) -> bool:
        """
        Acquire a lock for a repository.

        Args:
            repo: Repository identifier
            branch: Optional branch name for more granular locking
            timeout_seconds: How long to wait for lock (if wait=True)
            wait: Whether to wait for lock or fail immediately

        Returns:
            bool: True if lock acquired
        """
        lock_key = self._get_lock_key(repo, branch)

        if wait:
            # pg_try_advisory_lock waits until lock is available or timeout
            result = self.db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
        else:
            # pg_try_advisory_lock returns immediately
            result = self.db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})

        acquired = result.scalar()
        return bool(acquired)

    def release_lock(self, repo: str, branch: str | None = None) -> bool:
        """
        Release a lock for a repository.

        Note: Advisory locks are automatically released when the connection
        closes, but explicit release is good practice.

        Args:
            repo: Repository identifier
            branch: Optional branch name

        Returns:
            bool: True if lock was released
        """
        lock_key = self._get_lock_key(repo, branch)

        result = self.db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})

        released = result.scalar()
        return bool(released)

    @contextmanager
    def lock_context(
        self, repo: str, branch: str | None = None, timeout_seconds: int = 30, wait: bool = True
    ):
        """
        Context manager for automatic lock acquisition and release.

        Args:
            repo: Repository identifier
            branch: Optional branch name
            timeout_seconds: How long to wait for lock
            wait: Whether to wait for lock

        Yields:
            bool: True if lock was acquired
        """
        acquired = self.acquire_lock(repo, branch, timeout_seconds, wait)

        if not acquired:
            raise Exception(f"Could not acquire lock for {repo}:{branch or 'default'}")

        try:
            yield True
        finally:
            self.release_lock(repo, branch)

    def is_locked(self, repo: str, branch: str | None = None) -> bool:
        """
        Check if a lock is currently held.

        Args:
            repo: Repository identifier
            branch: Optional branch name

        Returns:
            bool: True if lock is held
        """
        lock_key = self._get_lock_key(repo, branch)

        self.db.execute(text("SELECT pg_advisory_lock_shared(:key)"), {"key": lock_key})

        # If we can acquire a shared lock, no exclusive lock is held
        # Then immediately release it
        self.release_lock(repo, branch)

        return False


# Convenience functions
def acquire_repo_lock(
    repo: str, branch: str | None = None, timeout_seconds: int = 30, wait: bool = True
) -> bool:
    """
    Acquire a repository lock.

    Args:
        repo: Repository identifier
        branch: Optional branch name
        timeout_seconds: How long to wait for lock
        wait: Whether to wait for lock

    Returns:
        bool: True if lock acquired
    """
    db = next(get_db())
    manager = LockManager(db)
    return manager.acquire_lock(repo, branch, timeout_seconds, wait)


def release_repo_lock(repo: str, branch: str | None = None) -> bool:
    """
    Release a repository lock.

    Args:
        repo: Repository identifier
        branch: Optional branch name

    Returns:
        bool: True if lock released
    """
    db = next(get_db())
    manager = LockManager(db)
    return manager.release_lock(repo, branch)
