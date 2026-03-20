"""Tests for the authoritative Battleship game plugin."""

import pytest

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
    def test_initial_state_assigns_players_and_legal_fleets(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())

        assert state.players == ["captain_alpha", "captain_beta"]
        assert state.active_player == "captain_alpha"
        for player_id in state.players:
            layout = state.ship_positions[player_id]
            assert [name for name, _ in layout.items()] == [name for name, _ in [
                ("Carrier", 5),
                ("Battleship", 4),
                ("Cruiser", 3),
                ("Submarine", 3),
                ("Destroyer", 2),
            ]]
            occupied = [coordinate for coords in layout.values() for coordinate in coords]
            assert len(occupied) == 17
            assert len(set(occupied)) == 17

    def test_registry_loads_battleship(self):
        assert isinstance(load_game("battleship"), BattleshipGame)
        assert isinstance(
            load_game_from_config(GameConfig(plugin="battleship", name="Battleship")),
            BattleshipGame,
        )

    def test_initial_state_requires_exactly_two_players(self):
        game = BattleshipGame()
        agents = _agents()[:2]

        with pytest.raises(ValueError, match="exactly two"):
            game.initial_state(_config(), agents)


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
        beta_target = next(iter(next(iter(state.ship_positions["captain_beta"].values()))))
        alpha_target = next(iter(next(iter(state.ship_positions["captain_alpha"].values()))))
        state = game.apply_action(
            state,
            "captain_alpha",
            GameAction(action_type="fire_shot", payload={"coordinate": beta_target}),
        ).next_state
        state = game.apply_action(
            state,
            "captain_beta",
            GameAction(action_type="fire_shot", payload={"coordinate": alpha_target}),
        ).next_state

        result = game.validate_action(
            state,
            "captain_alpha",
            GameAction(action_type="fire_shot", payload={"coordinate": beta_target}),
        )

        assert result.is_valid is False
        assert "already fired" in (result.reason or "").lower()

    def test_apply_action_marks_hit_and_switches_player(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())
        beta_target = next(iter(next(iter(state.ship_positions["captain_beta"].values()))))

        result = game.apply_action(
            state,
            "captain_alpha",
            GameAction(action_type="fire_shot", payload={"coordinate": beta_target}),
        )

        next_state = result.next_state
        assert next_state.attack_history["captain_alpha"][beta_target] == "hit"
        assert next_state.active_player == "captain_beta"
        assert result.state_delta["result"] == "hit"

    def test_sunk_ship_and_win_detection(self):
        game = BattleshipGame()
        state = game.initial_state(_config(), _agents())
        beta_targets = [
            coordinate
            for _, coordinates in state.ship_positions["captain_beta"].items()
            for coordinate in coordinates
        ]
        alpha_occupied = {
            coordinate
            for _, coordinates in state.ship_positions["captain_alpha"].items()
            for coordinate in coordinates
        }
        beta_misses = [
            f"{column}{row}"
            for column in "ABCDEFGHIJ"
            for row in range(1, 11)
            if f"{column}{row}" not in alpha_occupied
        ]
        moves = []
        for index, beta_target in enumerate(beta_targets):
            moves.append(("captain_alpha", beta_target))
            if index < len(beta_targets) - 1:
                moves.append(("captain_beta", beta_misses[index]))

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
