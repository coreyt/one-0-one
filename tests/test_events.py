"""Tests for smart event models (discriminated union serialization)."""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter

from src.session.events import (
    ChannelCreatedEvent,
    GameStateEvent,
    MessageEvent,
    MonologueEvent,
    RuleViolationEvent,
    SessionEndEvent,
    SessionEvent,
    TurnEvent,
)

NOW = datetime.now(UTC)
TA = TypeAdapter(SessionEvent)


def _round_trip(event) -> SessionEvent:
    """Serialize to dict and back through the discriminated union."""
    return TA.validate_python(event.model_dump())


class TestMessageEvent:
    def test_public_message(self):
        e = MessageEvent(
            timestamp=NOW,
            turn_number=1,
            session_id="s1",
            agent_id="agent_1",
            agent_name="Nova",
            model="anthropic/claude-sonnet-4-6",
            channel_id="public",
            text="Hello world",
        )
        assert e.type == "MESSAGE"
        assert e.recipient_id is None
        assert not e.is_parallel

    def test_private_message(self):
        e = MessageEvent(
            timestamp=NOW,
            turn_number=2,
            session_id="s1",
            agent_id="agent_1",
            agent_name="Nova",
            model="openai/gpt-4o",
            channel_id="private_agent_1_agent_2",
            recipient_id="agent_2",
            text="Psst...",
        )
        assert e.recipient_id == "agent_2"

    def test_round_trip(self):
        e = MessageEvent(
            timestamp=NOW,
            turn_number=1,
            session_id="s1",
            agent_id="a",
            agent_name="Nova",
            model="anthropic/claude-sonnet-4-6",
            channel_id="public",
            text="hi",
        )
        restored = _round_trip(e)
        assert restored.type == "MESSAGE"
        assert restored.text == "hi"  # type: ignore[union-attr]


class TestMonologueEvent:
    def test_monologue(self):
        e = MonologueEvent(
            timestamp=NOW,
            turn_number=1,
            session_id="s1",
            agent_id="a",
            agent_name="Nova",
            text="I should pivot my argument here.",
        )
        assert e.type == "MONOLOGUE"

    def test_round_trip(self):
        e = MonologueEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            agent_id="a", agent_name="Nova", text="thinking..."
        )
        restored = _round_trip(e)
        assert restored.type == "MONOLOGUE"


class TestTurnEvent:
    def test_sequential_turn(self):
        e = TurnEvent(
            timestamp=NOW, turn_number=3, session_id="s1",
            agent_ids=["agent_1"],
        )
        assert not e.is_parallel

    def test_parallel_turn(self):
        e = TurnEvent(
            timestamp=NOW, turn_number=3, session_id="s1",
            agent_ids=["agent_1", "agent_2"],
            is_parallel=True,
        )
        assert e.is_parallel
        assert len(e.agent_ids) == 2

    def test_round_trip(self):
        e = TurnEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            agent_ids=["a"], is_parallel=False,
        )
        restored = _round_trip(e)
        assert restored.type == "TURN"


class TestGameStateEvent:
    def test_game_state(self):
        e = GameStateEvent(
            timestamp=NOW, turn_number=5, session_id="s1",
            updates={"round": 3}, full_state={"round": 3, "scores": {}},
        )
        assert e.updates["round"] == 3

    def test_round_trip(self):
        e = GameStateEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            updates={"k": "v"}, full_state={"k": "v"},
        )
        restored = _round_trip(e)
        assert restored.type == "GAME_STATE"


class TestRuleViolationEvent:
    def test_rule_violation(self):
        e = RuleViolationEvent(
            timestamp=NOW, turn_number=2, session_id="s1",
            agent_id="agent_1",
            rule="Answers must be yes or no only",
            violation_text="It depends on the context...",
        )
        assert e.type == "RULE_VIOLATION"

    def test_round_trip(self):
        e = RuleViolationEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            agent_id="a", rule="r", violation_text="v",
        )
        restored = _round_trip(e)
        assert restored.type == "RULE_VIOLATION"


class TestChannelCreatedEvent:
    def test_public_channel(self):
        e = ChannelCreatedEvent(
            timestamp=NOW, session_id="s1",
            channel_id="public", channel_type="public", members=[],
        )
        assert e.channel_type == "public"

    def test_team_channel(self):
        e = ChannelCreatedEvent(
            timestamp=NOW, session_id="s1",
            channel_id="team_red", channel_type="team",
            members=["agent_1", "agent_2"],
        )
        assert len(e.members) == 2

    def test_round_trip(self):
        e = ChannelCreatedEvent(
            timestamp=NOW, session_id="s1",
            channel_id="public", channel_type="public", members=[],
        )
        restored = _round_trip(e)
        assert restored.type == "CHANNEL_CREATED"


class TestSessionEndEvent:
    def test_all_reason_values(self):
        for reason in ("max_turns", "win_condition", "completion_signal", "user_ended", "error"):
            e = SessionEndEvent(
                timestamp=NOW, turn_number=10, session_id="s1", reason=reason,
            )
            assert e.reason == reason

    def test_round_trip(self):
        e = SessionEndEvent(
            timestamp=NOW, turn_number=5, session_id="s1", reason="max_turns"
        )
        restored = _round_trip(e)
        assert restored.type == "SESSION_END"


class TestDiscriminatedUnion:
    def test_discriminator_routes_correctly(self):
        """The union must pick the right subtype based on `type` field."""
        payloads = [
            {"type": "MESSAGE", "timestamp": NOW.isoformat(), "turn_number": 1,
             "session_id": "s", "agent_id": "a", "agent_name": "N",
             "model": "m", "channel_id": "public", "text": "hi"},
            {"type": "MONOLOGUE", "timestamp": NOW.isoformat(), "turn_number": 1,
             "session_id": "s", "agent_id": "a", "agent_name": "N", "text": "t"},
            {"type": "TURN", "timestamp": NOW.isoformat(), "turn_number": 1,
             "session_id": "s", "agent_ids": ["a"]},
            {"type": "SESSION_END", "timestamp": NOW.isoformat(), "turn_number": 1,
             "session_id": "s", "reason": "max_turns"},
        ]
        expected = ["MESSAGE", "MONOLOGUE", "TURN", "SESSION_END"]
        for payload, exp in zip(payloads, expected):
            event = TA.validate_python(payload)
            assert event.type == exp
