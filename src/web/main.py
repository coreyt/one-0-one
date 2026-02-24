"""
FastAPI application entry point for one-0-one web UI.

Run with:
    uvicorn src.web.main:app --reload

For production:
    uvicorn src.web.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.logging import configure_logging
from src.web.api import router as api_router

configure_logging()


def serve() -> None:
    """Entry point for `one-0-one-web` script."""
    import uvicorn
    uvicorn.run("src.web.main:app", host="0.0.0.0", port=8000)


app = FastAPI(
    title="one-0-one",
    description="Multi-agent LLM conversation sessions",
    version="0.1.0",
)

# CORS — allow Vite dev server during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(api_router)

# Serve built frontend static files if they exist
_frontend_dist = Path(__file__).parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
