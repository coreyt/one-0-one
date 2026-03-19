"""Tests for shared moderation backends and hybrid audit."""

import pytest

from src.games import ConnectFourGame
from src.games.contracts import GameAction
from src.games.moderation import (
    DeterministicModerationBackend,
    HybridAuditBackend,
    HybridAuditRecord,
    LLMModerationBackend,
    ModerationDecision,
    ScriptedModerationBackend,
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


class TestDeterministicModerationBackend:
    def test_applies_valid_action_and_updates_state(self):
        game = _game()
        backend = DeterministicModerationBackend(
            game=game,
            state=game.initial_state(_config(), _agents()),
        )

        decision = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert decision.accepted is True
        assert decision.applied_action is not None
        assert decision.next_state is not None
        assert decision.next_state.board[5][3] == "R"


class TestScriptedModerationBackend:
    def test_returns_scripted_decision_under_same_contract(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())
        scripted = ScriptedModerationBackend(
            decisions=[
                ModerationDecision(
                    accepted=True,
                    moderator_mode="llm_moderated",
                    applied_action=GameAction(
                        action_type="drop_disc",
                        payload={"column": 4},
                    ),
                    next_state=initial_state,
                    reason="Accepted by moderator",
                )
            ]
        )

        decision = scripted.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert decision.accepted is True
        assert decision.moderator_mode == "llm_moderated"


class TestLLMModerationBackend:
    def test_uses_proposed_action_when_moderator_accepts_without_override(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())
        backend = LLMModerationBackend(
            game=game,
            state=initial_state,
            moderator_callable=lambda **_: {"accepted": True},
        )

        decision = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert decision.accepted is True
        assert decision.applied_action == GameAction(
            action_type="drop_disc",
            payload={"column": 4},
        )
        assert decision.next_state is not None
        assert decision.next_state.board[5][3] == "R"
        assert backend.state.board[5][3] == "R"

    def test_keeps_existing_state_when_moderator_rejects_action(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())
        backend = LLMModerationBackend(
            game=game,
            state=initial_state,
            moderator_callable=lambda **_: {
                "accepted": False,
                "reason": "Illegal move according to moderator",
            },
        )

        decision = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert decision.accepted is False
        assert decision.reason == "Illegal move according to moderator"
        assert decision.next_state == initial_state
        assert backend.state == initial_state

    def test_accepts_moderator_supplied_next_state(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())
        moderator_state = initial_state.model_copy(
            update={
                "board": [
                    ["." for _ in range(initial_state.columns)]
                    for _ in range(initial_state.rows)
                ],
                "active_player": "player_black",
                "move_count": 1,
                "turn_index": 1,
                "round_number": 0,
                "last_move": {
                    "player_id": "player_red",
                    "column": 4,
                    "row": 5,
                    "disc": "R",
                },
            }
        )
        moderator_state.board[5][3] = "R"
        backend = LLMModerationBackend(
            game=game,
            state=initial_state,
            moderator_callable=lambda **_: {
                "accepted": True,
                "applied_action": {
                    "action_type": "drop_disc",
                    "payload": {"column": 4},
                },
                "next_state": moderator_state.model_dump(),
                "state_delta": {"move_count": 1, "active_player": "player_black"},
                "public_events": [{"summary": "Moderator accepted the move."}],
            },
        )

        decision = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert decision.accepted is True
        assert decision.next_state == moderator_state
        assert decision.state_delta == {
            "move_count": 1,
            "active_player": "player_black",
        }
        assert backend.state == moderator_state

    def test_normalizes_completion_result_metadata_payload(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())
        backend = LLMModerationBackend(
            game=game,
            state=initial_state,
            moderator_callable=lambda **_: CompletionResult(
                text='{"accepted": true}',
                metadata={
                    "moderation_decision": {
                        "accepted": True,
                        "reason": "Moderator approves the move.",
                    }
                },
            ),
        )

        decision = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert decision.accepted is True
        assert decision.moderator_mode == "llm_moderated"
        assert decision.reason == "Moderator approves the move."
        assert decision.next_state is not None
        assert decision.next_state.board[5][3] == "R"

    def test_rejects_unstructured_moderator_payload(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())
        backend = LLMModerationBackend(
            game=game,
            state=initial_state,
            moderator_callable=lambda **_: {"reason": "missing accepted flag"},
        )

        try:
            backend.moderate_turn(
                actor_id="player_red",
                proposed_action=GameAction(
                    action_type="drop_disc",
                    payload={"column": 4},
                ),
            )
        except ValueError as exc:
            assert "accepted" in str(exc)
        else:
            raise AssertionError("Expected malformed moderator payload to raise ValueError")

    @pytest.mark.asyncio
    async def test_supports_async_moderator_callable(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())

        async def moderator_callable(**_):
            return {"accepted": True}

        backend = LLMModerationBackend(
            game=game,
            state=initial_state,
            moderator_callable=moderator_callable,
        )

        decision = await backend.amoderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert decision.accepted is True
        assert decision.next_state is not None
        assert decision.next_state.board[5][3] == "R"


class TestHybridAuditBackend:
    def test_records_no_divergence_when_primary_matches_shadow(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())
        primary = DeterministicModerationBackend(game=game, state=initial_state)
        shadow = DeterministicModerationBackend(
            game=game,
            state=game.initial_state(_config(), _agents()),
        )
        backend = HybridAuditBackend(primary=primary, shadow=shadow)

        record = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert isinstance(record, HybridAuditRecord)
        assert record.primary.accepted is True
        assert record.shadow is not None
        assert record.diverged is False

    def test_records_divergence_when_primary_differs_from_shadow(self):
        game = _game()
        initial_state = game.initial_state(_config(), _agents())
        primary = ScriptedModerationBackend(
            decisions=[
                ModerationDecision(
                    accepted=False,
                    moderator_mode="llm_moderated",
                    reason="Moderator rejected the move",
                    next_state=initial_state,
                )
            ]
        )
        shadow = DeterministicModerationBackend(
            game=game,
            state=game.initial_state(_config(), _agents()),
        )
        backend = HybridAuditBackend(primary=primary, shadow=shadow)

        record = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert record.primary.accepted is False
        assert record.shadow is not None
        assert record.shadow.accepted is True
        assert record.diverged is True

    def test_resynchronizes_shadow_state_after_divergence(self):
        game = _game()
        primary_state = game.initial_state(_config(), _agents())
        shadow_state = game.initial_state(_config(), _agents())
        primary = DeterministicModerationBackend(game=game, state=primary_state)
        shadow = DeterministicModerationBackend(game=game, state=shadow_state)
        backend = HybridAuditBackend(primary=primary, shadow=shadow)

        first = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )
        assert first.diverged is False

        shadow.state = game.initial_state(_config(), _agents())

        second = backend.moderate_turn(
            actor_id="player_black",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert second.diverged is True
        assert shadow.state == primary.state

        third = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 5}),
        )

        assert third.diverged is False
