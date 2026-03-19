"""Runtime wrapper for authoritative game plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from src.games.contracts import (
    ActionSpec,
    ApplyResult,
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
    ModerationBackend,
)
from src.games.registry import load_game_from_config

if TYPE_CHECKING:
    from src.session.config import SessionConfig


@runtime_checkable
class TextActionParser(Protocol):
    """Optional protocol for games that can parse raw text into typed actions."""

    def parse_action_text(self, text: str) -> GameAction | None: ...


class GameRuntime:
    """Holds one authoritative game instance and its current state."""

    def __init__(
        self,
        game: Game,
        state: GameStateBase,
        moderation_backend: ModerationBackend,
    ) -> None:
        self.game = game
        self.state = state
        self.moderation_backend = moderation_backend

    @classmethod
    def from_session_config(
        cls,
        config: "SessionConfig",
        llm_backend: ModerationBackend | None = None,
    ) -> "GameRuntime":
        if config.game is None:
            raise ValueError("Session config does not define a game.")
        game = load_game_from_config(config.game)
        state = game.initial_state(config.game, config.agents)
        moderation = config.game.moderation

        deterministic_backend = DeterministicModerationBackend(
            game=game,
            state=state.model_copy(deep=True),
        )

        if moderation.mode == "deterministic":
            backend: ModerationBackend = deterministic_backend
        elif moderation.mode == "llm_moderated":
            if llm_backend is None:
                raise ValueError("llm_backend is required for llm_moderated mode.")
            backend = llm_backend
        elif moderation.mode == "hybrid_audit":
            if llm_backend is None:
                raise ValueError("llm_backend is required for hybrid_audit mode.")
            backend = HybridAuditBackend(
                primary=llm_backend,
                shadow=deterministic_backend,
            )
        else:
            raise ValueError(f"Unsupported moderation mode: {moderation.mode!r}")

        return cls(game=game, state=state, moderation_backend=backend)

    def visible_state(self, viewer_id: str) -> VisibleGameState:
        return self.game.visible_state(self.state, viewer_id)

    def turn_context(self) -> TurnContext:
        return self.game.turn_context(self.state)

    def legal_actions(self, actor_id: str) -> list[ActionSpec]:
        return self.game.legal_actions(self.state, actor_id)

    def validate_action(
        self,
        actor_id: str,
        action: GameAction,
    ) -> ValidationResult:
        return self.game.validate_action(self.state, actor_id, action)

    def apply_action(
        self,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult:
        result = self.game.apply_action(self.state, actor_id, action)
        self.state = result.next_state
        return result

    def parse_action_text(self, text: str) -> GameAction | None:
        if isinstance(self.game, TextActionParser):
            return self.game.parse_action_text(text)
        return None

    def is_terminal(self) -> bool:
        return self.game.is_terminal(self.state)

    def outcome(self) -> GameOutcome | None:
        return self.game.outcome(self.state)
