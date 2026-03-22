"""
Integration tests for feeling-tag processing across the event/TTS pipeline.

Covers:
  - MessageEvent new fields (tts_text, tts_voice_settings)
  - SessionTTSStreamer.enqueue_message tts_text / voice_settings passthrough
  - build_script() uses tts_text from transcript events when available
  - router._build_system_prompt includes feeling instructions
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.session.events import MessageEvent


# ---------------------------------------------------------------------------
# MessageEvent: new TTS fields
# ---------------------------------------------------------------------------


def _make_event(**kwargs) -> MessageEvent:
    defaults = dict(
        timestamp=datetime.now(UTC),
        turn_number=1,
        session_id="s",
        agent_id="a",
        agent_name="Alice",
        model="gpt-4",
        channel_id="public",
        text="hello",
    )
    defaults.update(kwargs)
    return MessageEvent(**defaults)


def test_message_event_has_tts_text_field():
    ev = _make_event()
    assert hasattr(ev, "tts_text")


def test_message_event_tts_text_defaults_to_none():
    ev = _make_event()
    assert ev.tts_text is None


def test_message_event_has_tts_voice_settings_field():
    ev = _make_event()
    assert hasattr(ev, "tts_voice_settings")


def test_message_event_tts_voice_settings_defaults_empty():
    ev = _make_event()
    assert ev.tts_voice_settings == {}


def test_message_event_accepts_tts_text():
    ev = _make_event(tts_text="[angry] Clean text.")
    assert ev.tts_text == "[angry] Clean text."


def test_message_event_accepts_tts_voice_settings():
    ev = _make_event(tts_voice_settings={"stability": 0.3, "style": 0.7})
    assert ev.tts_voice_settings == {"stability": 0.3, "style": 0.7}


def test_message_event_serialises_tts_fields():
    ev = _make_event(tts_text="[angry] text", tts_voice_settings={"stability": 0.3})
    d = ev.model_dump()
    assert d["tts_text"] == "[angry] text"
    assert d["tts_voice_settings"] == {"stability": 0.3}


# ---------------------------------------------------------------------------
# SessionTTSStreamer.enqueue_message — tts_text / voice_settings passthrough
# ---------------------------------------------------------------------------


def test_streamer_enqueue_prefers_tts_text_over_text():
    from src.tts.streamer import SessionTTSStreamer

    streamer = SessionTTSStreamer({"agent_a": "voice123"})
    streamer.enqueue_message("agent_a", "clean text", tts_text="[angry] annotated")
    item = streamer._work_queue.get_nowait()
    agent_id, sent_text, _ = item
    assert sent_text == "[angry] annotated"


def test_streamer_enqueue_falls_back_to_text_when_no_tts_text():
    from src.tts.streamer import SessionTTSStreamer

    streamer = SessionTTSStreamer({"agent_a": "voice123"})
    streamer.enqueue_message("agent_a", "clean text", tts_text=None)
    item = streamer._work_queue.get_nowait()
    _, sent_text, _ = item
    assert sent_text == "clean text"


def test_streamer_enqueue_passes_voice_settings():
    from src.tts.streamer import SessionTTSStreamer

    streamer = SessionTTSStreamer({"agent_a": "voice123"})
    streamer.enqueue_message(
        "agent_a", "text", tts_text=None, voice_settings={"stability": 0.25}
    )
    item = streamer._work_queue.get_nowait()
    _, _, vs = item
    assert vs == {"stability": 0.25}


def test_streamer_enqueue_default_voice_settings_empty():
    from src.tts.streamer import SessionTTSStreamer

    streamer = SessionTTSStreamer({"agent_a": "voice123"})
    streamer.enqueue_message("agent_a", "text")
    _, _, vs = streamer._work_queue.get_nowait()
    assert vs == {}


def test_streamer_enqueue_skips_agent_not_in_voice_map():
    from src.tts.streamer import SessionTTSStreamer

    streamer = SessionTTSStreamer({"agent_a": "voice123"})
    streamer.enqueue_message("agent_b", "text")
    assert streamer._work_queue.empty()


# ---------------------------------------------------------------------------
# build_script — uses tts_text from transcript events when present
# ---------------------------------------------------------------------------


def test_build_script_uses_tts_text_when_present():
    from src.tts.renderer import build_script

    transcript = {
        "events": [
            {
                "type": "MESSAGE",
                "channel_id": "public",
                "agent_id": "a1",
                "agent_name": "Alice",
                "text": "clean text",
                "tts_text": "[angry] annotated text",
            }
        ]
    }
    script = build_script(transcript)
    _, _, text = script[0]
    assert text == "[angry] annotated text"


def test_build_script_falls_back_to_text_when_no_tts_text():
    from src.tts.renderer import build_script

    transcript = {
        "events": [
            {
                "type": "MESSAGE",
                "channel_id": "public",
                "agent_id": "a1",
                "agent_name": "Alice",
                "text": "clean text",
            }
        ]
    }
    script = build_script(transcript)
    _, _, text = script[0]
    assert text == "clean text"


def test_build_script_falls_back_when_tts_text_is_none():
    from src.tts.renderer import build_script

    transcript = {
        "events": [
            {
                "type": "MESSAGE",
                "channel_id": "public",
                "agent_id": "a1",
                "agent_name": "Alice",
                "text": "clean text",
                "tts_text": None,
            }
        ]
    }
    script = build_script(transcript)
    _, _, text = script[0]
    assert text == "clean text"


# ---------------------------------------------------------------------------
# System prompt includes feeling instructions
# ---------------------------------------------------------------------------


def test_system_prompt_includes_feeling_instructions():
    from src.channels.router import _build_system_prompt
    from src.session.config import AgentConfig, SessionConfig, OrchestratorConfig

    agent = AgentConfig(
        id="a1",
        name="Alice",
        provider="openai",
        model="gpt-4o",
        role="participant",
    )
    config = SessionConfig(
        title="Test",
        description="test",
        type="social",
        setting="test",
        topic="test topic",
        agents=[agent],
    )
    prompt = _build_system_prompt(agent, config)
    assert "<feeling>" in prompt
