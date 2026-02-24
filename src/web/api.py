"""
FastAPI router — all REST + SSE endpoints for one-0-one.

Endpoints:
    Templates:  GET/POST/PUT/DELETE /api/templates
    Sessions:   POST/GET/pause/resume/inject/end /api/sessions
    Stream:     GET /api/sessions/{id}/stream  (SSE)
    Transcripts: GET /api/transcripts (+ search, detail, export)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.logging import get_logger
from src.session.config import SessionConfig, load_session_config
from src.settings import settings
from src.web.session_manager import session_manager

log = get_logger(__name__)
router = APIRouter(prefix="/api")


# ──────────────────────────────────────────────────────────────────────────────
# Response models
# ──────────────────────────────────────────────────────────────────────────────

class TemplateSummary(BaseModel):
    slug: str
    title: str
    description: str | None = None
    setting: str | None = None
    agent_count: int = 0
    hitl_enabled: bool = False


class SessionStarted(BaseModel):
    session_id: str


class InjectBody(BaseModel):
    text: str
    channel_id: str = "public"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _templates_dir() -> Path:
    return Path(settings.session_templates_path)


def _slug_path(slug: str) -> Path:
    return _templates_dir() / f"{slug}.yaml"


def _config_to_summary(slug: str, config: SessionConfig) -> TemplateSummary:
    return TemplateSummary(
        slug=slug,
        title=config.title,
        description=config.description,
        setting=config.setting,
        agent_count=len(config.agents),
        hitl_enabled=config.hitl.enabled if config.hitl else False,
    )


def _sessions_dir() -> Path:
    p = Path(settings.sessions_path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ──────────────────────────────────────────────────────────────────────────────
# Templates
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/templates", response_model=list[TemplateSummary])
async def list_templates(type: str | None = None, q: str | None = None):
    templates_dir = _templates_dir()
    results: list[TemplateSummary] = []
    if not templates_dir.exists():
        return results
    for path in sorted(templates_dir.glob("*.yaml")):
        try:
            config = load_session_config(path)
            slug = path.stem
            if type and (config.setting or "").lower() != type.lower():
                continue
            if q:
                search = q.lower()
                if search not in config.title.lower() and search not in (config.description or "").lower():
                    continue
            results.append(_config_to_summary(slug, config))
        except Exception:
            pass
    return results


@router.get("/templates/{slug}")
async def get_template(slug: str) -> dict:
    path = _slug_path(slug)
    if not path.exists():
        raise HTTPException(404, detail=f"Template '{slug}' not found")
    config = load_session_config(path)
    return config.model_dump()


@router.post("/templates", response_model=TemplateSummary, status_code=201)
async def create_template(config_data: dict) -> TemplateSummary:
    config = SessionConfig.model_validate(config_data)
    slug = config.title.lower().replace(" ", "-")[:40]
    path = _slug_path(slug)
    _templates_dir().mkdir(parents=True, exist_ok=True)
    import yaml
    path.write_text(yaml.dump(config.model_dump(mode="json", exclude_none=True), allow_unicode=True))
    return _config_to_summary(slug, config)


@router.put("/templates/{slug}", response_model=TemplateSummary)
async def update_template(slug: str, config_data: dict) -> TemplateSummary:
    config = SessionConfig.model_validate(config_data)
    path = _slug_path(slug)
    import yaml
    path.write_text(yaml.dump(config.model_dump(mode="json", exclude_none=True), allow_unicode=True))
    return _config_to_summary(slug, config)


@router.delete("/templates/{slug}", status_code=204)
async def delete_template(slug: str) -> None:
    path = _slug_path(slug)
    if not path.exists():
        raise HTTPException(404, detail=f"Template '{slug}' not found")
    path.unlink()


# ──────────────────────────────────────────────────────────────────────────────
# Sessions
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/sessions", response_model=SessionStarted, status_code=201)
async def start_session(config_data: dict) -> SessionStarted:
    config = SessionConfig.model_validate(config_data)
    active = session_manager.start(config)
    return SessionStarted(session_id=active.session_id)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    active = session_manager.get(session_id)
    if active is None:
        raise HTTPException(404, detail=f"Session '{session_id}' not found")
    state = active.engine._state
    if state is None:
        return {"session_id": session_id, "status": "starting"}
    return state.model_dump()


@router.post("/sessions/{session_id}/pause", status_code=200)
async def pause_session(session_id: str) -> dict:
    active = session_manager.get(session_id)
    if active is None:
        raise HTTPException(404)
    active.engine.pause()
    return {"status": "paused"}


@router.post("/sessions/{session_id}/resume", status_code=200)
async def resume_session(session_id: str) -> dict:
    active = session_manager.get(session_id)
    if active is None:
        raise HTTPException(404)
    active.engine.resume()
    return {"status": "running"}


@router.post("/sessions/{session_id}/inject", status_code=200)
async def inject_message(session_id: str, body: InjectBody) -> dict:
    active = session_manager.get(session_id)
    if active is None:
        raise HTTPException(404)
    active.engine.inject_hitl_message(body.text, body.channel_id)
    return {"status": "injected"}


@router.post("/sessions/{session_id}/end", status_code=200)
async def end_session(session_id: str) -> dict:
    if session_manager.get(session_id) is None:
        raise HTTPException(404)
    session_manager.end(session_id)
    return {"status": "ended"}


# ──────────────────────────────────────────────────────────────────────────────
# SSE Stream
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/stream")
async def session_stream(session_id: str, request: Request) -> StreamingResponse:
    active = session_manager.get(session_id)
    if active is None:
        raise HTTPException(404, detail=f"Session '{session_id}' not found")

    queue = session_manager.add_sse_subscriber(session_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event_json = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {event_json}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            session_manager.remove_sse_subscriber(session_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Transcripts
# ──────────────────────────────────────────────────────────────────────────────

class TranscriptSummary(BaseModel):
    id: str
    title: str
    setting: str | None = None
    date: str
    agent_count: int = 0
    turn_count: int = 0


@router.get("/transcripts", response_model=list[TranscriptSummary])
async def list_transcripts(
    q: str | None = None,
    type: str | None = None,
    page: int = 1,
) -> list[TranscriptSummary]:
    sessions_dir = _sessions_dir()
    results: list[TranscriptSummary] = []
    for path in sorted(sessions_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text())
            transcript_id = path.stem
            title = data.get("title", transcript_id)
            setting = data.get("setting")
            if type and setting and setting.lower() != type.lower():
                continue
            if q:
                search = q.lower()
                if search not in title.lower():
                    continue
            results.append(TranscriptSummary(
                id=transcript_id,
                title=title,
                setting=setting,
                date=data.get("started_at", "")[:10],
                agent_count=len(data.get("agents", [])),
                turn_count=data.get("turn_count", 0),
            ))
        except Exception:
            pass
    # Simple pagination (50 per page)
    start = (page - 1) * 50
    return results[start: start + 50]


@router.get("/transcripts/search", response_model=list[TranscriptSummary])
async def search_transcripts(q: str = "", type: str | None = None) -> list[TranscriptSummary]:
    return await list_transcripts(q=q, type=type)


@router.get("/transcripts/{transcript_id}")
async def get_transcript(transcript_id: str) -> dict:
    path = _sessions_dir() / f"{transcript_id}.json"
    if not path.exists():
        raise HTTPException(404, detail=f"Transcript '{transcript_id}' not found")
    return json.loads(path.read_text())


@router.get("/transcripts/{transcript_id}/export")
async def export_transcript(transcript_id: str, format: str = "md") -> FileResponse:
    sessions_dir = _sessions_dir()
    ext = "md" if format == "md" else "json"
    path = sessions_dir / f"{transcript_id}.{ext}"
    if not path.exists():
        raise HTTPException(404, detail=f"Transcript export not found")
    return FileResponse(
        path=path,
        filename=f"{transcript_id}.{ext}",
        media_type="text/markdown" if ext == "md" else "application/json",
    )
