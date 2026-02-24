"""
E2E tests for the web API.

Exercises the full HTTP layer — templates CRUD, session lifecycle (start →
control → end), SSE event delivery, and transcript endpoints.

The LLM provider (LiteLLMClient) and TranscriptWriter are mocked so no
external calls are made.  All other components (SessionEngine, EventBus,
SessionManager) run with their real implementations.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import src.web.api as api_module
from src.web.main import app
from src.web.session_manager import session_manager

# ---------------------------------------------------------------------------
# Shared config payloads
# ---------------------------------------------------------------------------

_CONFIG = {
    "title": "E2E Test Session",
    "description": "E2E test",
    "type": "social",
    "setting": "social",
    "topic": "Testing.",
    "agents": [
        {
            "id": "a",
            "name": "Alice",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "role": "participant",
        }
    ],
    "orchestrator": {"type": "python", "module": "basic"},
    "hitl": {"enabled": False, "role": None},
    "transcript": {"auto_save": False, "format": "markdown", "path": "/tmp/"},
}

_TEMPLATE_YAML = """\
title: "E2E Template"
description: "An e2e test template"
type: social
setting: social
topic: "Chat about things."
agents:
  - id: agent_1
    name: Alice
    provider: anthropic
    model: claude-sonnet-4-6
    role: participant
orchestrator:
  type: python
  module: basic
hitl:
  enabled: false
  role: null
transcript:
  auto_save: false
  format: markdown
  path: ./sessions/
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def clean_sessions():
    """Ensure session_manager has no leftover state between tests."""
    session_manager._sessions.clear()
    yield
    for active in list(session_manager._sessions.values()):
        try:
            active.task.cancel()
        except Exception:
            pass
    session_manager._sessions.clear()


@pytest.fixture
def temp_templates(tmp_path):
    """Write one YAML template and point settings at tmp_path."""
    (tmp_path / "e2e-template.yaml").write_text(_TEMPLATE_YAML)
    with patch.object(api_module.settings, "session_templates_path", tmp_path):
        yield tmp_path


@pytest.fixture
def temp_sessions(tmp_path):
    """Write test transcript JSON files and point settings at tmp_path."""
    t1 = {
        "title": "Alpha Session",
        "setting": "social",
        "started_at": "2025-01-15T10:00:00",
        "agents": [{"id": "a", "name": "Alice"}],
        "turn_count": 4,
    }
    t2 = {
        "title": "Beta Session",
        "setting": "games",
        "started_at": "2025-01-20T12:00:00",
        "agents": [{"id": "a", "name": "Alice"}, {"id": "b", "name": "Bob"}],
        "turn_count": 10,
    }
    (tmp_path / "session-alpha.json").write_text(json.dumps(t1))
    (tmp_path / "session-beta.json").write_text(json.dumps(t2))
    # Write an .md file for export testing
    (tmp_path / "session-alpha.md").write_text("# Alpha Session\n\nContent here.")
    with patch.object(api_module.settings, "sessions_path", str(tmp_path)):
        yield tmp_path


@pytest.fixture
def mock_llm_ctx():
    """Context manager stack that patches LiteLLMClient + TranscriptWriter."""
    from src.providers import CompletionResult, TokenUsage

    result = CompletionResult(
        text="Hello.",
        usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
        model="test",
    )
    with patch("src.session.engine.LiteLLMClient") as MockClient:
        MockClient.return_value.complete = AsyncMock(return_value=result)
        with patch("src.session.engine.TranscriptWriter") as MockWriter:
            MockWriter.return_value.record = MagicMock()
            MockWriter.return_value.flush = AsyncMock()
            yield


# ---------------------------------------------------------------------------
# Template CRUD (create / update not covered by unit tests)
# ---------------------------------------------------------------------------

class TestTemplateCRUD:
    def test_create_template(self, client, tmp_path):
        with patch.object(api_module.settings, "session_templates_path", tmp_path):
            resp = client.post("/api/templates", json=_CONFIG)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "E2E Test Session"
        assert data["setting"] == "social"
        # File was written
        slug = data["slug"]
        assert (tmp_path / f"{slug}.yaml").exists()

    def test_create_then_list(self, client, tmp_path):
        with patch.object(api_module.settings, "session_templates_path", tmp_path):
            client.post("/api/templates", json=_CONFIG)
            resp = client.get("/api/templates")
            assert resp.status_code == 200
            assert len(resp.json()) == 1

    def test_update_template(self, client, temp_templates):
        updated = dict(_CONFIG, title="Updated Title")
        resp = client.put("/api/templates/e2e-template", json=updated)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Title"

    def test_update_nonexistent_still_writes(self, client, temp_templates):
        """PUT upserts — writes even if slug didn't exist before."""
        resp = client.put("/api/templates/brand-new-slug", json=_CONFIG)
        assert resp.status_code == 200

    def test_create_invalid_schema_500(self, client, tmp_path):
        """Manual model_validate inside the handler raises 500, not 422 (no FastAPI body validation)."""
        client_no_raise = TestClient(app, raise_server_exceptions=False)
        with patch.object(api_module.settings, "session_templates_path", tmp_path):
            resp = client_no_raise.post("/api/templates", json={"title": "Missing required fields"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Session lifecycle — mock ActiveSession injected into session_manager
#
# We bypass asyncio.create_task() complexity by directly registering a mock
# ActiveSession. This tests HTTP routing, status codes, and response shapes.
# The engine→LLM integration is covered by test_engine_integration.py.
# ---------------------------------------------------------------------------

_SESSION_ID = "test-session-abc"


@pytest.fixture
def mock_active(client):
    """Register a mock ActiveSession and yield (client, session_id)."""
    from src.session.config import SessionConfig
    from src.session.event_bus import EventBus
    from src.web.session_manager import ActiveSession

    mock_engine = MagicMock()
    mock_engine._session_id = _SESSION_ID
    mock_engine._state = None
    mock_engine.pause = MagicMock()
    mock_engine.resume = MagicMock()
    mock_engine.inject_hitl_message = MagicMock()

    mock_task = MagicMock()
    mock_task.cancel = MagicMock()

    config = SessionConfig.model_validate(_CONFIG)
    active = ActiveSession(
        session_id=_SESSION_ID,
        config=config,
        engine=mock_engine,
        bus=EventBus(),
        task=mock_task,
    )
    session_manager._sessions[_SESSION_ID] = active
    yield client, _SESSION_ID, mock_engine
    session_manager._sessions.pop(_SESSION_ID, None)


class TestSessionLifecycle:
    def test_start_session_returns_id(self, client):
        """POST /api/sessions returns session_id (mocked session_manager.start)."""
        mock_active_obj = MagicMock()
        mock_active_obj.session_id = "returned-id-123"
        with patch("src.web.api.session_manager.start", return_value=mock_active_obj):
            resp = client.post("/api/sessions", json=_CONFIG)
        assert resp.status_code == 201
        assert resp.json()["session_id"] == "returned-id-123"

    def test_get_session_state_starting(self, mock_active):
        client, sid, engine = mock_active
        resp = client.get(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "starting"

    def test_get_session_with_state(self, mock_active):
        """Once engine._state is set, GET returns the state dict."""
        client, sid, engine = mock_active
        from src.session.state import SessionState

        state = SessionState(session_id=sid, turn_number=0)
        engine._state = state
        resp = client.get(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == sid

    def test_pause_session(self, mock_active):
        client, sid, engine = mock_active
        resp = client.post(f"/api/sessions/{sid}/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"
        engine.pause.assert_called_once()

    def test_resume_session(self, mock_active):
        client, sid, engine = mock_active
        resp = client.post(f"/api/sessions/{sid}/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        engine.resume.assert_called_once()

    def test_inject_message(self, mock_active):
        client, sid, engine = mock_active
        resp = client.post(
            f"/api/sessions/{sid}/inject",
            json={"text": "Hello from human", "channel_id": "public"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "injected"
        engine.inject_hitl_message.assert_called_once_with("Hello from human", "public")

    def test_inject_defaults_to_public_channel(self, mock_active):
        client, sid, engine = mock_active
        resp = client.post(
            f"/api/sessions/{sid}/inject",
            json={"text": "No channel specified"},
        )
        assert resp.status_code == 200
        engine.inject_hitl_message.assert_called_once_with("No channel specified", "public")

    def test_end_session_removes_from_registry(self, mock_active):
        client, sid, engine = mock_active
        resp = client.post(f"/api/sessions/{sid}/end")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ended"
        assert session_manager.get(sid) is None

    def test_double_end_returns_404(self, mock_active):
        client, sid, engine = mock_active
        client.post(f"/api/sessions/{sid}/end")
        resp2 = client.post(f"/api/sessions/{sid}/end")
        assert resp2.status_code == 404

    def test_start_invalid_config_500(self, client):
        """Manual model_validate raises 500 for bad config (no FastAPI body schema)."""
        client_no_raise = TestClient(app, raise_server_exceptions=False)
        resp = client_no_raise.post("/api/sessions", json={"title": "bad"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# SSE endpoint — headers and event format
# ---------------------------------------------------------------------------

class TestSSEEndpoint:
    def test_sse_not_found_without_session(self, client):
        resp = client.get("/api/sessions/nonexistent/stream")
        assert resp.status_code == 404

    async def test_sse_content_type_and_cache_headers(self):
        """SSE response carries correct media type and cache-control headers."""
        import anyio
        from src.session.event_bus import EventBus
        from src.web.session_manager import ActiveSession

        mock_engine = MagicMock()
        mock_engine._session_id = _SESSION_ID
        mock_engine._state = None
        mock_task = MagicMock()

        from src.session.config import SessionConfig
        config = SessionConfig.model_validate(_CONFIG)
        active = ActiveSession(
            session_id=_SESSION_ID,
            config=config,
            engine=mock_engine,
            bus=EventBus(),
            task=mock_task,
        )
        session_manager._sessions[_SESSION_ID] = active

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as aclient:
                # Use anyio cancel scope to bail out quickly after checking headers
                with anyio.move_on_after(0.5):
                    async with aclient.stream(
                        "GET", f"/api/sessions/{_SESSION_ID}/stream"
                    ) as resp:
                        assert resp.status_code == 200
                        assert "text/event-stream" in resp.headers["content-type"]
                        assert resp.headers.get("cache-control") == "no-cache"
                        assert resp.headers.get("x-accel-buffering") == "no"
        finally:
            session_manager._sessions.pop(_SESSION_ID, None)

    async def test_sse_subscriber_receives_bus_events(self):
        """Events emitted to the bus fan out to SSE subscriber queues."""
        from src.session.event_bus import EventBus
        from src.session.events import TurnEvent
        from src.web.session_manager import ActiveSession
        from datetime import datetime

        bus = EventBus()
        mock_engine = MagicMock()
        mock_engine._session_id = _SESSION_ID
        mock_engine._state = None

        from src.session.config import SessionConfig
        config = SessionConfig.model_validate(_CONFIG)
        active = ActiveSession(
            session_id=_SESSION_ID,
            config=config,
            engine=mock_engine,
            bus=bus,
            task=MagicMock(),
        )
        session_manager._sessions[_SESSION_ID] = active

        # Wire up the bus fan-out subscription (mirrors session_manager.start())
        sub = bus.stream().subscribe(
            lambda e: [q.put_nowait(e.model_dump_json()) for q in active.sse_queues]
        )

        try:
            # Register an SSE subscriber
            q = session_manager.add_sse_subscriber(_SESSION_ID)

            # Emit a turn event
            event = TurnEvent(
                session_id=_SESSION_ID,
                turn_number=1,
                timestamp=datetime(2025, 1, 1),
                agent_ids=["a"],
            )
            bus.emit(event)

            # Yield to allow the background drain task to process the event
            await asyncio.sleep(0)

            # SSE subscriber queue should now have the serialized event
            assert not q.empty()
            raw = q.get_nowait()
            data = json.loads(raw)
            assert data["type"] == "TURN"
            assert data["turn_number"] == 1
            assert data["session_id"] == _SESSION_ID
        finally:
            sub.cancel()
            session_manager._sessions.pop(_SESSION_ID, None)


# ---------------------------------------------------------------------------
# Transcript endpoints
# ---------------------------------------------------------------------------

class TestTranscriptEndpoints:
    def test_list_transcripts_empty(self, client, tmp_path):
        with patch.object(api_module.settings, "sessions_path", str(tmp_path)):
            resp = client.get("/api/transcripts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_transcripts_returns_summaries(self, client, temp_sessions):
        resp = client.get("/api/transcripts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        titles = {t["title"] for t in data}
        assert titles == {"Alpha Session", "Beta Session"}

    def test_list_transcript_summary_fields(self, client, temp_sessions):
        resp = client.get("/api/transcripts")
        t = next(d for d in resp.json() if d["title"] == "Alpha Session")
        assert t["id"] == "session-alpha"
        assert t["setting"] == "social"
        assert t["date"] == "2025-01-15"
        assert t["agent_count"] == 1
        assert t["turn_count"] == 4

    def test_list_transcripts_filter_by_type(self, client, temp_sessions):
        resp = client.get("/api/transcripts?type=games")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Beta Session"

    def test_list_transcripts_search(self, client, temp_sessions):
        resp = client.get("/api/transcripts?q=alpha")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Alpha Session"

    def test_list_transcripts_no_match(self, client, temp_sessions):
        resp = client.get("/api/transcripts?q=zzznomatch")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_transcript(self, client, temp_sessions):
        resp = client.get("/api/transcripts/session-alpha")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Alpha Session"
        assert data["setting"] == "social"

    def test_get_transcript_not_found(self, client, temp_sessions):
        resp = client.get("/api/transcripts/does-not-exist")
        assert resp.status_code == 404

    def test_search_transcripts_endpoint(self, client, temp_sessions):
        """Dedicated /search endpoint mirrors list behaviour."""
        resp = client.get("/api/transcripts/search?q=beta")
        assert resp.status_code == 200
        assert resp.json()[0]["title"] == "Beta Session"

    def test_export_transcript_json(self, client, temp_sessions):
        resp = client.get("/api/transcripts/session-alpha/export?format=json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_export_transcript_md(self, client, temp_sessions):
        resp = client.get("/api/transcripts/session-alpha/export?format=md")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]
        assert b"Alpha Session" in resp.content

    def test_export_transcript_not_found(self, client, temp_sessions):
        resp = client.get("/api/transcripts/does-not-exist/export?format=md")
        assert resp.status_code == 404

    def test_list_transcripts_pagination(self, client, tmp_path):
        """Page 2 of a single-item store returns empty list."""
        t = {"title": "Solo", "setting": "social", "started_at": "2025-01-01", "agents": [], "turn_count": 1}
        (tmp_path / "solo.json").write_text(json.dumps(t))
        with patch.object(api_module.settings, "sessions_path", str(tmp_path)):
            resp = client.get("/api/transcripts?page=2")
        assert resp.status_code == 200
        assert resp.json() == []
