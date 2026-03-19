"""Tests for the provider-facing LLM moderator prompt/response contract."""

import pytest

from src.games import ConnectFourGame
from src.games.contracts import GameAction
from src.games.moderator_protocol import (
    ModeratorTurnRequest,
    build_moderation_messages,
    extract_moderation_payload,
)
from src.providers import CompletionResult
from src.session.config import AgentConfig, GameConfig


def _game():
    return ConnectFourGame()


def _config():
    return GameConfig(plugin="connect_four", name="Connect Four")


def _agents():
    return [
        AgentConfig(
            id="referee",
            name="Referee",
            provider="anthropic",
            model="m",
            role="moderator",
        ),
        AgentConfig(
            id="player_red",
            name="Alex",
            provider="openai",
            model="m",
            role="player",
        ),
        AgentConfig(
            id="player_black",
            name="Sasha",
            provider="google",
            model="m",
            role="player",
        ),
    ]


def _request() -> ModeratorTurnRequest:
    game = _game()
    state = game.initial_state(_config(), _agents())
    return ModeratorTurnRequest(
        game_type=game.game_type,
        actor_id="player_red",
        proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        state=state,
        visible_state=game.visible_state(state, "player_red"),
        legal_actions=game.legal_actions(state, "player_red"),
    )


def test_build_moderation_messages_includes_structured_contract():
    messages = build_moderation_messages(_request())

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "accepted" in messages[0]["content"]
    assert "next_state" in messages[0]["content"]
    assert "public_events" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert '"actor_id": "player_red"' in messages[1]["content"]
    assert '"column": 4' in messages[1]["content"]


def test_extract_moderation_payload_prefers_metadata():
    result = CompletionResult(
        text='{"accepted": false}',
        metadata={"moderation_decision": {"accepted": True, "reason": "ok"}},
    )

    payload = extract_moderation_payload(result)

    assert payload == {"accepted": True, "reason": "ok"}


def test_extract_moderation_payload_falls_back_to_json_text():
    result = CompletionResult(
        text='{"accepted": true, "reason": "parsed from text"}',
    )

    payload = extract_moderation_payload(result)

    assert payload == {"accepted": True, "reason": "parsed from text"}


def test_extract_moderation_payload_rejects_unstructured_completion():
    result = CompletionResult(text="not json")

    with pytest.raises(ValueError, match="structured moderation_decision"):
        extract_moderation_payload(result)
