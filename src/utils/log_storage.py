"""Log storage service for persistent execution logs.

Stores execution logs in the database before worktree cleanup.
"""

import os
from datetime import datetime

from src.core.database.base import get_db
from src.models.execution_log import ExecutionLog


def store_execution_log(
    execution_id: str,
    log_file_path: str,
    repo_url: str | None = None,
) -> bool:
    """
    Store execution log content to database.

    Args:
        execution_id: Execution identifier
        log_file_path: Path to the log file
        repo_url: Optional repository URL

    Returns:
        True if successful, False otherwise
    """
    if not os.path.exists(log_file_path):
        print(f"Log file not found: {log_file_path}")
        return False

    try:
        with open(log_file_path) as f:
            log_content = f.read()

        if not log_content.strip():
            print(f"Log file is empty: {log_file_path}")
            return False

        db = next(get_db())
        try:
            # Check if log already exists
            existing = (
                db.query(ExecutionLog).filter(ExecutionLog.execution_id == execution_id).first()
            )

            if existing:
                # Update existing log
                existing.log_content = log_content  # type: ignore[assignment]
                existing.file_size = len(log_content)  # type: ignore[assignment]
                existing.updated_at = datetime.utcnow()  # type: ignore[assignment]
            else:
                # Create new log entry
                execution_log = ExecutionLog(
                    execution_id=execution_id,
                    log_content=log_content,
                    file_size=len(log_content),
                    repo_url=repo_url,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(execution_log)

            db.commit()
            print(f"Stored execution log for {execution_id} ({len(log_content)} bytes)")
            return True
        except Exception as e:
            db.rollback()
            print(f"Error storing execution log: {e}")
            return False
        finally:
            db.close()
    except Exception as e:
        print(f"Error reading log file: {e}")
        return False


def get_execution_log(execution_id: str) -> dict | None:
    """
    Retrieve execution log from database.

    Args:
        execution_id: Execution identifier

    Returns:
        Log data dictionary or None if not found
    """
    db = next(get_db())
    try:
        log = db.query(ExecutionLog).filter(ExecutionLog.execution_id == execution_id).first()

        if log:
            return log.to_dict()  # type: ignore[no-any-return]
        return None
    except Exception as e:
        print(f"Error retrieving execution log: {e}")
        return None
    finally:
        db.close()


def delete_execution_log(execution_id: str) -> bool:
    """
    Delete execution log from database.

    Args:
        execution_id: Execution identifier

    Returns:
        True if deleted, False otherwise
    """
    db = next(get_db())
    try:
        log = db.query(ExecutionLog).filter(ExecutionLog.execution_id == execution_id).first()

        if log:
            db.delete(log)
            db.commit()
            return True
        return False
    except Exception as e:
        db.rollback()
        print(f"Error deleting execution log: {e}")
        return False
    finally:
        db.close()
