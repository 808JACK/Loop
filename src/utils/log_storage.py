"""Log storage service for persistent execution logs.

Stores sanitized execution logs in PostgreSQL, supporting real-time event streaming
and resume log appending while filtering out internal workflow secrets.
"""

import json
import os
import re
import uuid
from datetime import datetime

from src.core.database.base import SessionLocal
from src.models.execution_log import ExecutionLog


ALLOWED_TAGS = {"tool", "git", "sanity", "jira", "llm", "roadmap", "planner", "checkpoint"}
INTERNAL_KEYWORDS = ["system_prompt", "workflow_definition", "internal_agent_state", "prompt_template"]
SENSITIVE_TAGS = {"roadmap", "planner", "checkpoint"}


def parse_and_sanitize_logs(raw_input: str) -> list[dict]:
    """
    Parse raw terminal logs or JSON strings and store ALL logs for developer access.
    
    Returns all event objects with fields: {t, level, tag, msg}.
    Only filters internal keywords (system_prompt, etc.) - stores ALL tags including sensitive ones.
    Sanitization for users happens at API retrieval layer.
    """
    if not raw_input or not raw_input.strip():
        return []

    # If already a JSON array of events, store all except internal keywords
    if raw_input.strip().startswith("[") and raw_input.strip().endswith("]"):
        try:
            items = json.loads(raw_input)
            all_events = []
            filtered_count = 0
            for item in items:
                if isinstance(item, dict) and "msg" in item:
                    msg = item.get("msg", "")
                    tag = item.get("tag", "")
                    # Only filter internal keywords - store everything else including sensitive tags
                    if any(kw in msg.lower() for kw in INTERNAL_KEYWORDS):
                        filtered_count += 1
                        print(f"[STORAGE] Filtered internal keyword: {tag} - {msg[:50]}...")
                        continue
                    # Store all tags including sensitive ones for developer access
                    final_tag = tag if tag in ALLOWED_TAGS else "tool"
                    all_events.append({
                        "t": item.get("t", datetime.utcnow().strftime("%H:%M:%S")),
                        "level": item.get("level", "info"),
                        "tag": final_tag,
                        "msg": msg,
                    })
            print(f"[STORAGE] Parsed {len(items)} logs, filtered {filtered_count} internal keywords, stored {len(all_events)}")
            return all_events
        except json.JSONDecodeError:
            pass

    # Parse raw line-by-line log format
    events = []
    lines = raw_input.splitlines()
    filtered_count = 0
    for line in lines:
        line_str = line.strip()
        if not line_str or any(kw in line_str.lower() for kw in INTERNAL_KEYWORDS):
            if line_str:
                filtered_count += 1
                print(f"[STORAGE] Filtered internal keyword: {line_str[:50]}...")
            continue

        # Extract timestamp if present [HH:MM:SS]
        ts_match = re.search(r"(\d{2}:\d{2}:\d{2})", line_str)
        t_val = ts_match.group(1) if ts_match else datetime.utcnow().strftime("%H:%M:%S")

        level = "info"
        if "ERROR" in line_str or "FAILED" in line_str or "Exception" in line_str:
            level = "error"
        elif "WARN" in line_str or "WARNING" in line_str or "429" in line_str:
            level = "warn"

        tag = "tool"
        if "git " in line_str or "commit" in line_str or "Pushed branch" in line_str:
            tag = "git"
        elif "mvn " in line_str or "pytest" in line_str or "eslint" in line_str or "tsc:" in line_str or "test" in line_str.lower():
            tag = "sanity"
        elif "Transitioned" in line_str or "Jira" in line_str or "KAN-" in line_str:
            tag = "jira"
        elif "provider" in line_str or "LLM" in line_str:
            tag = "llm"
        elif "Checkpoint" in line_str:
            tag = "checkpoint"
        elif "Roadmap" in line_str:
            tag = "roadmap"
        elif "Phase" in line_str:
            tag = "planner"

        events.append({
            "t": t_val,
            "level": level,
            "tag": tag,
            "msg": line_str[:300],  # Truncate overly verbose raw output
        })

    print(f"[STORAGE] Parsed {len(lines)} raw lines, filtered {filtered_count} internal keywords, stored {len(events)}")
    return events


def store_execution_log(
    execution_id: str,
    raw_log_content: str,
    workspace_id: str = "stackbehare12.atlassian.net_stackbehare12@gmail.com",
    is_resume: bool = False,
    repo_url: str | None = None,
) -> bool:
    """
    Store sanitized execution log content to database.

    If is_resume=True, appends new events to existing log entries rather than replacing.
    """
    new_events = parse_and_sanitize_logs(raw_log_content)
    if not new_events:
        return False

    db = SessionLocal()
    try:
        existing = db.query(ExecutionLog).filter(ExecutionLog.execution_id == execution_id).first()

        if existing and is_resume:
            # Append events on resume
            try:
                current_events = json.loads(existing.log_content) if existing.log_content else []
            except json.JSONDecodeError:
                current_events = []

            # Deduplicate by msg + timestamp
            existing_set = {(e.get("t"), e.get("msg")) for e in current_events if isinstance(e, dict)}
            for ev in new_events:
                if (ev["t"], ev["msg"]) not in existing_set:
                    current_events.append(ev)

            merged_json = json.dumps(current_events)
            existing.log_content = merged_json
            existing.file_size = len(merged_json)
            existing.updated_at = datetime.utcnow()
        else:
            # Insert or overwrite for new runs
            log_json = json.dumps(new_events)
            if existing:
                existing.log_content = log_json
                existing.file_size = len(log_json)
                existing.updated_at = datetime.utcnow()
            else:
                new_entry = ExecutionLog(
                    id=uuid.uuid4(),
                    workspace_id=workspace_id,
                    execution_id=execution_id,
                    log_content=log_json,
                    file_size=len(log_json),
                    repo_url=repo_url,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(new_entry)

        db.commit()
        print(f"Stored sanitized logs for {execution_id} (is_resume={is_resume})")
        return True
    except Exception as e:
        db.rollback()
        print(f"Error storing execution log: {e}")
        return False
    finally:
        db.close()


def append_execution_event(
    execution_id: str,
    tag: str,
    msg: str,
    level: str = "info",
    workspace_id: str = "stackbehare12.atlassian.net_stackbehare12@gmail.com",
) -> bool:
    """
    Append a single execution log event in real-time as the workflow executes.
    Stores ALL logs for developer access - only filters internal keywords.
    Sanitization for users happens at API retrieval layer.
    """
    # Only filter internal keywords - store everything else including sensitive tags
    if any(kw in msg.lower() for kw in INTERNAL_KEYWORDS):
        print(f"[STORAGE] Real-time: Filtered internal keyword - {msg[:50]}...")
        return False  # Filter secret workflow internals

    event = {
        "t": datetime.utcnow().strftime("%H:%M:%S"),
        "level": level,
        "tag": tag if tag in ALLOWED_TAGS else "tool",
        "msg": msg,
    }

    print(f"[STORAGE] Real-time: Storing event - tag: {event['tag']}, msg: {msg[:50]}...")

    db = SessionLocal()
    try:
        existing = db.query(ExecutionLog).filter(ExecutionLog.execution_id == execution_id).first()
        if existing:
            try:
                events = json.loads(existing.log_content) if existing.log_content else []
            except json.JSONDecodeError:
                events = []
            events.append(event)
            merged = json.dumps(events)
            existing.log_content = merged
            existing.file_size = len(merged)
            existing.updated_at = datetime.utcnow()
        else:
            initial = json.dumps([event])
            new_entry = ExecutionLog(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                execution_id=execution_id,
                log_content=initial,
                file_size=len(initial),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(new_entry)

        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error appending execution event: {e}")
        return False
    finally:
        db.close()


def get_execution_log(execution_id: str) -> dict | None:
    """Retrieve execution log from database."""
    db = SessionLocal()
    try:
        log = db.query(ExecutionLog).filter(ExecutionLog.execution_id == execution_id).first()
        if log:
            return log.to_dict()
        return None
    except Exception as e:
        print(f"Error retrieving execution log: {e}")
        return None
    finally:
        db.close()


def delete_execution_log(execution_id: str) -> bool:
    """Delete execution log from database."""
    db = SessionLocal()
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
