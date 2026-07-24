"""
Pydantic response schemas for agent structured outputs.

Based on the pattern from REFERENCE blueprint for defining response_format schemas.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ToolAgentResponse(BaseModel):
    """Structured response from the Tool Agent (executor).

    Matches REFERENCE blueprint's CompletedStep schema for compatibility.
    """

    step: int = Field(..., description="Which step number was executed.")
    status: Literal["success", "partial", "failed"] = Field(
        ...,
        description="Execution outcome.",
    )
    files_created: list[str] = Field(
        default_factory=list,
        description="Files actually created (relative paths).",
    )
    files_modified: list[str] = Field(
        default_factory=list,
        description="Pre-existing files that were modified.",
    )
    details: str | None = Field(
        default=None,
        description="Brief notes about execution.",
    )
    verification_results: list[str] = Field(
        default_factory=list,
        description="Results of running verification checks.",
    )
