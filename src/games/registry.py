"""Game plugin registry."""

from __future__ import annotations

from src.games.battleship import BattleshipGame
from src.games.connect_four import ConnectFourGame
from src.games.mafia import MafiaGame
from src.games.contracts import Game
from src.session.config import GameConfig


def load_game(plugin_name: str) -> Game:
    """Load a built-in authoritative game plugin by name."""
    normalized = plugin_name.strip().lower().replace(" ", "_").replace("-", "_")
    if normalized == "battleship":
        return BattleshipGame()
    if normalized == "mafia":
        return MafiaGame()
    if normalized in {"connect_four", "connect4"}:
        return ConnectFourGame()
    raise ValueError(f"Unknown game plugin: {plugin_name!r}")


def load_game_from_config(config: GameConfig) -> Game:
    """Resolve a game plugin from explicit config or game name."""
    return load_game(config.plugin or config.name)
