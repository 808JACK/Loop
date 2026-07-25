"""
Configuration management for AI SDLC Automation.

Environment-based configuration with type safety and validation.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# settings.py is now at app/settings.py, so repo root is 2 levels up
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_ROOT = _REPO_ROOT / "backend"

# Load environment variables explicitly before Settings() is instantiated.
# Backend .env takes precedence over the repo-level .env.
load_dotenv(_REPO_ROOT / ".env", override=False)
if _BACKEND_ROOT.exists():
    load_dotenv(_BACKEND_ROOT / ".env", override=False)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # Application
    app_name: str = "AI SDLC Automation"
    app_version: str = "0.1.0"
    environment: str = Field(default="development", pattern="^(development|staging|production)$")
    debug: bool = False

    # Server
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8000
    workers: int = 4

    # Database
    database_url: str = Field(
        default="postgresql://root:Sarthak%40123@localhost:5432/ai_sdlc",
        description="PostgreSQL connection URL",
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Supabase
    next_public_supabase_url: str | None = Field(
        default=None, description="Supabase Project URL"
    )
    next_public_supabase_publishable_key: str | None = Field(
        default=None, description="Supabase Publishable Key"
    )
    supabase_url: str | None = Field(default=None, description="Supabase Project URL")
    supabase_key: str | None = Field(default=None, description="Supabase API/Anon Key")

    # Redis (for caching and optional queue)
    redis_url: str | None = Field(default=None, description="Redis connection URL (optional)")

    # Queue (SQS or Redis Streams)
    queue_type: str = Field(default="sqs", pattern="^(sqs|redis)$")
    sqs_region: str | None = None
    sqs_queue_url: str | None = None
    sqs_access_key_id: str | None = None
    sqs_secret_access_key: str | None = None

    # LLM Provider Configuration
    llm_provider: str = Field(default="ollama", pattern="^(claude|gemini|groq|ollama)$")
    llm_model: str = "qwen2.5-coder:32b"

    # Anthropic (Claude)
    anthropic_api_key: str | None = None

    # Google (Gemini)
    google_api_key: str | None = None

    # Groq
    groq_api_key: str | None = None

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str | None = None

    # LLM Throttler / Rate Limiting
    llm_requests_per_minute: int = Field(
        default=8, description="Max LLM API calls per minute (token bucket)"
    )
    llm_max_retries: int = Field(
        default=3, description="Max retries on 429 before raising RateLimitError"
    )
    llm_retry_base_delay: int = Field(
        default=15, description="Base delay in seconds for 429 retry backoff"
    )
    llm_queue_timeout_seconds: int = Field(
        default=900, description="Maximum time to queue LLM requests after 429 before failing"
    )

    # LangGraph Checkpointer (Postgres)
    langgraph_postgres_url: str = Field(
        default="postgresql://root:Sarthak%40123@localhost:5432/ai_sdlc",
        description="PostgreSQL URL for LangGraph checkpointer",
    )

    # Jira/Confluence
    jira_url: str | None = None
    jira_username: str | None = None
    jira_api_token: str | None = None
    jira_project_key: str | None = None
    jira_ready_label: str = "ai-ready"
    jira_repo_url_fields: str = "customfield_10100,customfield_10038"
    jira_repo_name_fields: str = "customfield_10101,customfield_10039"
    jira_reviewer_fields: str = "customfield_10102,customfield_10040"
    github_repo_owner: str | None = None
    # Jira OAuth 2.0
    jira_oauth_client_id: str | None = None
    jira_oauth_client_secret: str | None = None
    frontend_url: str = "http://localhost:5173"

    # Git/PR Platform (GitHub/GitLab/Bitbucket)
    git_platform: str = Field(default="github", pattern="^(github|gitlab|bitbucket)$")
    github_token: str | None = None
    github_app_id: int | None = None
    github_app_private_key: str | None = None
    gitlab_token: str | None = None
    bitbucket_token: str | None = None
    bitbucket_username: str | None = None
    default_repo_url: str | None = Field(
        default=None, description="Default repository URL to use when Jira doesn't provide one"
    )

    # Sandbox/Worktree
    worktree_base_path: str = os.path.join(os.path.expanduser("~"), "LOOP", "worktrees")  # nosec B108
    sandbox_type: str = Field(default="docker", pattern="^(docker|firecracker|none)$")

    # Execution Limits
    execution_timeout_seconds: int = 3600  # 1 hour
    max_token_budget: int = 100000
    max_retry_count: int = 3

    # Security
    webhook_secret: str | None = None
    jwt_secret_key: str = Field(
        default="change-this-in-production-use-a-long-random-string", min_length=32
    )

    # Observability
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    enable_otel: bool = False
    otel_endpoint: str | None = None
    otel_service_name: str = "ai-sdlc-automation"

    # LangSmith Tracing
    langsmith_tracing: bool = True
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: str | None = None
    langsmith_project: str = "flux"

    @field_validator("jira_url")
    @classmethod
    def validate_jira_url(cls, v: str | None) -> str | None:
        """Validate and format the Jira URL."""
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("Jira URL must start with http:// or https://")
        return v.rstrip("/") if v else None

    @property
    def is_production(self) -> bool:
        """Check if environment is set to production."""
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        """Check if environment is set to development."""
        return self.environment == "development"


# Global settings instance
settings = Settings()
