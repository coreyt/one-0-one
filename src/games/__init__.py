"""First-class game engine contracts for authoritative game plugins."""

from src.games.battleship import BattleshipGame, BattleshipState
from src.games.connect_four import ConnectFourGame, ConnectFourState
from src.games.mafia import MafiaGame, MafiaState
from src.games.contracts import (
    ActionSpec,
    ApplyResult,
    ChannelSpec,
    Game,
    GameAction,
    GameOutcome,
    GameStateBase,
    TurnContext,
    ValidationResult,
    VisibleGameState,
)
from src.games.moderation import (
    DeterministicModerationBackend,
    HybridAuditBackend,
    HybridAuditRecord,
    LLMModerationBackend,
    ModerationBackend,
    ModerationDecision,
    ScriptedModerationBackend,
)
from src.games.registry import load_game, load_game_from_config
from src.games.runtime import GameRuntime

__all__ = [
    "ActionSpec",
    "ApplyResult",
    "BattleshipGame",
    "BattleshipState",
    "ChannelSpec",
    "ConnectFourGame",
    "ConnectFourState",
    "DeterministicModerationBackend",
    "Game",
    "GameAction",
    "GameOutcome",
    "HybridAuditBackend",
    "HybridAuditRecord",
    "LLMModerationBackend",
    "MafiaGame",
    "MafiaState",
    "ModerationBackend",
    "ModerationDecision",
    "ScriptedModerationBackend",
    "GameStateBase",
    "TurnContext",
    "ValidationResult",
    "VisibleGameState",
    "GameRuntime",
    "load_game",
    "load_game_from_config",
]
