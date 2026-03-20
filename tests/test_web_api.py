"""
Tests for the web API — templates and session endpoints.
Uses FastAPI TestClient (sync) for REST endpoints.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.web.api as api_module
from src.web.main import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def temp_templates_dir(tmp_path):
    """Point settings at a temp directory with one YAML template."""
    template_yaml = """
title: "Test Template"
description: "A test template"
type: social
setting: social
topic: "Discuss things."
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
    (tmp_path / "test-template.yaml").write_text(template_yaml)
    # Patch the settings object that api.py already imported
    with patch.object(api_module.settings, "session_templates_path", tmp_path):
        yield tmp_path


class TestTemplateEndpoints:
    def test_list_templates_empty(self, client, tmp_path):
        with patch.object(api_module.settings, "session_templates_path", tmp_path):
            resp = client.get("/api/templates")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_templates_with_file(self, client, temp_templates_dir):
        resp = client.get("/api/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Template"
        assert data[0]["slug"] == "test-template"
        assert data[0]["setting"] == "social"

    def test_get_template(self, client, temp_templates_dir):
        resp = client.get("/api/templates/test-template")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Template"

    def test_get_template_not_found(self, client, temp_templates_dir):
        resp = client.get("/api/templates/nonexistent")
        assert resp.status_code == 404

    def test_filter_by_type(self, client, temp_templates_dir):
        resp = client.get("/api/templates?type=social")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = client.get("/api/templates?type=game")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_search_by_query(self, client, temp_templates_dir):
        resp = client.get("/api/templates?q=test")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = client.get("/api/templates?q=zzznomatch")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_delete_template(self, client, temp_templates_dir):
        resp = client.delete("/api/templates/test-template")
        assert resp.status_code == 204
        resp = client.get("/api/templates/test-template")
        assert resp.status_code == 404

    def test_list_models_returns_cached_models(self, client):
        with patch("src.web.api.load_cached_airlock_model_ids", return_value=["gpt-4o"]):
            with patch("src.web.api.refresh_airlock_model_ids", return_value=["gpt-4o"]):
                def _capture_task(coro):
                    coro.close()
                    return MagicMock()

                with patch("src.web.api.asyncio.create_task", side_effect=_capture_task) as create_task:
                    resp = client.get("/api/models")
        assert resp.status_code == 200
        assert resp.json() == ["gpt-4o"]
        assert create_task.called

    def test_list_models_refreshes_when_cache_empty(self, client):
        with patch("src.web.api.load_cached_airlock_model_ids", return_value=[]):
            with patch("src.web.api.refresh_airlock_model_ids", return_value=["gemini-pro"]):
                resp = client.get("/api/models")
        assert resp.status_code == 200
        assert resp.json() == ["gemini-pro"]


class TestSessionEndpoints:
    """Basic session lifecycle — does not actually run LLMs."""

    _MINIMAL_CONFIG = {
        "title": "Test Session",
        "description": "A test session",
        "type": "social",
        "setting": "social",
        "topic": "Testing.",
        "agents": [
            {
                "id": "agent_1",
                "name": "Alice",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "role": "participant",
            }
        ],
        "orchestrator": {"type": "python", "module": "basic"},
        "hitl": {"enabled": False, "role": None},
        "transcript": {"auto_save": False, "format": "markdown", "path": "./sessions/"},
    }

    def test_start_session_returns_session_id(self, client):
        mock_active = MagicMock()
        mock_active.session_id = "test-session-123"

        with patch("src.web.api.session_manager.start", return_value=mock_active):
            resp = client.post("/api/sessions", json=self._MINIMAL_CONFIG)

        assert resp.status_code == 201
        assert resp.json()["session_id"] == "test-session-123"

    def test_get_session_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent-id")
        assert resp.status_code == 404

    def test_end_session_not_found(self, client):
        resp = client.post("/api/sessions/nonexistent-id/end")
        assert resp.status_code == 404

    def test_pause_session_not_found(self, client):
        resp = client.post("/api/sessions/nonexistent-id/pause")
        assert resp.status_code == 404

    def test_resume_session_not_found(self, client):
        resp = client.post("/api/sessions/nonexistent-id/resume")
        assert resp.status_code == 404

    def test_inject_session_not_found(self, client):
        resp = client.post(
            "/api/sessions/nonexistent-id/inject",
            json={"text": "hello", "channel_id": "public"},
        )
        assert resp.status_code == 404


class TestSseFormat:
    """Verify the SSE response headers and structure."""

    def test_stream_not_found(self, client):
        resp = client.get("/api/sessions/nonexistent/stream")
        assert resp.status_code == 404
