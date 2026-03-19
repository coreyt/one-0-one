"""Tests for the game runtime wrapper."""

from src.games import (
    DeterministicModerationBackend,
    GameRuntime,
    GameAction,
    HybridAuditBackend,
    ModerationDecision,
    ScriptedModerationBackend,
)
from src.session.config import AgentConfig, GameConfig, SessionConfig


def _session_config() -> SessionConfig:
    return SessionConfig(
        title="Connect Four Runtime",
        description="Test",
        type="games",
        setting="game",
        topic="Play Connect Four",
        game=GameConfig(plugin="connect_four", name="Connect Four"),
        agents=[
            AgentConfig(
                id="referee",
                name="The Referee",
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
        ],
    )


def _llm_moderated_session_config() -> SessionConfig:
    config = _session_config()
    config.game.moderation.mode = "llm_moderated"  # type: ignore[union-attr]
    config.game.moderation.moderator_agent_id = "referee"  # type: ignore[union-attr]
    return config


def _hybrid_audit_session_config() -> SessionConfig:
    config = _session_config()
    config.game.moderation.mode = "hybrid_audit"  # type: ignore[union-attr]
    config.game.moderation.moderator_agent_id = "referee"  # type: ignore[union-attr]
    config.game.moderation.shadow_mode = "deterministic"  # type: ignore[union-attr]
    return config


def _battleship_session_config() -> SessionConfig:
    return SessionConfig(
        title="Battleship Runtime",
        description="Test",
        type="games",
        setting="game",
        topic="Play Battleship",
        game=GameConfig(plugin="battleship", name="Battleship"),
        agents=[
            AgentConfig(
                id="admiral",
                name="The Admiral",
                provider="anthropic",
                model="m",
                role="moderator",
            ),
            AgentConfig(
                id="captain_alpha",
                name="Commander Hayes",
                provider="openai",
                model="m",
                role="player",
            ),
            AgentConfig(
                id="captain_beta",
                name="Captain Voss",
                provider="google",
                model="m",
                role="player",
            ),
        ],
    )


class TestGameRuntime:
    def test_from_session_config_initializes_connect_four(self):
        runtime = GameRuntime.from_session_config(_session_config())

        assert runtime.turn_context().active_actor_ids == ["player_red"]
        assert runtime.visible_state("player_red").payload["active_player"] == "player_red"

    def test_apply_action_updates_runtime_state(self):
        runtime = GameRuntime.from_session_config(_session_config())
        result = runtime.apply_action(
            "player_red",
            GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert result.next_state.active_player == "player_black"
        assert runtime.state.active_player == "player_black"

    def test_parse_action_text_uses_game_parser_when_available(self):
        runtime = GameRuntime.from_session_config(_session_config())
        action = runtime.parse_action_text("Column 5. Let's do this.")

        assert action is not None
        assert action.payload["column"] == 5

    def test_runtime_state_can_seed_deterministic_backend(self):
        runtime = GameRuntime.from_session_config(_session_config())
        backend = DeterministicModerationBackend(
            game=runtime.game,
            state=runtime.state,
        )

        decision = backend.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert decision.accepted is True
        assert decision.next_state is not None

    def test_runtime_can_participate_in_hybrid_audit_shape(self):
        runtime = GameRuntime.from_session_config(_session_config())
        primary = DeterministicModerationBackend(
            game=runtime.game,
            state=runtime.state,
        )
        shadow_runtime = GameRuntime.from_session_config(_session_config())
        shadow = DeterministicModerationBackend(
            game=shadow_runtime.game,
            state=shadow_runtime.state,
        )
        audit = HybridAuditBackend(primary=primary, shadow=shadow)

        record = audit.moderate_turn(
            actor_id="player_red",
            proposed_action=GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert record.diverged is False

    def test_runtime_selects_deterministic_backend_by_default(self):
        runtime = GameRuntime.from_session_config(_session_config())
        assert isinstance(runtime.moderation_backend, DeterministicModerationBackend)

    def test_runtime_selects_llm_moderated_backend_shape(self):
        runtime = GameRuntime.from_session_config(
            _llm_moderated_session_config(),
            llm_backend=ScriptedModerationBackend(
                decisions=[
                    ModerationDecision(
                        accepted=True,
                        moderator_mode="llm_moderated",
                        next_state=None,
                    )
                ]
            ),
        )
        assert isinstance(runtime.moderation_backend, ScriptedModerationBackend)

    def test_runtime_selects_hybrid_audit_backend(self):
        runtime = GameRuntime.from_session_config(
            _hybrid_audit_session_config(),
            llm_backend=ScriptedModerationBackend(
                decisions=[
                    ModerationDecision(
                        accepted=True,
                        moderator_mode="llm_moderated",
                        next_state=GameRuntime.from_session_config(_session_config()).state,
                    )
                ]
            ),
        )
        assert isinstance(runtime.moderation_backend, HybridAuditBackend)

    def test_battleship_runtime_exposes_different_visible_state_per_player(self):
        runtime = GameRuntime.from_session_config(_battleship_session_config())

        alpha_view = runtime.visible_state("captain_alpha")
        beta_view = runtime.visible_state("captain_beta")

        assert alpha_view.payload["own_fleet"] != beta_view.payload["own_fleet"]
        assert "A1" in str(alpha_view.payload["own_fleet"])
        assert "B1" in str(beta_view.payload["own_fleet"])
