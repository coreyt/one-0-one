"""Contracts for authoritative game implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, Sequence

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.session.config import AgentConfig, GameConfig


class GameStateBase(BaseModel):
    """Base runtime state shared by all games."""

    phase: str = "setup"
    round_number: int = 0
    turn_index: int = 0


class VisibleGameState(BaseModel):
    """A player's view of authoritative game state."""

    viewer_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ChannelSpec(BaseModel):
    """A communication channel created by the game runtime."""

    channel_id: str
    channel_type: Literal["public", "team", "private"]
    members: list[str] = Field(default_factory=list)
    description: str = ""


class ActionSpec(BaseModel):
    """A legal action shape exposed to an actor for the current turn."""

    action_type: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class GameAction(BaseModel):
    """A typed action proposed by a player or moderator."""

    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    """Outcome of validating a proposed action."""

    is_valid: bool
    reason: str | None = None
    normalized_action: GameAction | None = None


class GameOutcome(BaseModel):
    """Terminal outcome reported by a game implementation."""

    status: Literal["win", "loss", "draw", "complete"]
    winners: list[str] = Field(default_factory=list)
    losers: list[str] = Field(default_factory=list)
    summary: str = ""


class TurnContext(BaseModel):
    """Turn/phase scheduling information returned by the game runtime."""

    active_actor_ids: list[str] = Field(default_factory=list)
    phase: str = "setup"
    allow_parallel: bool = False
    prompt: str = ""


class ApplyResult(BaseModel):
    """Result of applying a validated action to authoritative state."""

    next_state: GameStateBase
    public_events: list[dict[str, Any]] = Field(default_factory=list)
    private_events: list[dict[str, Any]] = Field(default_factory=list)
    state_delta: dict[str, Any] = Field(default_factory=dict)
    turn_advanced: bool = True


class Game(Protocol):
    """Protocol implemented by all authoritative game plugins."""

    game_type: str

    def initial_state(
        self,
        config: "GameConfig",
        agents: Sequence["AgentConfig"],
    ) -> GameStateBase: ...

    def initial_channels(self, state: GameStateBase) -> list[ChannelSpec]: ...

    def visible_state(
        self,
        state: GameStateBase,
        viewer_id: str,
    ) -> VisibleGameState: ...

    def turn_context(self, state: GameStateBase) -> TurnContext: ...

    def legal_actions(
        self,
        state: GameStateBase,
        actor_id: str,
    ) -> list[ActionSpec]: ...

    def validate_action(
        self,
        state: GameStateBase,
        actor_id: str,
        action: GameAction,
    ) -> ValidationResult: ...

    def apply_action(
        self,
        state: GameStateBase,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult: ...

    def is_terminal(self, state: GameStateBase) -> bool: ...

    def outcome(self, state: GameStateBase) -> GameOutcome | None: ...
