"""Tests for transcript rendering of newer event types."""

from datetime import UTC, datetime
from pathlib import Path

from src.session.config import AgentConfig, SessionConfig, TranscriptConfig
from src.session.events import HybridAuditEvent
from src.transcript.writer import TranscriptWriter


def _config() -> SessionConfig:
    return SessionConfig(
        title="Transcript Test",
        description="Test transcript rendering",
        type="games",
        setting="game",
        topic="Play a game.",
        agents=[
            AgentConfig(
                id="player_red",
                name="Alex",
                provider="openai",
                model="gpt-4o",
                role="player",
            )
        ],
        transcript=TranscriptConfig(
            auto_save=False,
            format="both",
            path=Path("/tmp/transcript-writer-tests"),
        ),
        game={"name": "Connect Four"},
    )


def test_markdown_renders_hybrid_audit_event():
    writer = TranscriptWriter(_config())
    writer.record(
        HybridAuditEvent(
            timestamp=datetime.now(UTC),
            turn_number=3,
            session_id="s1",
            actor_id="player_red",
            proposed_action={"action_type": "drop_disc", "payload": {"column": 4}},
            diverged=True,
            primary_decision={"accepted": True},
            shadow_decision={"accepted": False},
        )
    )

    markdown = writer._render_markdown()

    assert "[Hybrid audit" in markdown
    assert "player_red" in markdown
    assert "diverged=True" in markdown
