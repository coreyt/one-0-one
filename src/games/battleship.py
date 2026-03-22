"""Authoritative Battleship game implementation."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from pydantic import Field

from src.games.contracts import (
    ActionSpec,
    AgentGameContext,
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


# ---------------------------------------------------------------------------
# Battleship player journal — pluggable renderers
# ---------------------------------------------------------------------------
#
# Each renderer receives a _BattleshipJournalCtx and returns a string
# injected into the player's per-turn system message.  Add a new renderer
# and register it in _BATTLESHIP_JOURNAL_RENDERERS to expose it as a valid
# ``game.journal_format`` option in session templates.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _BattleshipJournalCtx:
    my_history: dict       # coord → "hit"|"miss"
    fired_count: int
    remaining: int
    opp_history: dict      # coord → "hit"|"miss"
    opponent_id: str | None
    ship_positions: dict   # ship_name → list[{coordinate, status}]
    sunk: list[str]


_JournalRendererFn = Callable[["_BattleshipJournalCtx"], str]

_BOARD_COLS = list("ABCDEFGHIJ")
_BOARD_ROWS = list(range(1, 11))


def _journal_renderer_xml(ctx: _BattleshipJournalCtx) -> str:
    """Structured XML — coordinate names are explicit named attributes.

    The LLM can match fired coordinates by name without any spatial reasoning.
    Best for most current models; eliminates the need to parse positional grids.
    """
    parts: list[str] = ["<game_state>"]
    parts.append(
        f'  <your_attacks fired="{ctx.fired_count}" remaining="{ctx.remaining}"'
        ' rule="DO NOT fire at any coordinate already in this list">'
    )
    for coord, result in ctx.my_history.items():
        parts.append(f'    <shot coordinate="{coord}" result="{result}"/>')
    parts.append("  </your_attacks>")
    if ctx.opponent_id is not None:
        parts.append(f'  <opponent_attacks fired="{len(ctx.opp_history)}">')
        for coord, result in ctx.opp_history.items():
            parts.append(f'    <shot coordinate="{coord}" result="{result}"/>')
        parts.append("  </opponent_attacks>")
    if ctx.ship_positions:
        parts.append("  <your_fleet>")
        for ship_name, cells in ctx.ship_positions.items():
            if ship_name in ctx.sunk:
                status = "sunk"
            else:
                hit_count = sum(1 for c in cells if c.get("status") == "hit")
                status = f"damaged({hit_count}_hit)" if hit_count else "intact"
            parts.append(
                f'    <ship name="{ship_name}" size="{len(cells)}" status="{status}"/>'
            )
        parts.append("  </your_fleet>")
    parts.append("</game_state>")
    return "\n".join(parts)


def _journal_renderer_text(ctx: _BattleshipJournalCtx) -> str:
    """Compact unstructured text — tests whether a model can track state from plain text.

    Coordinates appear as "E5:miss" tokens.  The LLM must parse and cross-reference
    the list to avoid repeats rather than reading structured element names.
    """
    lines: list[str] = ["=== GAME STATE ==="]
    if ctx.my_history:
        tokens = [f"{c}:{r}" for c, r in ctx.my_history.items()]
        lines.append(
            f"Your shots ({ctx.fired_count} fired, {ctx.remaining} remaining)"
            " — DO NOT fire at any coordinate in this list:"
        )
        for i in range(0, len(tokens), 10):
            lines.append("  " + "  ".join(tokens[i : i + 10]))
    else:
        lines.append(f"Your shots: none yet ({ctx.remaining} available)")
    if ctx.opp_history:
        tokens = [f"{c}:{r}" for c, r in ctx.opp_history.items()]
        lines.append(f"Opponent shots against your fleet ({len(tokens)} fired):")
        for i in range(0, len(tokens), 10):
            lines.append("  " + "  ".join(tokens[i : i + 10]))
    else:
        lines.append("Opponent shots against your fleet: none yet")
    if ctx.ship_positions:
        lines.append("Your fleet:")
        for ship_name, cells in ctx.ship_positions.items():
            if ship_name in ctx.sunk:
                status = "SUNK"
            else:
                hit_count = sum(1 for c in cells if c.get("status") == "hit")
                status = f"{hit_count} cell(s) hit" if hit_count else "intact"
            lines.append(f"  {ship_name}({len(cells)}): {status}")
    return "\n".join(lines)


def _journal_renderer_board(ctx: _BattleshipJournalCtx) -> str:
    """2D attack grid — tests spatial/positional reasoning in the model.

    Legend: · = unfired (legal target)  X = hit  O = miss
    The LLM must map column letter + row number to a coordinate string
    (e.g., column C, row 5 → "C5") rather than reading an explicit name.
    """
    lines: list[str] = ["=== GAME STATE ==="]
    header = "    " + " ".join(_BOARD_COLS)
    grid_rows = [header]
    for row in _BOARD_ROWS:
        cells = [
            "X" if ctx.my_history.get(f"{col}{row}") == "hit"
            else "O" if ctx.my_history.get(f"{col}{row}") == "miss"
            else "·"
            for col in _BOARD_COLS
        ]
        grid_rows.append(f"{row:>2}  " + " ".join(cells))
    lines.append(
        f"Your attack grid ({ctx.fired_count} fired, {ctx.remaining} remaining)"
        " — · = legal target, X = hit (do not re-fire), O = miss (do not re-fire):"
    )
    lines.extend(grid_rows)
    if ctx.opp_history:
        tokens = [f"{c}:{r}" for c, r in ctx.opp_history.items()]
        lines.append(f"Opponent shots against your fleet ({len(tokens)} fired):")
        for i in range(0, len(tokens), 10):
            lines.append("  " + "  ".join(tokens[i : i + 10]))
    else:
        lines.append("Opponent shots against your fleet: none yet")
    if ctx.ship_positions:
        lines.append("Your fleet:")
        for ship_name, cells in ctx.ship_positions.items():
            if ship_name in ctx.sunk:
                status = "SUNK"
            else:
                hit_count = sum(1 for c in cells if c.get("status") == "hit")
                status = f"{hit_count} cell(s) hit" if hit_count else "intact"
            lines.append(f"  {ship_name}({len(cells)}): {status}")
    return "\n".join(lines)


_BATTLESHIP_JOURNAL_RENDERERS: dict[str, _JournalRendererFn] = {
    "xml": _journal_renderer_xml,
    "text": _journal_renderer_text,
    "board": _journal_renderer_board,
}


_COORD_RE = re.compile(r"\b([A-Ja-j](?:10|[1-9]))\b")
_FLEET_ORDER = [
    ("Carrier", 5),
    ("Battleship", 4),
    ("Cruiser", 3),
    ("Submarine", 3),
    ("Destroyer", 2),
]
class BattleshipState(GameStateBase):
    """Authoritative runtime state for Battleship."""

    phase: str = "playing"
    grid_size: int = 10
    players: list[str] = Field(default_factory=list)
    active_player: str = ""
    ship_positions: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    attack_history: dict[str, dict[str, str]] = Field(default_factory=dict)
    hits_received: dict[str, list[str]] = Field(default_factory=dict)
    sunk_ships: dict[str, list[str]] = Field(default_factory=dict)
    winner: str | None = None
    last_shot: dict[str, Any] | None = None


class BattleshipGame:
    """Deterministic Battleship plugin with hidden per-player fleet state."""

    game_type = "battleship"

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.SystemRandom()

    def initial_state(
        self,
        config: "GameConfig",
        agents: list["AgentConfig"],
    ) -> BattleshipState:
        players = [agent.id for agent in agents if agent.role != "moderator"][:2]
        if len(players) != 2:
            raise ValueError("Battleship requires exactly two non-moderator players.")
        ship_positions = {
            player_id: self._generate_layout()
            for player_id in players
        }
        return BattleshipState(
            players=players,
            active_player=players[0] if players else "",
            ship_positions=ship_positions,
            attack_history={player_id: {} for player_id in players},
            hits_received={player_id: [] for player_id in players},
            sunk_ships={player_id: [] for player_id in players},
        )

    def initial_channels(self, state: BattleshipState) -> list[ChannelSpec]:
        return [ChannelSpec(channel_id="public", channel_type="public")]

    def visible_state(self, state: BattleshipState, viewer_id: str) -> VisibleGameState:
        own_hits = set(state.hits_received.get(viewer_id, []))
        own_ship_positions = state.ship_positions.get(viewer_id, {})
        own_fleet = {
            ship_name: [
                {
                    "coordinate": coordinate,
                    "status": "hit" if coordinate in own_hits else "intact",
                }
                for coordinate in coordinates
            ]
            for ship_name, coordinates in own_ship_positions.items()
        }
        return VisibleGameState(
            viewer_id=viewer_id,
            payload={
                "phase": state.phase,
                "active_player": state.active_player,
                "winner": state.winner,
                "own_fleet": {
                    "ship_positions": own_fleet,
                    "sunk_ships": state.sunk_ships.get(viewer_id, []),
                },
                "attack_history": state.attack_history.get(viewer_id, {}),
                "last_shot": state.last_shot,
            },
        )

    def turn_context(self, state: BattleshipState) -> TurnContext:
        if self.is_terminal(state):
            return TurnContext(active_actor_ids=[], phase=state.phase)
        return TurnContext(
            active_actor_ids=[state.active_player] if state.active_player else [],
            phase=state.phase,
            allow_parallel=False,
            prompt="Choose one grid coordinate from A1 to J10.",
        )

    def legal_actions(self, state: BattleshipState, actor_id: str) -> list[ActionSpec]:
        if self.is_terminal(state) or actor_id != state.active_player:
            return []
        return [
            ActionSpec(
                action_type="fire_shot",
                description="Fire one shot at an enemy grid coordinate.",
                input_schema={"coordinate_pattern": "^[A-J](10|[1-9])$"},
            )
        ]

    def validate_action(
        self,
        state: BattleshipState,
        actor_id: str,
        action: GameAction,
    ) -> ValidationResult:
        if self.is_terminal(state):
            return ValidationResult(is_valid=False, reason="Game is already over.")
        if actor_id != state.active_player:
            return ValidationResult(is_valid=False, reason="It is not this player's turn.")
        if action.action_type != "fire_shot":
            return ValidationResult(is_valid=False, reason="Unsupported action type for Battleship.")
        coordinate = action.payload.get("coordinate")
        if not isinstance(coordinate, str):
            return ValidationResult(is_valid=False, reason="Coordinate must be a grid reference.")
        normalized = coordinate.strip().upper()
        if _COORD_RE.fullmatch(normalized) is None:
            return ValidationResult(is_valid=False, reason="Coordinate must be from A1 to J10.")
        if normalized in state.attack_history.get(actor_id, {}):
            return ValidationResult(is_valid=False, reason="Coordinate has already fired at.")
        return ValidationResult(
            is_valid=True,
            normalized_action=GameAction(action_type="fire_shot", payload={"coordinate": normalized}),
        )

    def apply_action(
        self,
        state: BattleshipState,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult:
        validation = self.validate_action(state, actor_id, action)
        if not validation.is_valid or validation.normalized_action is None:
            raise ValueError(validation.reason or "Invalid action.")

        coordinate = validation.normalized_action.payload["coordinate"]
        target_id = self._target_player(state.players, actor_id)
        target_layout = state.ship_positions[target_id]
        hit_ship = next(
            (
                ship_name
                for ship_name, coordinates in target_layout.items()
                if coordinate in coordinates
            ),
            None,
        )
        result = "hit" if hit_ship is not None else "miss"

        next_attack_history = {
            player_id: dict(history)
            for player_id, history in state.attack_history.items()
        }
        next_attack_history[actor_id][coordinate] = result

        next_hits_received = {
            player_id: list(hits)
            for player_id, hits in state.hits_received.items()
        }
        if result == "hit":
            next_hits_received[target_id].append(coordinate)

        next_sunk_ships = {
            player_id: list(sunk)
            for player_id, sunk in state.sunk_ships.items()
        }
        sunk_ship: str | None = None
        if hit_ship is not None:
            ship_cells = set(target_layout[hit_ship])
            received = set(next_hits_received[target_id])
            if ship_cells.issubset(received) and hit_ship not in next_sunk_ships[target_id]:
                next_sunk_ships[target_id].append(hit_ship)
                sunk_ship = hit_ship

        winner = actor_id if len(next_sunk_ships[target_id]) == len(_FLEET_ORDER) else None
        next_state = state.model_copy(
            update={
                "attack_history": next_attack_history,
                "hits_received": next_hits_received,
                "sunk_ships": next_sunk_ships,
                "winner": winner,
                "turn_index": state.turn_index + 1,
                "round_number": state.round_number + 1,
                "active_player": "" if winner else target_id,
                "phase": "complete" if winner else "playing",
                "last_shot": {
                    "attacker_id": actor_id,
                    "target_id": target_id,
                    "coordinate": coordinate,
                    "result": result,
                    "sunk_ship": sunk_ship,
                },
            }
        )
        summary = f"{actor_id} fires at {coordinate}: {result.upper()}."
        if sunk_ship:
            summary += f" {sunk_ship} sunk."
        if winner:
            summary += f" {actor_id} wins."
        return ApplyResult(
            next_state=next_state,
            state_delta=next_state.last_shot or {},
            public_events=[{"summary": summary}],
            turn_advanced=True,
        )

    def is_terminal(self, state: BattleshipState) -> bool:
        return bool(state.winner or state.phase == "complete")

    def outcome(self, state: BattleshipState) -> GameOutcome | None:
        if state.winner:
            losers = [player_id for player_id in state.players if player_id != state.winner]
            return GameOutcome(
                status="win",
                winners=[state.winner],
                losers=losers,
                summary=f"{state.winner} sank the full enemy fleet.",
            )
        return None

    def parse_action_text(self, text: str) -> GameAction | None:
        match = _COORD_RE.search(text)
        if match is None:
            return None
        return GameAction(action_type="fire_shot", payload={"coordinate": match.group(1).upper()})

    def render_agent_context(
        self,
        state: BattleshipState,
        viewer_id: str,
        role: str,
        *,
        config: "GameConfig | None" = None,
    ) -> AgentGameContext:
        viewer = self.visible_state(state, viewer_id)
        authoritative = state.model_dump()

        if role == "moderator":
            return AgentGameContext(
                instructions=[
                    "role=presentation_referee",
                    "Read authoritative_state to narrate hit/miss/sunk results and both tracking grids.",
                    "Do not validate moves or decide the winner yourself.",
                    "Do not reveal hidden ship coordinates that have not been observed in play unless the game is already over.",
                ],
                state_lines=[
                    f"authoritative_state={json.dumps(authoritative)}",
                    f"visible_state={json.dumps(viewer.payload)}",
                ],
            )

        journal_format = config.journal_format if config is not None else "xml"
        players: list[str] = state.players
        opponent_id = next((p for p in players if p != viewer_id), None)
        my_history: dict[str, str] = viewer.payload.get("attack_history", {})
        own_fleet_info = viewer.payload.get("own_fleet", {})
        ctx = _BattleshipJournalCtx(
            my_history=my_history,
            fired_count=len(my_history),
            remaining=100 - len(my_history),
            opp_history=(
                state.attack_history.get(opponent_id, {}) if opponent_id else {}
            ),
            opponent_id=opponent_id,
            ship_positions=own_fleet_info.get("ship_positions", {}),
            sunk=own_fleet_info.get("sunk_ships", []),
        )
        renderer = _BATTLESHIP_JOURNAL_RENDERERS.get(journal_format, _journal_renderer_xml)
        journal = renderer(ctx)
        return AgentGameContext(
            instructions=[],
            state_lines=[journal],
            response_schema='{"coordinate": "B5"}',
            response_example='{"coordinate": "A10"}',
        )

    def parse_action_payload(self, payload: dict[str, Any]) -> GameAction | None:
        """Build a typed action from structured player output."""
        coordinate = payload.get("coordinate")
        if not isinstance(coordinate, str):
            return None
        normalized = coordinate.strip().upper()
        if _COORD_RE.fullmatch(normalized) is None:
            return None
        return GameAction(action_type="fire_shot", payload={"coordinate": normalized})

    @staticmethod
    def _target_player(players: list[str], actor_id: str) -> str:
        return next(player_id for player_id in players if player_id != actor_id)

    def _generate_layout(self) -> dict[str, list[str]]:
        occupied: set[str] = set()
        layout: dict[str, list[str]] = {}
        for ship_name, size in _FLEET_ORDER:
            while True:
                orientation = self._rng.choice(("horizontal", "vertical"))
                if orientation == "horizontal":
                    start_col = self._rng.randint(0, 10 - size)
                    row = self._rng.randint(1, 10)
                    coordinates = [
                        f"{chr(ord('A') + start_col + offset)}{row}"
                        for offset in range(size)
                    ]
                else:
                    col = self._rng.randint(0, 9)
                    start_row = self._rng.randint(1, 11 - size)
                    coordinates = [
                        f"{chr(ord('A') + col)}{start_row + offset}"
                        for offset in range(size)
                    ]
                if occupied.isdisjoint(coordinates):
                    layout[ship_name] = coordinates
                    occupied.update(coordinates)
                    break
        return layout
