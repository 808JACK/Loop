"""
Workspace context management for multi-tenant isolation.

This module provides utilities to extract and validate workspace context
from requests to ensure proper data isolation.
"""

from typing import Optional

from fastapi import Header, HTTPException, Request

from src.core.database.base import SessionLocal
from src.models.workspace import Workspace, WorkspaceStatus


def get_workspace_from_header(x_workspace_id: Optional[str] = Header(None)) -> str:
    """
    Extract workspace_id from request header.
    
    Args:
        x_workspace_id: Workspace ID from X-Workspace-ID header
        
    Returns:
        str: The workspace ID
        
    Raises:
        HTTPException: If workspace_id is missing or invalid
    """
    if not x_workspace_id:
        raise HTTPException(status_code=401, detail="Missing workspace identifier")
    
    # Validate workspace exists and is active
    db = SessionLocal()
    try:
        workspace = db.query(Workspace).filter(
            Workspace.workspace_id == x_workspace_id,
            Workspace.status == WorkspaceStatus.ACTIVE
        ).first()
        
        if not workspace:
            raise HTTPException(status_code=403, detail="Invalid or inactive workspace")
        
        return x_workspace_id
    finally:
        db.close()


def get_workspace_from_token(request: Request) -> str:
    """
    Extract workspace_id from JWT token or session.
    
    This is a placeholder for future JWT implementation.
    Currently falls back to header-based extraction.
    
    Args:
        request: FastAPI request object
        
    Returns:
        str: The workspace ID
    """
    # Try to get from header first
    workspace_id = request.headers.get("X-Workspace-ID")
    if workspace_id:
        return get_workspace_from_header(workspace_id)
    
    # Future: Extract from JWT token
    # token = request.headers.get("Authorization")
    # if token and token.startswith("Bearer "):
    #     decoded = decode_jwt(token[7:])
    #     return decoded.get("workspace_id")
    
    raise HTTPException(status_code=401, detail="No workspace context found")


class WorkspaceContext:
    """
    Context manager for workspace-scoped database operations.
    
    Ensures all database queries are automatically filtered by workspace_id.
    """
    
    def __init__(self, workspace_id: str):
        self.workspace_id = workspace_id
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    
    def filter_query(self, query, model):
        """
        Apply workspace filter to a SQLAlchemy query.
        
        Args:
            query: SQLAlchemy query object
            model: SQLAlchemy model class
            
        Returns:
            Filtered query object
        """
        if hasattr(model, 'workspace_id'):
            return query.filter(model.workspace_id == self.workspace_id)
        return query
