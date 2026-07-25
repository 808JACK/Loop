"""Workspace model - multi-tenant isolation."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base


class WorkspaceStatus(PyEnum):
    """Status of a workspace."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class Workspace(Base):
    """
    Workspace - multi-tenant isolation unit.
    
    Each workspace represents a Jira instance/organization and isolates
    all data (executions, project memory, checkpoints, etc.) per workspace.
    """

    __tablename__ = "workspaces"

    workspace_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Jira integration details
    jira_url: Mapped[str] = mapped_column(String(512), nullable=False)
    jira_project_key: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # OAuth credentials (encrypted in production)
    jira_oauth_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jira_oauth_client_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # User who owns this workspace (from OAuth)
    owner_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Jira Cloud site details
    jira_cloud_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jira_site_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # OAuth tokens (store for webhook/API calls)
    jira_access_token: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    jira_refresh_token: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    
    status: Mapped[WorkspaceStatus] = mapped_column(
        SQLEnum(WorkspaceStatus), nullable=False, default=WorkspaceStatus.ACTIVE, index=True
    )
    
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        """Convert workspace to dictionary."""
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "jira_url": self.jira_url,
            "jira_project_key": self.jira_project_key,
            "owner_email": self.owner_email,
            "owner_name": self.owner_name,
            "status": self.status.value if self.status else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
