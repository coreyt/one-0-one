"""
Runtime session state models.

SessionState is the live snapshot passed to the orchestrator on every
turn. It is built and maintained by SessionEngine and is read-only
from the orchestrator's perspective (mutations come back via OrchestratorOutput).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.session.config import AgentConfig
from src.session.events import SessionEvent


class AgentState(BaseModel):
    config: AgentConfig
    status: Literal["idle", "thinking", "speaking", "done"] = "idle"
    token_usage: dict[str, int] = Field(default_factory=dict)
    # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}


class GameState(BaseModel):
    round: int = 0
    scores: dict[str, int] = Field(default_factory=dict)  # agent_id → score
    winner: str | None = None
    is_over: bool = False
    eliminated: list[str] = Field(default_factory=list)  # eliminated agent IDs
    custom: dict[str, Any] = Field(default_factory=dict)  # game-specific data
    incidents: list[dict[str, Any]] = Field(default_factory=list)
    """
    LLM errors and timeouts recorded during the session.
    Each entry: {"turn": int, "agent_id": str, "model": str, "type": "timeout"|"error"}
    """


class SessionState(BaseModel):
    session_id: str
    turn_number: int = 0
    game_state: GameState = Field(default_factory=GameState)
    events: list[SessionEvent] = Field(default_factory=list)
    # master event log — ALL channels, ALL event types
    # ChannelRouter filters this per-agent on each turn
    agents: dict[str, AgentState] = Field(default_factory=dict)
    # key: agent_id
    is_paused: bool = False
    end_reason: str | None = None
