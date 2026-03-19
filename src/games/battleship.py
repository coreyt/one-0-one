"""Authoritative Battleship game implementation."""

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


_COORD_RE = re.compile(r"\b([A-Ja-j](?:10|[1-9]))\b")
_FLEET_ORDER = [
    ("Carrier", 5),
    ("Battleship", 4),
    ("Cruiser", 3),
    ("Submarine", 3),
    ("Destroyer", 2),
]
_FIXED_LAYOUTS = [
    {
        "Carrier": ["A1", "A2", "A3", "A4", "A5"],
        "Battleship": ["C1", "C2", "C3", "C4"],
        "Cruiser": ["E1", "E2", "E3"],
        "Submarine": ["G1", "G2", "G3"],
        "Destroyer": ["I1", "I2"],
    },
    {
        "Carrier": ["B1", "B2", "B3", "B4", "B5"],
        "Battleship": ["D1", "D2", "D3", "D4"],
        "Cruiser": ["F1", "F2", "F3"],
        "Submarine": ["H1", "H2", "H3"],
        "Destroyer": ["J1", "J2"],
    },
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

    def initial_state(
        self,
        config: "GameConfig",
        agents: list["AgentConfig"],
    ) -> BattleshipState:
        players = [agent.id for agent in agents if agent.role != "moderator"][:2]
        ship_positions = {
            player_id: _FIXED_LAYOUTS[index]
            for index, player_id in enumerate(players)
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

    @staticmethod
    def _target_player(players: list[str], actor_id: str) -> str:
        return next(player_id for player_id in players if player_id != actor_id)
