"""WebSocket endpoint for real-time execution logs streaming."""

import json
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.core.logging.logger import get_logger

logger = get_logger("websocket")
router = APIRouter()

# Sensitive tags to filter from user-facing logs
SENSITIVE_TAGS = {"roadmap", "planner", "checkpoint"}
ALLOWED_TAGS = {"tool", "git", "sanity", "jira", "llm"}

# Store active WebSocket connections by execution_id
active_connections: Dict[str, WebSocket] = {}


class ConnectionManager:
    """Manage WebSocket connections for per-execution log streaming."""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, execution_id: str):
        """Accept a WebSocket connection for a specific execution."""
        await websocket.accept()
        self.active_connections[execution_id] = websocket
        logger.info(f"WebSocket connected for execution: {execution_id}")

    def disconnect(self, execution_id: str):
        """Remove WebSocket connection for an execution."""
        if execution_id in self.active_connections:
            del self.active_connections[execution_id]
        logger.info(f"WebSocket disconnected for execution: {execution_id}")

    async def send_log(self, execution_id: str, log_data: dict):
        """Send a log entry to the specific execution's WebSocket.
        
        Filters out sensitive tags before sending to user.
        """
        # Filter sensitive tags from real-time logs
        tag = log_data.get("tag", "")
        if tag in SENSITIVE_TAGS:
            print(f"[SANITIZATION] WebSocket: Filtered sensitive tag {tag} - {log_data.get('msg', '')[:50]}...")
            return  # Don't send sensitive workflow internals to user
        
        # Only allow allowed tags, default to tool
        final_tag = tag if tag in ALLOWED_TAGS else "tool"
        sanitized_data = {
            "t": log_data.get("t", ""),
            "level": log_data.get("level", "info"),
            "tag": final_tag,
            "msg": log_data.get("msg", ""),
        }
        
        print(f"[SANITIZATION] WebSocket: Streaming event - tag: {final_tag}, msg: {log_data.get('msg', '')[:50]}...")
        
        if execution_id in self.active_connections:
            try:
                await self.active_connections[execution_id].send_json({
                    "type": "log",
                    "data": sanitized_data,
                })
            except Exception as e:
                logger.error(f"Error sending log to WebSocket: {e}")
                self.disconnect(execution_id)

    async def send_status(self, execution_id: str, status: str, phase: str = None):
        """Send status update to the specific execution's WebSocket."""
        if execution_id in self.active_connections:
            try:
                await self.active_connections[execution_id].send_json({
                    "type": "status",
                    "status": status,
                    "phase": phase,
                })
            except Exception as e:
                logger.error(f"Error sending status to WebSocket: {e}")
                self.disconnect(execution_id)

    async def complete(self, execution_id: str, final_status: str):
        """Send completion signal and close connection."""
        if execution_id in self.active_connections:
            try:
                await self.active_connections[execution_id].send_json({
                    "type": "complete",
                    "status": final_status,
                })
                await self.active_connections[execution_id].close()
            except Exception as e:
                logger.error(f"Error sending completion to WebSocket: {e}")
            finally:
                self.disconnect(execution_id)

    async def broadcast_jira_update(self, issue_key: str, status: str):
        """Broadcast Jira status update to all connected clients."""
        message = {
            "type": "jira_status",
            "issue_key": issue_key,
            "status": status,
        }
        
        logger.info(f"Broadcasting Jira status update: {issue_key} -> {status}")
        
        # Send to all active connections
        for exec_id, websocket in self.active_connections.items():
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to {exec_id}: {e}")
                self.disconnect(exec_id)


manager = ConnectionManager()


@router.websocket("/ws/execution/{execution_id}")
async def websocket_endpoint(websocket: WebSocket, execution_id: str):
    """WebSocket endpoint for streaming logs for a specific execution."""
    await manager.connect(websocket, execution_id)
    
    try:
        # Keep connection alive until workflow completes
        while True:
            # Handle any client messages (like ping/pong)
            data = await websocket.receive_text()
            logger.debug(f"Received WebSocket message: {data}")
    except WebSocketDisconnect:
        manager.disconnect(execution_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(execution_id)


def stream_log(execution_id: str, log_entry: dict):
    """Stream a log entry to the execution's WebSocket.
    
    This function can be called from the workflow to send real-time logs.
    """
    import asyncio
    
    async def _send():
        await manager.send_log(execution_id, log_entry)
    
    try:
        loop = asyncio.get_running_loop()
        asyncio.create_task(_send())
    except RuntimeError:
        logger.warning("No event loop running for log streaming")


def update_status(execution_id: str, status: str, phase: str = None):
    """Update status for the execution's WebSocket.
    
    This function can be called from the workflow to send status updates.
    """
    import asyncio
    
    async def _send():
        await manager.send_status(execution_id, status, phase)
    
    try:
        loop = asyncio.get_running_loop()
        asyncio.create_task(_send())
    except RuntimeError:
        logger.warning("No event loop running for status update")


def complete_execution(execution_id: str, final_status: str):
    """Complete the execution and close WebSocket connection.
    
    This function should be called when the workflow finishes.
    """
    import asyncio
    
    async def _send():
        await manager.complete(execution_id, final_status)
    
    try:
        loop = asyncio.get_running_loop()
        asyncio.create_task(_send())
    except RuntimeError:
        logger.warning("No event loop running for completion")
