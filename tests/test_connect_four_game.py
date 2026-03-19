"""Tests for the authoritative Connect Four game plugin."""

from src.games import ConnectFourGame, GameAction, load_game, load_game_from_config
from src.games.connect_four import render_connect_four_board
from src.session.config import AgentConfig, GameConfig


def _agents() -> list[AgentConfig]:
    return [
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
    ]


def _config() -> GameConfig:
    return GameConfig(name="Connect Four")


class TestConnectFourInitialState:
    def test_initial_state_assigns_players_and_board(self):
        game = ConnectFourGame()
        state = game.initial_state(_config(), _agents())

        assert state.players == ["player_red", "player_black"]
        assert state.active_player == "player_red"
        assert state.disc_by_player["player_red"] == "R"
        assert state.disc_by_player["player_black"] == "B"
        assert len(state.board) == 6
        assert len(state.board[0]) == 7

    def test_registry_loads_connect_four(self):
        game = load_game("connect-four")
        assert isinstance(game, ConnectFourGame)

    def test_registry_loads_from_game_config_plugin(self):
        game = load_game_from_config(GameConfig(plugin="connect_four", name="Connect Four"))
        assert isinstance(game, ConnectFourGame)

    def test_render_board_without_border_for_reasoning(self):
        game = ConnectFourGame()
        state = game.initial_state(_config(), _agents())

        rendered = render_connect_four_board(state.board, bordered=False, empty_cell=".")

        assert "┌" not in rendered
        assert "└" not in rendered
        assert "1 2 3 4 5 6 7" in rendered
        assert ". . . . . . ." in rendered

    def test_render_board_with_border_for_main_display(self):
        game = ConnectFourGame()
        state = game.initial_state(_config(), _agents())

        rendered = render_connect_four_board(state.board, bordered=True, empty_cell="·")

        assert "┌" in rendered
        assert "└" in rendered
        assert "│ · · · · · · · │" in rendered


class TestConnectFourParsingAndValidation:
    def test_parse_action_text_extracts_column(self):
        game = ConnectFourGame()
        action = game.parse_action_text("Column 4. My move.")

        assert action is not None
        assert action.action_type == "drop_disc"
        assert action.payload["column"] == 4

    def test_validate_rejects_wrong_turn(self):
        game = ConnectFourGame()
        state = game.initial_state(_config(), _agents())

        result = game.validate_action(
            state,
            "player_black",
            GameAction(action_type="drop_disc", payload={"column": 4}),
        )

        assert result.is_valid is False
        assert "not this player's turn" in (result.reason or "").lower()

    def test_validate_rejects_full_column(self):
        game = ConnectFourGame()
        state = game.initial_state(_config(), _agents())
        for _ in range(state.rows):
            actor = state.active_player
            state = game.apply_action(
                state,
                actor,
                GameAction(action_type="drop_disc", payload={"column": 1}),
            ).next_state

        result = game.validate_action(
            state,
            state.active_player,
            GameAction(action_type="drop_disc", payload={"column": 1}),
        )

        assert result.is_valid is False
        assert result.reason == "Column is full."


class TestConnectFourStateTransitions:
    def test_apply_action_updates_board_and_switches_player(self):
        game = ConnectFourGame()
        state = game.initial_state(_config(), _agents())

        result = game.apply_action(
            state,
            "player_red",
            GameAction(action_type="drop_disc", payload={"column": 4}),
        )
        next_state = result.next_state

        assert next_state.board[5][3] == "R"
        assert next_state.active_player == "player_black"
        assert next_state.move_count == 1
        assert next_state.last_move == {
            "player_id": "player_red",
            "column": 4,
            "row": 5,
            "disc": "R",
        }

    def test_horizontal_win_detection(self):
        game = ConnectFourGame()
        state = game.initial_state(_config(), _agents())
        moves = [1, 1, 2, 2, 3, 3, 4]
        actors = [
            "player_red",
            "player_black",
            "player_red",
            "player_black",
            "player_red",
            "player_black",
            "player_red",
        ]

        for actor, column in zip(actors, moves):
            state = game.apply_action(
                state,
                actor,
                GameAction(action_type="drop_disc", payload={"column": column}),
            ).next_state

        assert state.winner == "player_red"
        assert game.is_terminal(state) is True
        outcome = game.outcome(state)
        assert outcome is not None
        assert outcome.winners == ["player_red"]

    def test_visible_state_exposes_authoritative_board(self):
        game = ConnectFourGame()
        state = game.initial_state(_config(), _agents())
        state = game.apply_action(
            state,
            "player_red",
            GameAction(action_type="drop_disc", payload={"column": 2}),
        ).next_state

        visible = game.visible_state(state, "player_black")

        assert visible.payload["board"][5][1] == "R"
        assert visible.payload["active_player"] == "player_black"
