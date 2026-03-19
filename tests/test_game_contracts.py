"""Tests for first-class game engine contracts."""

from src.games import (
    ActionSpec,
    ApplyResult,
    ChannelSpec,
    GameAction,
    GameOutcome,
    GameStateBase,
    TurnContext,
    ValidationResult,
    VisibleGameState,
)


class TestGameContracts:
    def test_base_models_round_trip(self):
        state = GameStateBase(phase="play", round_number=2, turn_index=4)
        visible = VisibleGameState(viewer_id="a1", payload={"board": []})
        action = GameAction(action_type="drop_disc", payload={"column": 4})
        validation = ValidationResult(is_valid=True, normalized_action=action)
        turn = TurnContext(active_actor_ids=["a1"], phase="play", allow_parallel=False)
        channel = ChannelSpec(channel_id="public", channel_type="public")
        spec = ActionSpec(action_type="drop_disc", input_schema={"column": "int"})
        result = ApplyResult(next_state=state, state_delta={"column": 4})
        outcome = GameOutcome(status="win", winners=["a1"], losers=["a2"])

        assert visible.viewer_id == "a1"
        assert validation.normalized_action == action
        assert turn.active_actor_ids == ["a1"]
        assert channel.channel_type == "public"
        assert spec.action_type == "drop_disc"
        assert result.next_state.phase == "play"
        assert outcome.winners == ["a1"]
