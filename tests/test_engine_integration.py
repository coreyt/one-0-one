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

from src.games import GameAction, HybridAuditRecord, ModerationDecision
from src.providers import CompletionResult, MonologueSegment, TokenUsage
from src.session.config import (
    AgentConfig,
    ChannelConfig,
    GameConfig,
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


def _make_connect_four_config(*, max_turns: int = 4) -> SessionConfig:
    return SessionConfig(
        title="Connect Four Integration",
        description="Test game session",
        type="games",
        setting="game",
        topic="Play Connect Four.",
        agents=[
            AgentConfig(
                id="referee",
                name="The Referee",
                provider="anthropic",
                model="claude-sonnet-4-6",
                role="moderator",
            ),
            AgentConfig(
                id="player_red",
                name="Alex Mercer",
                provider="openai",
                model="gpt-4o",
                role="player",
            ),
            AgentConfig(
                id="player_black",
                name="Sasha Kim",
                provider="google",
                model="gemini-2.5-flash",
                role="player",
            ),
        ],
        game=GameConfig(plugin="connect_four", name="Connect Four"),
        orchestrator=OrchestratorConfig(type="python", module="turn_based"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=False, format="markdown", path="/tmp/"),
        max_turns=max_turns,
    )


def _make_connect_four_player_only_config(*, max_turns: int = 4) -> SessionConfig:
    return SessionConfig(
        title="Connect Four Player Only",
        description="Test game session without moderator actor",
        type="games",
        setting="game",
        topic="Play Connect Four.",
        agents=[
            AgentConfig(
                id="player_red",
                name="Alex Mercer",
                provider="openai",
                model="gpt-4o",
                role="player",
            ),
            AgentConfig(
                id="player_black",
                name="Sasha Kim",
                provider="google",
                model="gemini-2.5-flash",
                role="player",
            ),
        ],
        game=GameConfig(plugin="connect_four", name="Connect Four"),
        orchestrator=OrchestratorConfig(type="python", module="turn_based"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=False, format="markdown", path="/tmp/"),
        max_turns=max_turns,
    )


def _make_connect_four_llm_moderated_config(*, max_turns: int = 4) -> SessionConfig:
    config = _make_connect_four_config(max_turns=max_turns)
    config.game.moderation.mode = "llm_moderated"  # type: ignore[union-attr]
    config.game.moderation.moderator_agent_id = "referee"  # type: ignore[union-attr]
    return config


def _make_connect_four_hybrid_audit_config(*, max_turns: int = 4) -> SessionConfig:
    config = _make_connect_four_config(max_turns=max_turns)
    config.game.moderation.mode = "hybrid_audit"  # type: ignore[union-attr]
    config.game.moderation.moderator_agent_id = "referee"  # type: ignore[union-attr]
    config.game.moderation.shadow_mode = "deterministic"  # type: ignore[union-attr]
    return config


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


class _RecordingModerationBackend:
    def __init__(self, decision):
        self.decision = decision
        self.calls: list[tuple[str, GameAction]] = []

    def moderate_turn(self, *, actor_id: str, proposed_action: GameAction):
        self.calls.append((actor_id, proposed_action))
        return self.decision

    async def amoderate_turn(self, *, actor_id: str, proposed_action: GameAction):
        return self.moderate_turn(actor_id=actor_id, proposed_action=proposed_action)


class _RaisingModerationBackend:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls: list[tuple[str, GameAction]] = []

    def moderate_turn(self, *, actor_id: str, proposed_action: GameAction):
        self.calls.append((actor_id, proposed_action))
        raise self.exc

    async def amoderate_turn(self, *, actor_id: str, proposed_action: GameAction):
        return self.moderate_turn(actor_id=actor_id, proposed_action=proposed_action)


class _SequenceModerationBackend:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, GameAction]] = []
        self._index = 0

    def moderate_turn(self, *, actor_id: str, proposed_action: GameAction):
        self.calls.append((actor_id, proposed_action))
        outcome = self.outcomes[self._index]
        self._index += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def amoderate_turn(self, *, actor_id: str, proposed_action: GameAction):
        return self.moderate_turn(actor_id=actor_id, proposed_action=proposed_action)


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

    async def test_plugin_game_emits_initial_authoritative_state(self):
        config = _make_connect_four_config(max_turns=1)
        events = await _run_and_collect(config, mock_response="Opening narration.")

        game_state_events = [e for e in events if e.type == "GAME_STATE"]
        assert len(game_state_events) >= 1
        authoritative = game_state_events[0].full_state["custom"]["authoritative_state"]
        assert authoritative["active_player"] == "player_red"
        assert authoritative["players"] == ["player_red", "player_black"]

    async def test_plugin_game_uses_authoritative_turn_actor_instead_of_orchestrator(self):
        config = _make_connect_four_config(max_turns=1)
        events = await _run_and_collect(config, mock_response="Column 4.")

        turn_events = [e for e in events if e.type == "TURN"]
        assert len(turn_events) >= 1
        assert turn_events[0].agent_ids == ["player_red"]

    async def test_plugin_game_without_moderator_starts_with_active_player(self):
        config = _make_connect_four_player_only_config(max_turns=1)
        events = await _run_and_collect(config, mock_response="Column 4.")

        turn_events = [e for e in events if e.type == "TURN"]
        assert len(turn_events) >= 1
        assert turn_events[0].agent_ids == ["player_red"]

    async def test_plugin_game_ignores_premature_orchestrator_end_reason(self):
        from src.orchestrators import OrchestratorOutput

        def bad_orchestrator(_input):
            return OrchestratorOutput(session_end=True, end_reason="win_condition")

        config = _make_connect_four_player_only_config(max_turns=2)
        events = await _run_and_collect(
            config,
            mock_response=["Column 4.", "Column 4."],
            patch_orchestrator=bad_orchestrator,
        )

        turn_events = [e for e in events if e.type == "TURN"]
        assert len(turn_events) >= 1
        end = next(e for e in events if e.type == "SESSION_END")
        assert end.reason == "max_turns"


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

    async def test_native_monologue_result_emits_monologue_event(self):
        config = _make_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)

        native_result = CompletionResult(
            text="Visible reply.",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            model="test-model",
            monologue=[
                MonologueSegment(
                    text="Provider-native reasoning.",
                    source="provider_native",
                )
            ],
        )

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            mi = MockClient.return_value
            mi.complete = AsyncMock(return_value=native_result)

            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                await engine.run()

        mono_events = [e for e in events if e.type == "MONOLOGUE"]
        public_msgs = [e for e in events if e.type == "MESSAGE" and e.channel_id == "public"]
        assert len(mono_events) >= 1
        assert mono_events[0].text == "Provider-native reasoning."
        assert any(e.text == "Visible reply." for e in public_msgs)

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

    async def test_plugin_game_applies_player_action_to_authoritative_state(self):
        config = _make_connect_four_config(max_turns=2)
        events = await _run_and_collect(
            config,
            mock_response=["Opening narration.", "Column 4."],
        )

        game_state_events = [e for e in events if e.type == "GAME_STATE"]
        authoritative_events = [
            e for e in game_state_events
            if "authoritative_delta" in e.updates
        ]
        assert len(authoritative_events) >= 1
        final_state = authoritative_events[-1].full_state["custom"]["authoritative_state"]
        assert final_state["board"][5][3] == "R"
        assert final_state["active_player"] == "player_black"

    async def test_plugin_game_authoritative_win_ends_session(self):
        agents = [
            {"id": "player_red", "name": "Alex Mercer", "provider": "openai", "model": "gpt-4o", "role": "player", "team": "red"},
            {"id": "player_black", "name": "Sasha Kim", "provider": "google", "model": "gemini-2.5-flash", "role": "player", "team": "black"},
        ]
        channels = [
            ChannelConfig(id="red", type="team", members=["player_red"]),
            ChannelConfig(id="black", type="team", members=["player_black"]),
        ]
        config = SessionConfig(
            title="Connect Four Win",
            description="Test authoritative win condition",
            type="games",
            setting="game",
            topic="Play Connect Four.",
            agents=[AgentConfig(**agent) for agent in agents],
            channels=channels,
            game=GameConfig(plugin="connect_four", name="Connect Four"),
            orchestrator=OrchestratorConfig(type="python", module="basic"),
            hitl=HITLConfig(enabled=False),
            transcript=TranscriptConfig(auto_save=False, format="markdown", path="/tmp/"),
            max_turns=20,
        )

        turns = [
            "Column 1.",
            "Column 1.",
            "Column 2.",
            "Column 2.",
            "Column 3.",
            "Column 3.",
            "Column 4.",
        ]

        def plugin_orchestrator(input_):
            active = input_.state.game_state.custom["authoritative_state"]["active_player"]
            if not active:
                from src.orchestrators import OrchestratorOutput
                return OrchestratorOutput(next_agents=[])
            from src.orchestrators import OrchestratorOutput
            return OrchestratorOutput(next_agents=[active], advance_turns=1)

        events = await _run_and_collect(
            config,
            mock_response=turns,
            patch_orchestrator=plugin_orchestrator,
        )

        end_events = [e for e in events if e.type == "SESSION_END"]
        assert len(end_events) == 1
        assert end_events[0].reason == "win_condition"
        last_game_state = [e for e in events if e.type == "GAME_STATE"][-1]
        final_authoritative = last_game_state.full_state["custom"]["authoritative_state"]
        assert final_authoritative["winner"] == "player_red"

    async def test_plugin_game_context_includes_visible_state_and_legal_actions(self):
        config = _make_connect_four_config(max_turns=1)
        bus = EventBus()
        captured_messages: list[list[dict]] = []

        async def capture_complete(**kwargs):
            captured_messages.append(kwargs["messages"])
            return _make_result("Column 4.")

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(side_effect=capture_complete)
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                await engine.run()

        assert len(captured_messages) >= 1
        combined = "\n".join(m["content"] for m in captured_messages[0] if "content" in m)
        assert "active_player" in combined
        assert "player_red" in combined
        assert "drop_disc" in combined
        assert "column" in combined

    async def test_plugin_game_context_uses_authoritative_view_without_legacy_game_state_dump(self):
        config = _make_connect_four_config(max_turns=1)
        bus = EventBus()
        captured_messages: list[list[dict]] = []

        async def capture_complete(**kwargs):
            captured_messages.append(kwargs["messages"])
            return _make_result("Column 4.")

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(side_effect=capture_complete)
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                await engine.run()

        assert len(captured_messages) >= 1
        system_contents = [
            message["content"]
            for message in captured_messages[0]
            if message.get("role") == "system"
        ]
        assert any(content.startswith("[Authoritative game view]") for content in system_contents)
        assert not any(content.startswith("[Game state update]") for content in system_contents)

    async def test_plugin_game_without_moderator_applies_moves_and_can_end(self):
        config = _make_connect_four_player_only_config(max_turns=20)
        turns = [
            "Column 1.",
            "Column 1.",
            "Column 2.",
            "Column 2.",
            "Column 3.",
            "Column 3.",
            "Column 4.",
        ]

        events = await _run_and_collect(config, mock_response=turns)

        end_events = [e for e in events if e.type == "SESSION_END"]
        assert len(end_events) == 1
        assert end_events[0].reason == "win_condition"
        last_game_state = [e for e in events if e.type == "GAME_STATE"][-1]
        final_authoritative = last_game_state.full_state["custom"]["authoritative_state"]
        assert final_authoritative["winner"] == "player_red"

    async def test_plugin_game_deterministic_mode_executes_turn_via_moderation_backend(self):
        config = _make_connect_four_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)

        runtime = engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        result = runtime.game.apply_action(runtime.state, "player_red", action)
        backend = _RecordingModerationBackend(
            ModerationDecision.from_apply_result(
                mode="deterministic",
                action=action,
                result=result,
            )
        )
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.validate_action = MagicMock(side_effect=AssertionError("legacy validate path should not be used"))
        runtime.apply_action = MagicMock(side_effect=AssertionError("legacy apply path should not be used"))
        runtime.moderation_backend = backend

        await engine.run()

        assert backend.calls == [("player_red", action)]
        authoritative_events = [
            e for e in events
            if e.type == "GAME_STATE" and "authoritative_delta" in e.updates
        ]
        assert len(authoritative_events) == 1
        final_state = authoritative_events[-1].full_state["custom"]["authoritative_state"]
        assert final_state["board"][5][3] == "R"
        assert final_state["active_player"] == "player_black"

    async def test_plugin_game_llm_moderated_mode_executes_turn_via_moderation_backend(self):
        config = _make_connect_four_llm_moderated_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_config = _make_connect_four_config(max_turns=1)
        deterministic_engine = SessionEngine(deterministic_config, EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        result = runtime.game.apply_action(runtime.state, "player_red", action)
        backend = _RecordingModerationBackend(
            ModerationDecision.from_apply_result(
                mode="llm_moderated",
                action=action,
                result=result,
            )
        )
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.validate_action = MagicMock(side_effect=AssertionError("legacy validate path should not be used"))
        runtime.apply_action = MagicMock(side_effect=AssertionError("legacy apply path should not be used"))
        runtime.moderation_backend = backend

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    await engine.run()

        assert backend.calls == [("player_red", action)]
        authoritative_events = [
            e for e in events
            if e.type == "GAME_STATE" and "authoritative_delta" in e.updates
        ]
        assert len(authoritative_events) == 1
        assert authoritative_events[-1].full_state["custom"]["authoritative_state"]["board"][5][3] == "R"

    async def test_plugin_game_llm_moderated_mode_runs_without_runtime_patch(self):
        config = _make_connect_four_llm_moderated_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)

        async def complete_side_effect(**kwargs):
            messages = kwargs["messages"]
            if messages and messages[0]["role"] == "system":
                if "authoritative game moderator" in messages[0]["content"]:
                    return CompletionResult(
                        text='{"accepted": true, "reason": "Accepted."}',
                        usage=TokenUsage(prompt_tokens=8, completion_tokens=4),
                        model="moderator-model",
                        metadata={
                            "moderation_decision": {
                                "accepted": True,
                                "reason": "Accepted.",
                            }
                        },
                    )
            return _make_result("Column 4.")

        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(side_effect=complete_side_effect)
            with patch("src.session.engine.TranscriptWriter") as MockWriter:
                MockWriter.return_value.record = MagicMock()
                MockWriter.return_value.flush = AsyncMock()
                engine = SessionEngine(config, bus)
                final_state = await engine.run()

        authoritative_events = [
            e for e in events
            if e.type == "GAME_STATE" and "authoritative_delta" in e.updates
        ]
        assert len(authoritative_events) == 1
        assert final_state.game_state.custom["authoritative_state"]["board"][5][3] == "R"
        assert final_state.end_reason == "max_turns"

    async def test_plugin_game_hybrid_audit_mode_executes_turn_via_primary_backend(self):
        config = _make_connect_four_hybrid_audit_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_config = _make_connect_four_config(max_turns=1)
        deterministic_engine = SessionEngine(deterministic_config, EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        result = runtime.game.apply_action(runtime.state, "player_red", action)
        primary = ModerationDecision.from_apply_result(
            mode="llm_moderated",
            action=action,
            result=result,
        )
        shadow = ModerationDecision(
            accepted=False,
            moderator_mode="deterministic",
            next_state=runtime.state,
            reason="Shadow backend rejected the move.",
        )
        backend = _RecordingModerationBackend(
            HybridAuditRecord(
                primary=primary,
                shadow=shadow,
                diverged=True,
            )
        )
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.validate_action = MagicMock(side_effect=AssertionError("legacy validate path should not be used"))
        runtime.apply_action = MagicMock(side_effect=AssertionError("legacy apply path should not be used"))
        runtime.moderation_backend = backend

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    await engine.run()

        assert backend.calls == [("player_red", action)]
        authoritative_events = [
            e for e in events
            if e.type == "GAME_STATE" and "authoritative_delta" in e.updates
        ]
        assert len(authoritative_events) == 1
        final_state = authoritative_events[-1].full_state["custom"]["authoritative_state"]
        assert final_state["board"][5][3] == "R"

    async def test_plugin_game_hybrid_audit_mode_emits_audit_event_and_persists_record(self):
        config = _make_connect_four_hybrid_audit_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_config = _make_connect_four_config(max_turns=1)
        deterministic_engine = SessionEngine(deterministic_config, EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        result = runtime.game.apply_action(runtime.state, "player_red", action)
        primary = ModerationDecision.from_apply_result(
            mode="llm_moderated",
            action=action,
            result=result,
        )
        shadow = ModerationDecision(
            accepted=False,
            moderator_mode="deterministic",
            next_state=runtime.state,
            reason="Shadow backend rejected the move.",
        )
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.moderation_backend = _RecordingModerationBackend(
            HybridAuditRecord(
                primary=primary,
                shadow=shadow,
                diverged=True,
            )
        )

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    final_state = await engine.run()

        audit_events = [e for e in events if e.type == "HYBRID_AUDIT"]
        assert len(audit_events) == 1
        assert audit_events[0].actor_id == "player_red"
        assert audit_events[0].diverged is True
        assert audit_events[0].proposed_action["payload"]["column"] == 4
        stored_records = final_state.game_state.custom["hybrid_audit_records"]
        assert len(stored_records) == 1
        assert stored_records[0]["actor_id"] == "player_red"
        assert stored_records[0]["diverged"] is True

    async def test_plugin_game_malformed_moderator_payload_becomes_rule_violation(self):
        config = _make_connect_four_llm_moderated_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_engine = SessionEngine(_make_connect_four_config(max_turns=1), EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.moderation_backend = _RaisingModerationBackend(
            ValueError(
                "Malformed moderator payload; expected structured moderation decision with an 'accepted' field."
            )
        )

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    final_state = await engine.run()

        rv_events = [e for e in events if e.type == "RULE_VIOLATION"]
        assert len(rv_events) == 3
        assert "accepted" in rv_events[0].rule
        assert rv_events[0].agent_id == "player_red"
        assert final_state.game_state.custom["authoritative_state"]["board"][5][3] == "."
        assert not any(
            e.type == "GAME_STATE" and "authoritative_delta" in e.updates
            for e in events
        )
        end_event = next(e for e in events if e.type == "SESSION_END")
        assert end_event.reason == "error"

    async def test_plugin_game_unusable_accepted_moderation_becomes_rule_violation(self):
        config = _make_connect_four_llm_moderated_config(max_turns=1)
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_engine = SessionEngine(_make_connect_four_config(max_turns=1), EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.moderation_backend = _RaisingModerationBackend(
            ValueError(
                "Moderator accepted an action that cannot be applied without an explicit next_state: It is not this player's turn."
            )
        )

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    final_state = await engine.run()

        rv_events = [e for e in events if e.type == "RULE_VIOLATION"]
        assert len(rv_events) == 3
        assert "cannot be applied" in rv_events[0].rule
        assert final_state.game_state.custom["authoritative_state"]["active_player"] == "player_red"
        assert not any(
            e.type == "GAME_STATE" and "authoritative_delta" in e.updates
            for e in events
        )
        end_event = next(e for e in events if e.type == "SESSION_END")
        assert end_event.reason == "error"

    async def test_plugin_game_retries_same_actor_with_rule_violation_clarification(self):
        config = _make_connect_four_llm_moderated_config(max_turns=3)
        bus = EventBus()
        events = _setup_event_capture(bus)
        captured_messages: list[list[dict]] = []
        deterministic_engine = SessionEngine(_make_connect_four_config(max_turns=3), EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        result = runtime.game.apply_action(runtime.state, "player_red", action)
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.moderation_backend = _SequenceModerationBackend(
            [
                ValueError("Malformed moderator payload; expected structured moderation decision with an 'accepted' field."),
                ModerationDecision.from_apply_result(
                    mode="llm_moderated",
                    action=action,
                    result=result,
                ),
            ]
        )

        async def capture_complete(**kwargs):
            captured_messages.append(kwargs["messages"])
            return _make_result("Column 4.")

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(side_effect=capture_complete)
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    await engine.run()

        turn_events = [e for e in events if e.type == "TURN"]
        assert len(turn_events) >= 2
        assert turn_events[0].agent_ids == ["player_red"]
        assert turn_events[1].agent_ids == ["player_red"]
        assert len(captured_messages) >= 2
        retry_context = "\n".join(
            msg["content"] for msg in captured_messages[1] if "content" in msg
        )
        assert "[Rule violation]" in retry_context
        authoritative_events = [
            e for e in events
            if e.type == "GAME_STATE" and "authoritative_delta" in e.updates
        ]
        assert len(authoritative_events) == 1
        final_state = authoritative_events[-1].full_state["custom"]["authoritative_state"]
        assert final_state["board"][5][3] == "R"

    async def test_plugin_game_exceeding_retry_cap_ends_session_with_error(self):
        config = _make_connect_four_llm_moderated_config(max_turns=5)
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_engine = SessionEngine(_make_connect_four_config(max_turns=5), EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.moderation_backend = _SequenceModerationBackend(
            [
                ValueError("Malformed moderator payload; expected structured moderation decision with an 'accepted' field."),
                ValueError("Malformed moderator payload; expected structured moderation decision with an 'accepted' field."),
                ValueError("Malformed moderator payload; expected structured moderation decision with an 'accepted' field."),
            ]
        )

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    final_state = await engine.run()

        rv_events = [e for e in events if e.type == "RULE_VIOLATION"]
        assert len(rv_events) == 3
        end_event = next(e for e in events if e.type == "SESSION_END")
        assert end_event.reason == "error"
        assert final_state.end_reason == "error"

    async def test_plugin_game_unparsable_action_becomes_rule_violation_without_advancing_turn(self):
        config = _make_connect_four_llm_moderated_config(max_turns=2)
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_engine = SessionEngine(_make_connect_four_config(max_turns=2), EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        runtime.parse_action_text = MagicMock(return_value=None)

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(
                    side_effect=[
                        _make_result("not a valid move"),
                        _make_result("still not a valid move"),
                        _make_result("yet another invalid move"),
                    ]
                )
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    final_state = await engine.run()

        rv_events = [e for e in events if e.type == "RULE_VIOLATION"]
        assert len(rv_events) == 3
        assert "Could not parse" in rv_events[0].rule
        turn_events = [e for e in events if e.type == "TURN"]
        assert len(turn_events) == 3
        assert all(e.turn_number == 0 for e in turn_events)
        assert final_state.end_reason == "win_condition"
        assert final_state.game_state.custom["authoritative_state"]["winner"] == "player_black"

    async def test_plugin_game_actor_failure_can_skip_turn_on_retry_exhaustion(self):
        config = _make_connect_four_llm_moderated_config(max_turns=1)
        config.game.moderation.failure_policy.actor_retry_exhaustion_action = "skip_turn"  # type: ignore[union-attr]
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_engine = SessionEngine(_make_connect_four_config(max_turns=1), EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.moderation_backend = _SequenceModerationBackend(
            [
                ModerationDecision(
                    accepted=False,
                    moderator_mode="llm_moderated",
                    next_state=runtime.state,
                    reason="Illegal move.",
                ),
                ModerationDecision(
                    accepted=False,
                    moderator_mode="llm_moderated",
                    next_state=runtime.state,
                    reason="Illegal move.",
                ),
                ModerationDecision(
                    accepted=False,
                    moderator_mode="llm_moderated",
                    next_state=runtime.state,
                    reason="Illegal move.",
                ),
            ]
        )

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    final_state = await engine.run()

        assert final_state.end_reason == "max_turns"
        authoritative = final_state.game_state.custom["authoritative_state"]
        assert authoritative["active_player"] == "player_black"
        assert authoritative["board"][5][3] == "."
        resolution = final_state.game_state.custom["moderation_resolutions"][-1]
        assert resolution["policy_action"] == "skip_turn"
        assert resolution["agent_id"] == "player_red"

    async def test_plugin_game_actor_failure_can_forfeit_on_retry_exhaustion(self):
        config = _make_connect_four_llm_moderated_config(max_turns=5)
        config.game.moderation.failure_policy.actor_retry_exhaustion_action = "forfeit"  # type: ignore[union-attr]
        bus = EventBus()
        events = _setup_event_capture(bus)
        deterministic_engine = SessionEngine(_make_connect_four_config(max_turns=5), EventBus())
        runtime = deterministic_engine._game_runtime
        assert runtime is not None
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        runtime.parse_action_text = MagicMock(return_value=action)
        runtime.moderation_backend = _SequenceModerationBackend(
            [
                ModerationDecision(
                    accepted=False,
                    moderator_mode="llm_moderated",
                    next_state=runtime.state,
                    reason="Illegal move.",
                ),
                ModerationDecision(
                    accepted=False,
                    moderator_mode="llm_moderated",
                    next_state=runtime.state,
                    reason="Illegal move.",
                ),
                ModerationDecision(
                    accepted=False,
                    moderator_mode="llm_moderated",
                    next_state=runtime.state,
                    reason="Illegal move.",
                ),
            ]
        )

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(return_value=_make_result("Column 4."))
                with patch("src.session.engine.TranscriptWriter") as MockWriter:
                    MockWriter.return_value.record = MagicMock()
                    MockWriter.return_value.flush = AsyncMock()
                    engine = SessionEngine(config, bus)
                    final_state = await engine.run()

        assert final_state.end_reason == "win_condition"
        end_event = next(e for e in events if e.type == "SESSION_END")
        assert end_event.reason == "win_condition"
        authoritative = final_state.game_state.custom["authoritative_state"]
        assert authoritative["winner"] == "player_black"
        resolution = final_state.game_state.custom["moderation_resolutions"][-1]
        assert resolution["policy_action"] == "forfeit"
        assert resolution["winner"] == "player_black"


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
