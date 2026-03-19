"""Tests for the authoritative Battleship game plugin."""

from src.games import GameAction, load_game, load_game_from_config
from src.games.battleship import BattleshipGame
from src.session.config import AgentConfig, GameConfig


def _agents() -> list[AgentConfig]:
    return [
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
    ]


def _config() -> GameConfig:
    return GameConfig(plugin="battleship", name="Battleship")


class TestBattleshipInitialState:
    def test_initial_state_assigns_players_and_fixed_fleets(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())

        assert state.players == ["captain_alpha", "captain_beta"]
        assert state.active_player == "captain_alpha"
        assert "Carrier" in state.ship_positions["captain_alpha"]
        assert "Carrier" in state.ship_positions["captain_beta"]

    def test_registry_loads_battleship(self):
        assert isinstance(load_game("battleship"), BattleshipGame)
        assert isinstance(
            load_game_from_config(GameConfig(plugin="battleship", name="Battleship")),
            BattleshipGame,
        )


class TestBattleshipVisibilityAndParsing:
    def test_parse_action_text_extracts_coordinate(self):
        game = BattleshipGame()
        action = game.parse_action_text("Fire at B5.")

        assert action is not None
        assert action.action_type == "fire_shot"
        assert action.payload["coordinate"] == "B5"

    def test_parse_action_payload_extracts_coordinate(self):
        game = BattleshipGame()
        action = game.parse_action_payload({"coordinate": "a10"})

        assert action is not None
        assert action.action_type == "fire_shot"
        assert action.payload["coordinate"] == "A10"

    def test_visible_state_hides_opponent_ship_positions(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())

        alpha_view = game.visible_state(state, "captain_alpha")
        beta_view = game.visible_state(state, "captain_beta")

        assert "ship_positions" in alpha_view.payload["own_fleet"]
        assert "opponent_fleet" not in alpha_view.payload
        assert alpha_view.payload["own_fleet"] != beta_view.payload["own_fleet"]


class TestBattleshipValidationAndTransitions:
    def test_validate_rejects_wrong_turn(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())

        result = game.validate_action(
            state,
            "captain_beta",
            GameAction(action_type="fire_shot", payload={"coordinate": "B5"}),
        )

        assert result.is_valid is False
        assert "not this player's turn" in (result.reason or "").lower()

    def test_validate_rejects_repeat_coordinate(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())
        state = game.apply_action(
            state,
            "captain_alpha",
            GameAction(action_type="fire_shot", payload={"coordinate": "B1"}),
        ).next_state
        state = game.apply_action(
            state,
            "captain_beta",
            GameAction(action_type="fire_shot", payload={"coordinate": "A1"}),
        ).next_state

        result = game.validate_action(
            state,
            "captain_alpha",
            GameAction(action_type="fire_shot", payload={"coordinate": "B1"}),
        )

        assert result.is_valid is False
        assert "already fired" in (result.reason or "").lower()

    def test_apply_action_marks_hit_and_switches_player(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())

        result = game.apply_action(
            state,
            "captain_alpha",
            GameAction(action_type="fire_shot", payload={"coordinate": "B1"}),
        )

        next_state = result.next_state
        assert next_state.attack_history["captain_alpha"]["B1"] == "hit"
        assert next_state.active_player == "captain_beta"
        assert result.state_delta["result"] == "hit"

    def test_sunk_ship_and_win_detection(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())
        moves = [
            ("captain_alpha", "B1"),
            ("captain_beta", "A1"),
            ("captain_alpha", "B2"),
            ("captain_beta", "A2"),
            ("captain_alpha", "B3"),
            ("captain_beta", "A3"),
            ("captain_alpha", "B4"),
            ("captain_beta", "A4"),
            ("captain_alpha", "B5"),
            ("captain_beta", "A5"),
            ("captain_alpha", "D1"),
            ("captain_beta", "C1"),
            ("captain_alpha", "D2"),
            ("captain_beta", "C2"),
            ("captain_alpha", "D3"),
            ("captain_beta", "C3"),
            ("captain_alpha", "D4"),
            ("captain_beta", "E1"),
            ("captain_alpha", "F1"),
            ("captain_beta", "E2"),
            ("captain_alpha", "F2"),
            ("captain_beta", "E3"),
            ("captain_alpha", "F3"),
            ("captain_beta", "E4"),
            ("captain_alpha", "H1"),
            ("captain_beta", "G1"),
            ("captain_alpha", "H2"),
            ("captain_beta", "G2"),
            ("captain_alpha", "H3"),
            ("captain_beta", "G3"),
            ("captain_alpha", "J1"),
            ("captain_beta", "I1"),
            ("captain_alpha", "J2"),
        ]

        for actor, coordinate in moves:
            state = game.apply_action(
                state,
                actor,
                GameAction(action_type="fire_shot", payload={"coordinate": coordinate}),
            ).next_state

        assert game.is_terminal(state) is True
        outcome = game.outcome(state)
        assert outcome is not None
        assert outcome.winners == ["captain_alpha"]
