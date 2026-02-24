"""Tests for TTS transcript rendering (src/tts/)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tts.renderer import _collect_bytes_with_retry, _strip_markdown, build_script, render_mp3
from src.tts.voices import assign_voices


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _transcript(agents, events, title="Test") -> dict:
    return {
        "title": title,
        "setting": "game",
        "type": "game",
        "topic": "Test topic",
        "started_at": "2026-02-23T00:00:00Z",
        "agents": agents,
        "events": events,
    }


def _agent(agent_id: str, name: str, role: str = "villager") -> dict:
    return {
        "id": agent_id, "name": name, "role": role,
        "team": None, "provider": "anthropic", "model": "claude-sonnet-4-6",
    }


def _msg(
    agent_id: str,
    agent_name: str,
    text: str,
    channel_id: str = "public",
    turn: int = 0,
) -> dict:
    return {
        "type": "MESSAGE",
        "timestamp": "2026-02-23T00:00:00Z",
        "turn_number": turn,
        "session_id": "test-session",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "model": "anthropic/claude-sonnet-4-6",
        "channel_id": channel_id,
        "recipient_id": None,
        "text": text,
        "is_parallel": False,
    }


def _fake_voice(voice_id: str, name: str, category: str = "premade") -> MagicMock:
    v = MagicMock()
    v.voice_id = voice_id
    v.name = name
    v.category = category
    v.labels = {}
    return v


def _fake_client(fake_voices: list, fake_audio: bytes = b"FAKEMP3") -> MagicMock:
    client = MagicMock()
    # Mock the v2 paginated search API (voices.get_all is legacy v1)
    search_response = MagicMock()
    search_response.voices = fake_voices
    search_response.has_more = False
    search_response.next_page_token = None
    client.voices.search.return_value = search_response
    client.text_to_dialogue.convert.return_value = iter([fake_audio])
    client.text_to_speech.convert.return_value = iter([fake_audio])
    return client


def _transcript_file(tmp_path: Path, agents, events, title="Test") -> Path:
    data = _transcript(agents, events, title)
    p = tmp_path / "session.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# build_script
# ---------------------------------------------------------------------------

class TestBuildScript:
    def test_includes_public_messages(self):
        t = _transcript(
            agents=[_agent("a", "Alice")],
            events=[_msg("a", "Alice", "Hello world")],
        )
        assert build_script(t) == [("a", "Alice", "Hello world")]

    def test_filters_non_public_channels_by_default(self):
        t = _transcript(
            agents=[_agent("a", "Alice")],
            events=[
                _msg("a", "Alice", "Public msg", channel_id="public"),
                _msg("a", "Alice", "Team msg", channel_id="mafia"),
                _msg("a", "Alice", "Private msg", channel_id="private_a_b"),
            ],
        )
        assert build_script(t) == [("a", "Alice", "Public msg")]

    def test_includes_specified_channels(self):
        t = _transcript(
            agents=[_agent("a", "Alice")],
            events=[
                _msg("a", "Alice", "Public msg", channel_id="public"),
                _msg("a", "Alice", "Team msg", channel_id="mafia"),
            ],
        )
        script = build_script(t, channels=["public", "mafia"])
        assert len(script) == 2

    def test_skips_monologue_events(self):
        t = _transcript(
            agents=[_agent("a", "Alice")],
            events=[
                _msg("a", "Alice", "Hello"),
                {"type": "MONOLOGUE", "agent_id": "a", "agent_name": "Alice",
                 "text": "thinking...", "timestamp": "2026-02-23T00:00:00Z",
                 "turn_number": 0, "session_id": "s"},
            ],
        )
        assert len(build_script(t)) == 1

    def test_skips_empty_and_whitespace_text(self):
        t = _transcript(
            agents=[_agent("a", "Alice")],
            events=[
                _msg("a", "Alice", ""),
                _msg("a", "Alice", "   "),
                _msg("a", "Alice", "Hello"),
            ],
        )
        assert len(build_script(t)) == 1

    def test_preserves_turn_order(self):
        t = _transcript(
            agents=[_agent("a", "Alice"), _agent("b", "Bob")],
            events=[
                _msg("a", "Alice", "First", turn=0),
                _msg("b", "Bob", "Second", turn=1),
                _msg("a", "Alice", "Third", turn=2),
            ],
        )
        texts = [s[2] for s in build_script(t)]
        assert texts == ["First", "Second", "Third"]

    def test_skips_non_message_event_types(self):
        t = _transcript(
            agents=[_agent("a", "Alice")],
            events=[
                {"type": "TURN", "turn_number": 0, "session_id": "s",
                 "agent_ids": ["a"], "is_parallel": False, "timestamp": "2026-02-23T00:00:00Z"},
                _msg("a", "Alice", "Hello"),
                {"type": "SESSION_END", "turn_number": 1, "session_id": "s",
                 "reason": "max_turns", "timestamp": "2026-02-23T00:00:00Z"},
            ],
        )
        assert len(build_script(t)) == 1

    def test_empty_transcript_returns_empty_list(self):
        t = _transcript(agents=[], events=[])
        assert build_script(t) == []


# ---------------------------------------------------------------------------
# assign_voices
# ---------------------------------------------------------------------------

class TestAssignVoices:
    def _voices(self, n: int) -> list:
        return [_fake_voice(f"v{i}", f"Voice{i}") for i in range(n)]

    def test_all_agents_get_a_voice(self):
        agents = [_agent("a", "Alice"), _agent("b", "Bob")]
        mapping = assign_voices(agents, self._voices(10), seed=42)
        assert set(mapping.keys()) == {"a", "b"}

    def test_no_duplicate_voices(self):
        agents = [_agent(f"a{i}", f"Agent{i}") for i in range(5)]
        mapping = assign_voices(agents, self._voices(10), seed=42)
        assert len(set(mapping.values())) == 5

    def test_same_seed_same_assignment(self):
        agents = [_agent("a", "Alice"), _agent("b", "Bob"), _agent("c", "Carol")]
        voices = self._voices(10)
        assert assign_voices(agents, voices, seed=42) == assign_voices(agents, voices, seed=42)

    def test_different_seed_different_assignment(self):
        agents = [_agent("a", "Alice"), _agent("b", "Bob"), _agent("c", "Carol")]
        voices = self._voices(10)
        assert assign_voices(agents, voices, seed=1) != assign_voices(agents, voices, seed=9999)

    def test_raises_if_not_enough_voices(self):
        agents = [_agent(f"a{i}", f"Agent{i}") for i in range(5)]
        with pytest.raises(ValueError, match="Not enough voices"):
            assign_voices(agents, self._voices(3), seed=42)

    def test_voice_ids_are_strings(self):
        agents = [_agent("a", "Alice")]
        mapping = assign_voices(agents, self._voices(5), seed=1)
        assert isinstance(mapping["a"], str)


# ---------------------------------------------------------------------------
# render_mp3
# ---------------------------------------------------------------------------

class TestRenderMp3:
    def test_creates_mp3_file(self, tmp_path):
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice"), _agent("b", "Bob")],
            events=[_msg("a", "Alice", "Hello"), _msg("b", "Bob", "Hi")],
        )
        client = _fake_client([_fake_voice(f"v{i}", f"Voice{i}") for i in range(10)])
        with patch("src.tts.renderer._make_client", return_value=client):
            result = render_mp3(transcript, tmp_path / "out.mp3", seed=42)
        assert result == tmp_path / "out.mp3"
        assert result.exists()
        assert result.read_bytes() == b"FAKEMP3"

    def test_default_output_path_beside_transcript(self, tmp_path):
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice")],
            events=[_msg("a", "Alice", "Hello")],
        )
        client = _fake_client([_fake_voice(f"v{i}", f"Voice{i}") for i in range(10)])
        with patch("src.tts.renderer._make_client", return_value=client):
            result = render_mp3(transcript, seed=42)
        assert result.suffix == ".mp3"
        assert result.parent == tmp_path

    def test_uses_dialogue_api_for_small_cast(self, tmp_path):
        """≤10 unique speakers → text_to_dialogue called once."""
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice"), _agent("b", "Bob")],
            events=[_msg("a", "Alice", "Hello"), _msg("b", "Bob", "Hi")],
        )
        client = _fake_client([_fake_voice(f"v{i}", f"Voice{i}") for i in range(10)])
        with patch("src.tts.renderer._make_client", return_value=client):
            render_mp3(transcript, tmp_path / "out.mp3", seed=42)
        client.text_to_dialogue.convert.assert_called_once()
        client.text_to_speech.convert.assert_not_called()

    def test_falls_back_to_per_turn_for_large_cast(self, tmp_path):
        """11 unique speakers → text_to_speech called per message."""
        agents = [_agent(f"a{i}", f"Agent{i}") for i in range(11)]
        events = [_msg(f"a{i}", f"Agent{i}", f"Message {i}") for i in range(11)]
        transcript = _transcript_file(tmp_path, agents, events)
        client = _fake_client([_fake_voice(f"v{i}", f"Voice{i}") for i in range(20)])
        with patch("src.tts.renderer._make_client", return_value=client):
            render_mp3(transcript, tmp_path / "out.mp3", seed=42)
        client.text_to_dialogue.convert.assert_not_called()
        assert client.text_to_speech.convert.call_count == 11

    def test_empty_script_raises(self, tmp_path):
        """No speakable messages → ValueError."""
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice")],
            events=[],
        )
        client = _fake_client([_fake_voice(f"v{i}", f"Voice{i}") for i in range(10)])
        with patch("src.tts.renderer._make_client", return_value=client):
            with pytest.raises(ValueError, match="No speakable messages"):
                render_mp3(transcript, tmp_path / "out.mp3", seed=42)

    def test_only_speaking_agents_get_voices(self, tmp_path):
        """Agents who never speak don't consume a voice slot."""
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice"), _agent("b", "Bob"), _agent("c", "Carol")],
            events=[_msg("a", "Alice", "Hello")],  # only Alice speaks
        )
        # Only 1 voice needed (Alice) — even though 3 agents listed
        client = _fake_client([_fake_voice("v0", "VoiceOnly")])
        with patch("src.tts.renderer._make_client", return_value=client):
            render_mp3(transcript, tmp_path / "out.mp3", seed=42)
        assert (tmp_path / "out.mp3").exists()

    def test_dialogue_inputs_match_script(self, tmp_path):
        """DialogueInput list passed to convert matches the script order."""
        from elevenlabs import DialogueInput
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice"), _agent("b", "Bob")],
            events=[
                _msg("a", "Alice", "First line", turn=0),
                _msg("b", "Bob", "Second line", turn=1),
                _msg("a", "Alice", "Third line", turn=2),
            ],
        )
        client = _fake_client([_fake_voice(f"v{i}", f"Voice{i}") for i in range(10)])
        with patch("src.tts.renderer._make_client", return_value=client):
            render_mp3(transcript, tmp_path / "out.mp3", seed=42)

        call_kwargs = client.text_to_dialogue.convert.call_args.kwargs
        inputs = call_kwargs["inputs"]
        assert len(inputs) == 3
        assert inputs[0].text == "First line"
        assert inputs[1].text == "Second line"
        assert inputs[2].text == "Third line"
        # Alice and Bob get different voices
        assert inputs[0].voice_id == inputs[2].voice_id  # both Alice
        assert inputs[0].voice_id != inputs[1].voice_id  # Alice ≠ Bob

    def test_missing_api_key_raises(self, tmp_path):
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice")],
            events=[_msg("a", "Alice", "Hello")],
        )
        with patch("src.tts.renderer.settings") as mock_settings:
            mock_settings.eleven_labs_api_key = ""
            with pytest.raises(RuntimeError, match="ELEVEN_LABS_API_KEY"):
                render_mp3(transcript, tmp_path / "out.mp3", seed=42)

    def test_batches_dialogue_when_text_exceeds_limit(self, tmp_path):
        """When total text > 5000 chars, text_to_dialogue is called multiple times."""
        # Two agents, each with a message just over 2500 chars → two batches
        long_text = "x" * 2600
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice"), _agent("b", "Bob")],
            events=[
                _msg("a", "Alice", long_text, turn=0),
                _msg("b", "Bob", long_text, turn=1),
            ],
        )
        client = _fake_client([_fake_voice(f"v{i}", f"Voice{i}") for i in range(10)])
        with patch("src.tts.renderer._make_client", return_value=client):
            result = render_mp3(transcript, tmp_path / "out.mp3", seed=42)
        # Should have made two dialogue calls (one per turn, each ~2600 chars)
        assert client.text_to_dialogue.convert.call_count == 2
        assert result.exists()

    def test_single_turn_over_limit_is_truncated(self, tmp_path):
        """A single turn exceeding 5000 chars is truncated to fit."""
        huge_text = "y" * 6000
        transcript = _transcript_file(
            tmp_path,
            agents=[_agent("a", "Alice")],
            events=[_msg("a", "Alice", huge_text)],
        )
        client = _fake_client([_fake_voice(f"v{i}", f"Voice{i}") for i in range(10)])
        with patch("src.tts.renderer._make_client", return_value=client):
            render_mp3(transcript, tmp_path / "out.mp3", seed=42)
        call_kwargs = client.text_to_dialogue.convert.call_args.kwargs
        assert len(call_kwargs["inputs"][0].text) == 5000


# ---------------------------------------------------------------------------
# _strip_markdown
# ---------------------------------------------------------------------------

class TestStripMarkdown:
    def test_removes_atx_headers(self):
        assert _strip_markdown("# Heading\ntext") == "Heading\ntext"
        assert _strip_markdown("## Sub\ntext") == "Sub\ntext"

    def test_removes_bold_and_italic(self):
        assert _strip_markdown("**bold** and *italic*") == "bold and italic"
        assert _strip_markdown("***both***") == "both"

    def test_removes_table_rows(self):
        text = "| Name | Status |\n|------|--------|\n| Alice | Alive |"
        result = _strip_markdown(text)
        assert "|" not in result

    def test_removes_horizontal_rules(self):
        assert _strip_markdown("above\n---\nbelow") == "above\n\nbelow"

    def test_plain_text_unchanged(self):
        plain = "Hello, my name is Alice."
        assert _strip_markdown(plain) == plain

    def test_strips_trailing_whitespace(self):
        assert _strip_markdown("  hello  ") == "hello"

    def test_collapses_multiple_blank_lines(self):
        result = _strip_markdown("a\n\n\n\nb")
        assert "\n\n\n" not in result


class TestCollectBytesWithRetry:
    """
    Tests for _collect_bytes_with_retry.

    ElevenLabs convert() is a generator — the HTTP request fires during iteration,
    not during the initial call.  We simulate this by having fn() return a generator
    that raises ApiError on first iteration.
    """

    def _ok_gen(self, *chunks: bytes):
        """Return a function that yields the given byte chunks."""
        def fn(*args, **kwargs):
            yield from chunks
        return fn

    def _error_gen(self, exc):
        """Return a function that returns a generator which raises on first next()."""
        def fn(*args, **kwargs):
            raise exc
            yield  # make it a generator  # noqa: unreachable
        return fn

    def test_success_returns_concatenated_bytes(self):
        fn = self._ok_gen(b"hello", b" world")
        result = _collect_bytes_with_retry(fn)
        assert result == b"hello world"

    def test_retries_on_500_error_during_iteration(self):
        from elevenlabs.core.api_error import ApiError
        err = ApiError(status_code=500, headers={}, body="down")
        calls = []

        def fn(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise err
            yield b"audio"

        with patch("src.tts.renderer.time.sleep"):
            result = _collect_bytes_with_retry(fn)
        assert result == b"audio"
        assert len(calls) == 3

    def test_retries_on_429_rate_limit(self):
        from elevenlabs.core.api_error import ApiError
        err = ApiError(status_code=429, headers={}, body="rate limited")
        calls = []

        def fn(*args, **kwargs):
            calls.append(1)
            if len(calls) < 2:
                raise err
            yield b"ok"

        with patch("src.tts.renderer.time.sleep"):
            result = _collect_bytes_with_retry(fn)
        assert result == b"ok"
        assert len(calls) == 2

    def test_raises_after_all_retries_exhausted(self):
        from elevenlabs.core.api_error import ApiError
        from src.tts.renderer import _RETRY_WAITS
        err = ApiError(status_code=503, headers={}, body="unavailable")
        fn = self._error_gen(err)
        with patch("src.tts.renderer.time.sleep"):
            with pytest.raises(ApiError) as exc_info:
                _collect_bytes_with_retry(fn)
        assert exc_info.value.status_code == 503
        # Attempted once per wait + the final raise = len(_RETRY_WAITS) calls
        # (last attempt raises immediately since attempt == len-1)

    def test_does_not_retry_on_4xx(self):
        from elevenlabs.core.api_error import ApiError
        err = ApiError(status_code=422, headers={}, body="text too long")
        calls = []

        def fn(*args, **kwargs):
            calls.append(1)
            raise err
            yield  # noqa: unreachable

        with pytest.raises(ApiError):
            _collect_bytes_with_retry(fn)
        assert len(calls) == 1  # no retry

    def test_sleep_duration_follows_retry_waits(self):
        from elevenlabs.core.api_error import ApiError
        from src.tts.renderer import _RETRY_WAITS
        err = ApiError(status_code=500, headers={}, body="down")
        calls = []

        def fn(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise err
            yield b"done"

        sleep_calls = []
        with patch("src.tts.renderer.time.sleep", side_effect=sleep_calls.append):
            _collect_bytes_with_retry(fn)
        # Two failures → two sleeps, using the first two wait values
        assert sleep_calls == list(_RETRY_WAITS[:2])

    def test_ignores_non_bytes_chunks(self):
        """Chunks that are not bytes instances should be filtered out."""
        def fn(*args, **kwargs):
            yield b"real"
            yield "string_not_bytes"  # type: ignore[misc]
            yield b"_audio"
        result = _collect_bytes_with_retry(fn)
        assert result == b"real_audio"
