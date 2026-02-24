"""Tests for ChannelRouter visibility rules."""

from datetime import UTC, datetime

import pytest

from src.channels.router import ChannelRouter
from src.session.config import (
    AgentConfig,
    ChannelConfig,
    HITLConfig,
    OrchestratorConfig,
    SessionConfig,
    TranscriptConfig,
)
from src.session.events import (
    GameStateEvent,
    MessageEvent,
    MonologueEvent,
    RuleViolationEvent,
    TurnEvent,
)
from src.session.state import AgentState, GameState, SessionState

NOW = datetime.now(UTC)


def _make_config(agents: list[dict], channels: list[dict] = None) -> SessionConfig:
    return SessionConfig.model_validate({
        "title": "Test",
        "description": "Test",
        "type": "social",
        "setting": "social",
        "topic": "Test topic",
        "agents": agents,
        "channels": channels or [],
    })


def _make_state(config: SessionConfig, events=None) -> SessionState:
    return SessionState(
        session_id="s1",
        turn_number=1,
        game_state=GameState(),
        events=events or [],
        agents={a.id: AgentState(config=a) for a in config.agents},
    )


def _msg(channel: str, agent_id: str = "a1", agent_name: str = "Nova",
         text: str = "hi", recipient_id: str = None) -> MessageEvent:
    return MessageEvent(
        timestamp=NOW, turn_number=1, session_id="s1",
        agent_id=agent_id, agent_name=agent_name,
        model="m", channel_id=channel, text=text,
        recipient_id=recipient_id,
    )


class TestPublicVisibility:
    def test_public_visible_to_all(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        event = _msg("public", agent_id="a1")
        state = _make_state(config, events=[event])

        ctx_a1 = router.build_context("a1", state)
        ctx_a2 = router.build_context("a2", state)

        # System prompt is always first; messages follow
        def has_message(ctx, text):
            return any(m.get("content", "").endswith(text) for m in ctx[1:])

        assert has_message(ctx_a1, "hi")
        assert has_message(ctx_a2, "hi")


class TestTeamVisibility:
    def _team_config(self):
        return _make_config(
            agents=[
                {"id": "a1", "name": "Nova", "provider": "p", "model": "m",
                 "role": "r", "team": "team_red"},
                {"id": "a2", "name": "Rex", "provider": "p", "model": "m",
                 "role": "r", "team": "team_red"},
                {"id": "a3", "name": "Sage", "provider": "p", "model": "m", "role": "r"},
            ],
            channels=[
                {"id": "team_red", "type": "team", "members": ["a1", "a2"]},
            ],
        )

    def test_team_message_visible_to_members(self):
        config = self._team_config()
        router = ChannelRouter(config)
        event = _msg("team_red", agent_id="a1", agent_name="Nova")
        state = _make_state(config, events=[event])

        ctx_a2 = router.build_context("a2", state)
        ctx_a3 = router.build_context("a3", state)

        def has_text(ctx, text):
            return any(text in m.get("content", "") for m in ctx[1:])

        assert has_text(ctx_a2, "hi")      # teammate
        assert not has_text(ctx_a3, "hi")  # not on team


class TestPrivateVisibility:
    def test_private_visible_to_sender_and_recipient(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
            {"id": "a3", "name": "Sage", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        event = _msg(
            "private_a1_a2", agent_id="a1", agent_name="Nova",
            text="secret", recipient_id="a2"
        )
        state = _make_state(config, events=[event])

        def has_text(ctx, text):
            return any(text in m.get("content", "") for m in ctx[1:])

        assert has_text(router.build_context("a1", state), "secret")  # sender
        assert has_text(router.build_context("a2", state), "secret")  # recipient
        assert not has_text(router.build_context("a3", state), "secret")  # third party


class TestMonologueExclusion:
    def test_monologue_never_in_context(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        mono = MonologueEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            agent_id="a1", agent_name="Nova", text="INTERNAL THOUGHT",
        )
        state = _make_state(config, events=[mono])

        def has_text(ctx, text):
            return any(text in m.get("content", "") for m in ctx)

        # Neither the speaker nor any other agent should see the monologue
        assert not has_text(router.build_context("a1", state), "INTERNAL THOUGHT")
        assert not has_text(router.build_context("a2", state), "INTERNAL THOUGHT")


class TestSystemMessages:
    def test_game_state_visible_to_all(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        gs = GameStateEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            updates={"score": 5}, full_state={"score": 5},
        )
        state = _make_state(config, events=[gs])

        ctx_a1 = router.build_context("a1", state)
        ctx_a2 = router.build_context("a2", state)

        def has_system(ctx, text):
            return any(
                m["role"] == "system" and text in m.get("content", "")
                for m in ctx[1:]
            )

        assert has_system(ctx_a1, "Game state update")
        assert has_system(ctx_a2, "Game state update")

    def test_rule_violation_only_for_violating_agent(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        rv = RuleViolationEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            agent_id="a1", rule="Yes/No only", violation_text="It depends...",
        )
        state = _make_state(config, events=[rv])

        def has_violation(ctx):
            return any("Rule violation" in m.get("content", "") for m in ctx[1:])

        assert has_violation(router.build_context("a1", state))      # violator sees it
        assert not has_violation(router.build_context("a2", state))  # others don't


class TestSystemPrompt:
    def test_system_prompt_contains_topic(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "researcher"},
        ])
        router = ChannelRouter(config)
        state = _make_state(config)
        ctx = router.build_context("a1", state)

        system = ctx[0]
        assert system["role"] == "system"
        assert "Test topic" in system["content"]

    def test_system_prompt_contains_channel_instructions(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        state = _make_state(config)
        ctx = router.build_context("a1", state)

        system = ctx[0]["content"]
        assert "<thinking>" in system
        assert "<private" in system
        assert "<team>" in system
