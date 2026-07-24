"""
FastAPI server for webhook handling.

Run with: uv run uvicorn src.api.server:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import webhook_router
from src.core.logging.logger import get_logger

logger = get_logger("api_server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    logger.info("Starting webhook API server")
    yield
    logger.info("Shutting down webhook API server")


app = FastAPI(
    title="AI SDLC Webhook API",
    description="Webhook endpoints for Git platform events",
    version="1.0.0",
    lifespan=lifespan,
)

# Include webhook routers
app.include_router(webhook_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "ai-sdlc-webhook-api"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
