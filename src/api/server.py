"""
FastAPI server for webhook handling.

Run with: uv run uvicorn src.api.server:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import webhook_router
from src.api.auth import router as auth_router
from src.core.logging.logger import get_logger

# Import all models to ensure they're registered with Base before database initialization
from src.models import *  # noqa: F401, F403

logger = get_logger("api_server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    logger.info("Starting webhook API server")
    # Initialize database to ensure all tables are created
    from src.core.database.base import initialize_database
    initialize_database()
    yield
    logger.info("Shutting down webhook API server")


app = FastAPI(
    title="AI SDLC Webhook API",
    description="Webhook endpoints for Git platform events",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include webhook routers
app.include_router(webhook_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "ai-sdlc-webhook-api"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
