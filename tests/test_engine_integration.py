"""
Integration tests for SessionEngine.

Runs the full engine loop (orchestrator → agent calls → events) with a mocked
LiteLLMClient. No real LLM calls are made.

Covers:
    - Full session lifecycle: SESSION_CREATED → turns → SESSION_END
    - All 8 event types emitted in the correct order
    - Public / team / private message routing
    - Monologue event emission
    - GameState updates propagated to bus
    - RuleViolation events from orchestrator
    - pause() / resume() gating the loop
    - inject_hitl_message() appearing in subsequent context
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.providers import CompletionResult, TokenUsage
from src.session.config import (
    AgentConfig,
    ChannelConfig,
    HITLConfig,
    OrchestratorConfig,
    SessionConfig,
    TranscriptConfig,
)
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def _make_config(
    *,
    max_turns: int = 2,
    agents: list[dict] | None = None,
    channels: list[ChannelConfig] | None = None,
    completion_signal: str | None = None,
) -> SessionConfig:
    agents = agents or [
        {"id": "a", "name": "Alice", "provider": "anthropic", "model": "claude-sonnet-4-6", "role": "participant"},
        {"id": "b", "name": "Bob", "provider": "openai", "model": "gpt-4o", "role": "participant"},
    ]
    return SessionConfig(
        title="Integration Test",
        description="Test session",
        type="social",
        setting="social",
        topic="Discuss testing.",
        agents=[AgentConfig(**a) for a in agents],
        channels=channels or [],
        orchestrator=OrchestratorConfig(type="python", module="basic"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=False, format="markdown", path="/tmp/"),
        max_turns=max_turns,
        completion_signal=completion_signal,
    )


def _make_result(text: str = "Hello.") -> CompletionResult:
    return CompletionResult(
        text=text,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
        model="test-model",
    )


def _setup_event_capture(bus: EventBus) -> list:
    """
    Patch bus.emit to synchronously append events to a list.

    This avoids the asyncio drain-task timing issue that arises when using
    bus.stream().subscribe(events.append): subscribe() spawns a background
    asyncio.Task; after engine.run() completes the task may not have drained
    all queued events yet.  Patching emit captures events at the moment of
    emission, before any queue is involved.
    """
    events: list = []
    original_emit = bus.emit

    def capturing_emit(event):
        events.append(event)
        original_emit(event)

    bus.emit = capturing_emit
    return events


# ---------------------------------------------------------------------------
# Primary helper: run engine with mocked LLM + collect events
# ---------------------------------------------------------------------------

async def _run_and_collect(
    config: SessionConfig,
    mock_response: str | list[str] = "Hello.",
    *,
    patch_orchestrator=None,
) -> list:
    """Run the engine with a mocked provider and return all emitted events."""
    bus = EventBus()
    events = _setup_event_capture(bus)

    if isinstance(mock_response, list):
        side_effect = [_make_result(r) for r in mock_response] + [_make_result("Done.")] * 20
        return_value = None
    else:
        side_effect = None
        return_value = _make_result(mock_response)

    orch_ctx = (
        patch("src.session.engine.load_orchestrator", return_value=patch_orchestrator)
        if patch_orchestrator is not None
        else None
    )

    with patch("src.session.engine.LiteLLMClient") as MockClient:
        mi = MockClient.return_value
        if side_effect:
            mi.complete = AsyncMock(side_effect=side_effect)
        else:
            mi.complete = AsyncMock(return_value=return_value)

        with patch("src.session.engine.TranscriptWriter") as MockWriter:
            MockWriter.return_value.record = MagicMock()
            MockWriter.return_value.flush = AsyncMock()

            if orch_ctx:
                with orch_ctx:
                    engine = SessionEngine(config, bus)
                    await engine.run()
            else:
                engine = SessionEngine(config, bus)
                await engine.run()

    return events


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestSessionLifecycle:
    async def test_emits_channel_created_on_start(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(config)
        channel_events = [e for e in events if e.type == "CHANNEL_CREATED"]
        assert len(channel_events) >= 1
        assert any(e.channel_id == "public" for e in channel_events)

    async def test_emits_session_end(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(config)
        end_events = [e for e in events if e.type == "SESSION_END"]
        assert len(end_events) == 1
        assert end_events[0].reason == "max_turns"

    async def test_emits_turn_events(self):
        config = _make_config(max_turns=2)
        events = await _run_and_collect(config)
        turn_events = [e for e in events if e.type == "TURN"]
        assert len(turn_events) >= 1

    async def test_emits_message_events(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(config, mock_response="Hello from agent.")
        msg_events = [e for e in events if e.type == "MESSAGE"]
        assert len(msg_events) >= 1
        assert all(isinstance(e.text, str) for e in msg_events)

    async def test_event_order_channel_before_turn(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(config)
        types = [e.type for e in events]
        first_channel = next(i for i, t in enumerate(types) if t == "CHANNEL_CREATED")
        first_turn = next((i for i, t in enumerate(types) if t == "TURN"), None)
        if first_turn is not None:
            assert first_channel < first_turn

    async def test_session_end_reason_max_turns(self):
        config = _make_config(max_turns=2)
        events = await _run_and_collect(config)
        end = next(e for e in events if e.type == "SESSION_END")
        assert end.reason == "max_turns"

    async def test_turn_number_increments(self):
        config = _make_config(max_turns=2)
        events = await _run_and_collect(config)
        turn_events = [e for e in events if e.type == "TURN"]
        turn_numbers = [e.turn_number for e in turn_events]
        assert turn_numbers == sorted(turn_numbers)


# ---------------------------------------------------------------------------
# Message routing tests
# ---------------------------------------------------------------------------

class TestMessageRouting:
    async def test_plain_text_goes_to_public(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(config, mock_response="Hello world.")
        public_msgs = [e for e in events if e.type == "MESSAGE" and e.channel_id == "public"]
        assert len(public_msgs) >= 1
        assert any("Hello world." in e.text for e in public_msgs)

    async def test_team_tag_routes_to_team_channel(self):
        agents = [
            {
                "id": "a", "name": "Alice", "provider": "anthropic",
                "model": "claude-sonnet-4-6", "role": "participant", "team": "red",
            },
        ]
        channels = [ChannelConfig(id="red", type="team", members=["a"])]
        config = _make_config(max_turns=1, agents=agents, channels=channels)
        events = await _run_and_collect(
            config,
            mock_response="<team>Secret team message.</team> Public part.",
        )
        team_msgs = [e for e in events if e.type == "MESSAGE" and e.channel_id == "red"]
        assert len(team_msgs) >= 1
        assert "Secret team message." in team_msgs[0].text

    async def test_thinking_tag_emits_monologue(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(
            config,
            mock_response="<thinking>My inner thoughts.</thinking>My public reply.",
        )
        mono_events = [e for e in events if e.type == "MONOLOGUE"]
        assert len(mono_events) >= 1
        assert any("My inner thoughts." in e.text for e in mono_events)

    async def test_thinking_not_in_public_messages(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(
            config,
            mock_response="<thinking>Hidden thoughts.</thinking>Visible reply.",
        )
        public_msgs = [e for e in events if e.type == "MESSAGE" and e.channel_id == "public"]
        for msg in public_msgs:
            assert "Hidden thoughts." not in msg.text

    async def test_private_tag_sets_recipient(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(
            config,
            mock_response='<private to="Bob">Just for Bob.</private>',
        )
        priv_msgs = [e for e in events if e.type == "MESSAGE" and e.recipient_id is not None]
        assert len(priv_msgs) >= 1
        assert priv_msgs[0].recipient_id == "b"
        assert "Just for Bob." in priv_msgs[0].text


# ---------------------------------------------------------------------------
# Agent metadata on events
# ---------------------------------------------------------------------------

class TestEventMetadata:
    async def test_message_event_has_agent_name(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(config, mock_response="Hi.")
        msg_events = [e for e in events if e.type == "MESSAGE" and e.agent_id != "system"]
        assert all(e.agent_name != "" for e in msg_events)

    async def test_all_events_have_session_id(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(config)
        assert len(events) > 0, "expected at least one event"
        for e in events:
            assert hasattr(e, "session_id")
            assert e.session_id != ""

    async def test_message_event_has_model(self):
        config = _make_config(max_turns=1)
        events = await _run_and_collect(config, mock_response="Hi.")
        msg_events = [e for e in events if e.type == "MESSAGE"]
        assert len(msg_events) > 0, "expected at least one MESSAGE event"
        for msg in msg_events:
            assert msg.model != ""


# ---------------------------------------------------------------------------
# GameState tests
# ---------------------------------------------------------------------------

class TestGameState:
    async def test_game_state_event_emitted_when_orchestrator_updates(self):
        from src.orchestrators import OrchestratorOutput

        call_count = 0

        def mock_orchestrate(inp):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return OrchestratorOutput(
                    next_agents=["a"],
                    game_state_updates={"score": 10},
                )
            return OrchestratorOutput(session_end=True, end_reason="win_condition")

        config = _make_config(max_turns=5)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.load_orchestrator", return_value=mock_orchestrate):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Hi."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    await engine.run()

        gs_events = [e for e in events if e.type == "GAME_STATE"]
        assert len(gs_events) == 1
        assert gs_events[0].updates == {"score": 10}
        assert gs_events[0].full_state["custom"]["score"] == 10

    async def test_session_end_win_condition(self):
        from src.orchestrators import OrchestratorOutput

        def mock_orchestrate(inp):
            return OrchestratorOutput(session_end=True, end_reason="win_condition")

        config = _make_config(max_turns=5)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.load_orchestrator", return_value=mock_orchestrate):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result())
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    await engine.run()

        end = next(e for e in events if e.type == "SESSION_END")
        assert end.reason == "win_condition"


# ---------------------------------------------------------------------------
# RuleViolation tests
# ---------------------------------------------------------------------------

class TestRuleViolations:
    async def test_rule_violation_event_emitted(self):
        from src.orchestrators import OrchestratorOutput, RuleViolation

        call_count = 0

        def mock_orchestrate(inp):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return OrchestratorOutput(
                    next_agents=["a"],
                    rule_violations=[
                        RuleViolation(
                            agent_id="b",
                            rule="Must answer yes or no",
                            violation_text="Agent gave a long essay",
                        )
                    ],
                )
            return OrchestratorOutput(session_end=True, end_reason="max_turns")

        config = _make_config(max_turns=5)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.load_orchestrator", return_value=mock_orchestrate):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result())
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    await engine.run()

        rv_events = [e for e in events if e.type == "RULE_VIOLATION"]
        assert len(rv_events) == 1
        assert rv_events[0].agent_id == "b"
        assert rv_events[0].rule == "Must answer yes or no"


# ---------------------------------------------------------------------------
# Pause / Resume tests
# ---------------------------------------------------------------------------

class TestPauseResume:
    async def test_pause_delays_next_turn(self):
        """Engine paused immediately — should not advance until resumed."""
        config = _make_config(max_turns=2)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(return_value=_make_result())
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                engine.pause()

                async def _resume_after():
                    await asyncio.sleep(0.05)
                    engine.resume()

                await asyncio.gather(engine.run(), _resume_after())

        end_events = [e for e in events if e.type == "SESSION_END"]
        assert len(end_events) == 1

    async def test_resume_event_is_set_after_resume(self):
        config = _make_config(max_turns=1)
        bus = EventBus()

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(return_value=_make_result())
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                engine.pause()
                assert not engine._resume_event.is_set()
                engine.resume()
                assert engine._resume_event.is_set()


# ---------------------------------------------------------------------------
# inject_hitl_message tests
# ---------------------------------------------------------------------------

class TestHITLInject:
    async def test_injected_message_emitted_to_bus(self):
        config = _make_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(return_value=_make_result())
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                await engine.run()
                engine.inject_hitl_message("Hello from human", "public")

        hitl_msgs = [e for e in events if e.type == "MESSAGE" and e.agent_id == "hitl"]
        assert len(hitl_msgs) == 1
        assert hitl_msgs[0].text == "Hello from human"
        assert hitl_msgs[0].channel_id == "public"

    async def test_injected_message_before_run_is_noop(self):
        """inject_hitl_message before run() (state is None) should not crash."""
        config = _make_config(max_turns=1)
        bus = EventBus()

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(return_value=_make_result())
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                engine.inject_hitl_message("Should be ignored", "public")  # must not raise

    async def test_inject_channel_can_be_team(self):
        config = _make_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(return_value=_make_result())
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                await engine.run()
                engine.inject_hitl_message("Team message", "team_red")

        hitl = next(e for e in events if e.type == "MESSAGE" and e.agent_id == "hitl")
        assert hitl.channel_id == "team_red"


# ---------------------------------------------------------------------------
# Provider error handling
# ---------------------------------------------------------------------------

class TestProviderErrors:
    async def test_provider_error_does_not_crash_session(self):
        """A ProviderError on all agents triggers forced advancement and session ends."""
        from src.providers import ProviderError

        # max_turns=1: session ends once turn_number reaches 1.
        # All LLM calls fail, so the engine retries up to _MAX_EMPTY_ATTEMPTS (3)
        # times before force-advancing the turn counter to prevent an infinite loop.
        config = _make_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(
                side_effect=ProviderError("API down", provider="test", model="test/model")
            )
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                state = await engine.run()  # must not raise

        end_events = [e for e in events if e.type == "SESSION_END"]
        assert len(end_events) == 1

    async def test_provider_errors_recorded_as_incidents(self):
        """Each ProviderError is recorded in game_state.incidents."""
        from src.providers import ProviderError

        config = _make_config(max_turns=2)
        bus = EventBus()

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            # First call fails, subsequent calls succeed
            MockClient.return_value.complete = AsyncMock(
                side_effect=[
                    ProviderError("Timeout", provider="test", model="test/model"),
                    _make_result("hello"),
                    _make_result("world"),
                ]
            )
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                state = await engine.run()

        assert len(state.game_state.incidents) == 1
        inc = state.game_state.incidents[0]
        assert inc["type"] == "timeout"
        assert inc["agent_id"] is not None
        assert "model" in inc
        assert "turn" in inc

    async def test_timeout_turn_does_not_consume_turn_budget(self):
        """A timeout on a single-agent turn should not increment turn_number."""
        from src.providers import ProviderError

        # Single-agent config so a timeout produces no output for the whole turn.
        config = _make_config(
            max_turns=3,
            agents=[{"id": "a", "name": "Alice", "provider": "anthropic",
                     "model": "claude-sonnet-4-6", "role": "participant"}],
        )
        bus = EventBus()
        events = _setup_event_capture(bus)

        # Sequence: timeout, then three successes.
        # The timeout should not advance the counter, so we consume turns 0,1,2 (3 msgs).
        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(
                side_effect=[
                    ProviderError("Timeout", provider="test", model="test/model"),
                    _make_result("msg 1"),
                    _make_result("msg 2"),
                    _make_result("msg 3"),
                ]
            )
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                state = await engine.run()

        public_msgs = [e for e in events if e.type == "MESSAGE" and e.channel_id == "public"]
        # Without the fix this would be 2 (timeout burns one turn).
        # With the fix: turn 0 is retried after timeout → 3 successful turns = 3 messages.
        assert len(public_msgs) == 3
        assert len(state.game_state.incidents) == 1

    async def test_incident_event_emitted_on_provider_error(self):
        """IncidentEvent is emitted to the bus when a provider call fails."""
        from src.providers import ProviderError

        config = _make_config(
            max_turns=2,
            agents=[{"id": "a", "name": "Alice", "provider": "anthropic",
                     "model": "claude-sonnet-4-6", "role": "participant"}],
        )
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(
                side_effect=[
                    ProviderError("Connection timed out", provider="anthropic", model="claude-sonnet-4-6"),
                    _make_result("hello"),
                    _make_result("world"),
                ]
            )
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                await engine.run()

        incident_events = [e for e in events if e.type == "INCIDENT"]
        assert len(incident_events) == 1
        inc = incident_events[0]
        assert inc.agent_id == "a"
        assert inc.agent_name == "Alice"
        assert inc.incident_type == "timeout"
        assert "timed out" in inc.detail.lower()


# ---------------------------------------------------------------------------
# Private channel auto-creation
# ---------------------------------------------------------------------------

class TestPrivateChannelCreation:
    async def test_channel_created_emitted_for_private_message(self):
        """When an agent sends a private message, CHANNEL_CREATED fires for that channel."""
        config = _make_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(
                return_value=_make_result('<private to="Bob">Hey Bob.</private>')
            )
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                await engine.run()

        ch_events = [e for e in events if e.type == "CHANNEL_CREATED"]
        private_ch = [e for e in ch_events if e.channel_type == "private"]
        assert len(private_ch) >= 1
        ch = private_ch[0]
        assert "a" in ch.members or "b" in ch.members
        assert ch.channel_id.startswith("private_")

    async def test_channel_created_only_once_per_private_pair(self):
        """Sending multiple private messages on the same channel only emits CHANNEL_CREATED once."""
        config = _make_config(max_turns=2)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(
                return_value=_make_result('<private to="Bob">Hey.</private>')
            )
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                await engine.run()

        ch_events = [e for e in events if e.type == "CHANNEL_CREATED"]
        private_ch = [e for e in ch_events if e.channel_type == "private"]
        # Regardless of how many turns produced private messages, each unique channel fires once
        channel_ids = [e.channel_id for e in private_ch]
        assert len(channel_ids) == len(set(channel_ids))
