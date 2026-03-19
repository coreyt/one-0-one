"""Authoritative Connect Four game implementation."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import Field

from src.games.contracts import (
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

if TYPE_CHECKING:
    from src.session.config import AgentConfig, GameConfig


_COLUMN_RE = re.compile(r"\b(?:column\s*)?([1-7])\b", re.IGNORECASE)


def render_connect_four_board(
    board: list[list[str]],
    *,
    bordered: bool,
    empty_cell: str = "·",
) -> str:
    """Render a Connect Four board for either player-facing or reasoning use."""
    if not board:
        return ""

    column_count = len(board[0])
    header = "  " + " ".join(str(index) for index in range(1, column_count + 1))
    rows = [" ".join(empty_cell if cell == "." else cell for cell in row) for row in board]
    if not bordered:
        return "\n".join([header, *rows])

    width = column_count * 2 - 1
    framed_rows = [f"│ {row} │" for row in rows]
    return "\n".join(
        [
            header,
            f"┌{'─' * (width + 2)}┐",
            *framed_rows,
            f"└{'─' * (width + 2)}┘",
        ]
    )


class ConnectFourState(GameStateBase):
    """Authoritative runtime state for Connect Four."""

    phase: str = "playing"
    rows: int = 6
    columns: int = 7
    connect_n: int = 4
    board: list[list[str]] = Field(default_factory=list)
    players: list[str] = Field(default_factory=list)
    disc_by_player: dict[str, str] = Field(default_factory=dict)
    active_player: str = ""
    winner: str | None = None
    is_draw: bool = False
    move_count: int = 0
    last_move: dict[str, Any] | None = None


class ConnectFourGame:
    """Deterministic Connect Four plugin with authoritative board state."""

    game_type = "connect_four"

    @staticmethod
    def render_board(
        board: list[list[str]],
        *,
        bordered: bool,
        empty_cell: str = "·",
    ) -> str:
        return render_connect_four_board(
            board,
            bordered=bordered,
            empty_cell=empty_cell,
        )

    def initial_state(
        self,
        config: "GameConfig",
        agents: list["AgentConfig"],
    ) -> ConnectFourState:
        players = [agent.id for agent in agents if agent.role != "moderator"][:2]
        disc_by_player: dict[str, str] = {}
        if players:
            disc_by_player[players[0]] = "R"
        if len(players) > 1:
            disc_by_player[players[1]] = "B"

        rows = 6
        columns = 7
        board = [["." for _ in range(columns)] for _ in range(rows)]
        return ConnectFourState(
            rows=rows,
            columns=columns,
            connect_n=4,
            board=board,
            players=players,
            disc_by_player=disc_by_player,
            active_player=players[0] if players else "",
        )

    def initial_channels(self, state: ConnectFourState) -> list[ChannelSpec]:
        return [ChannelSpec(channel_id="public", channel_type="public")]

    def visible_state(
        self,
        state: ConnectFourState,
        viewer_id: str,
    ) -> VisibleGameState:
        return VisibleGameState(
            viewer_id=viewer_id,
            payload={
                "phase": state.phase,
                "board": state.board,
                "active_player": state.active_player,
                "winner": state.winner,
                "is_draw": state.is_draw,
                "move_count": state.move_count,
                "last_move": state.last_move,
            },
        )

    def turn_context(self, state: ConnectFourState) -> TurnContext:
        if self.is_terminal(state):
            return TurnContext(active_actor_ids=[], phase=state.phase)
        return TurnContext(
            active_actor_ids=[state.active_player] if state.active_player else [],
            phase=state.phase,
            allow_parallel=False,
            prompt="Choose a column from 1 to 7.",
        )

    def legal_actions(
        self,
        state: ConnectFourState,
        actor_id: str,
    ) -> list[ActionSpec]:
        if self.is_terminal(state) or actor_id != state.active_player:
            return []
        open_columns = [
            index + 1
            for index in range(state.columns)
            if state.board[0][index] == "."
        ]
        return [
            ActionSpec(
                action_type="drop_disc",
                description="Drop a disc into an open column.",
                input_schema={"column": open_columns},
            )
        ]

    def validate_action(
        self,
        state: ConnectFourState,
        actor_id: str,
        action: GameAction,
    ) -> ValidationResult:
        if self.is_terminal(state):
            return ValidationResult(
                is_valid=False,
                reason="Game is already over.",
            )
        if actor_id != state.active_player:
            return ValidationResult(
                is_valid=False,
                reason="It is not this player's turn.",
            )
        if action.action_type != "drop_disc":
            return ValidationResult(
                is_valid=False,
                reason="Unsupported action type for Connect Four.",
            )
        column = action.payload.get("column")
        if not isinstance(column, int):
            return ValidationResult(
                is_valid=False,
                reason="Column must be an integer from 1 to 7.",
            )
        if column < 1 or column > state.columns:
            return ValidationResult(
                is_valid=False,
                reason="Column is out of range.",
            )
        if state.board[0][column - 1] != ".":
            return ValidationResult(
                is_valid=False,
                reason="Column is full.",
            )
        return ValidationResult(
            is_valid=True,
            normalized_action=GameAction(
                action_type="drop_disc",
                payload={"column": column},
            ),
        )

    def apply_action(
        self,
        state: ConnectFourState,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult:
        validation = self.validate_action(state, actor_id, action)
        if not validation.is_valid or validation.normalized_action is None:
            raise ValueError(validation.reason or "Invalid action.")

        column = validation.normalized_action.payload["column"] - 1
        row = self._drop_row(state.board, column)
        disc = state.disc_by_player[actor_id]
        next_board = [line[:] for line in state.board]
        next_board[row][column] = disc

        winner = actor_id if self._is_winning_move(next_board, row, column, disc, state.connect_n) else None
        move_count = state.move_count + 1
        is_draw = winner is None and move_count == state.rows * state.columns

        next_state = state.model_copy(
            update={
                "board": next_board,
                "winner": winner,
                "is_draw": is_draw,
                "move_count": move_count,
                "turn_index": state.turn_index + 1,
                "round_number": move_count // len(state.players) if state.players else 0,
                "active_player": (
                    ""
                    if winner or is_draw
                    else self._next_player(state.players, actor_id)
                ),
                "phase": "complete" if winner or is_draw else "playing",
                "last_move": {
                    "player_id": actor_id,
                    "column": column + 1,
                    "row": row,
                    "disc": disc,
                },
            }
        )

        summary = f"{actor_id} dropped {disc} into column {column + 1}."
        if winner:
            summary = f"{actor_id} wins by connecting four."
        elif is_draw:
            summary = "The board is full. The game is a draw."

        return ApplyResult(
            next_state=next_state,
            state_delta={
                "last_move": next_state.last_move,
                "active_player": next_state.active_player,
                "winner": next_state.winner,
                "is_draw": next_state.is_draw,
                "move_count": next_state.move_count,
            },
            public_events=[{"summary": summary}],
            turn_advanced=True,
        )

    def is_terminal(self, state: ConnectFourState) -> bool:
        return bool(state.winner or state.is_draw or state.phase == "complete")

    def outcome(self, state: ConnectFourState) -> GameOutcome | None:
        if state.winner:
            losers = [player for player in state.players if player != state.winner]
            return GameOutcome(
                status="win",
                winners=[state.winner],
                losers=losers,
                summary=f"{state.winner} connected four.",
            )
        if state.is_draw:
            return GameOutcome(
                status="draw",
                summary="All cells are filled with no winner.",
            )
        return None

    def parse_action_text(self, text: str) -> GameAction | None:
        match = _COLUMN_RE.search(text)
        if not match:
            return None
        return GameAction(
            action_type="drop_disc",
            payload={"column": int(match.group(1))},
        )

    def parse_action_payload(self, payload: dict[str, Any]) -> GameAction | None:
        """Build a typed action from structured player output."""
        column = payload.get("column")
        if isinstance(column, str):
            column = column.strip()
            if column.isdigit():
                column = int(column)
        if not isinstance(column, int) or isinstance(column, bool):
            return None
        return GameAction(
            action_type="drop_disc",
            payload={"column": column},
        )

    @staticmethod
    def _drop_row(board: list[list[str]], column: int) -> int:
        for row in range(len(board) - 1, -1, -1):
            if board[row][column] == ".":
                return row
        raise ValueError("Column is full.")

    @staticmethod
    def _next_player(players: list[str], current_player: str) -> str:
        if len(players) < 2:
            return ""
        current_index = players.index(current_player)
        return players[(current_index + 1) % len(players)]

    @staticmethod
    def _is_winning_move(
        board: list[list[str]],
        row: int,
        column: int,
        disc: str,
        connect_n: int,
    ) -> bool:
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]
        for d_row, d_col in directions:
            total = 1
            total += ConnectFourGame._count_direction(board, row, column, disc, d_row, d_col)
            total += ConnectFourGame._count_direction(board, row, column, disc, -d_row, -d_col)
            if total >= connect_n:
                return True
        return False

    @staticmethod
    def _count_direction(
        board: list[list[str]],
        start_row: int,
        start_col: int,
        disc: str,
        d_row: int,
        d_col: int,
    ) -> int:
        rows = len(board)
        columns = len(board[0]) if board else 0
        row = start_row + d_row
        col = start_col + d_col
        count = 0
        while 0 <= row < rows and 0 <= col < columns and board[row][col] == disc:
            count += 1
            row += d_row
            col += d_col
        return count
