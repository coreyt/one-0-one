"""
Smart session event models.

Each event type is a distinct Pydantic model. SessionEvent is an
Annotated discriminated union — consumers get fully-typed access
to every field with no data: dict lookups.

Usage:
    from src.session.events import SessionEvent, MessageEvent, TurnEvent

    # Deserialize from raw dict (e.g., JSON sidecar, SSE payload)
    from pydantic import TypeAdapter
    ta = TypeAdapter(SessionEvent)
    event = ta.validate_python(raw_dict)

    # Type-narrowing in handlers
    match event.type:
        case "MESSAGE":
            print(event.channel_id, event.text)  # fully typed
        case "TURN":
            print(event.agent_ids, event.is_parallel)
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class MessageEvent(BaseModel):
    """An agent (or HITL human) sent a message on a channel."""

    type: Literal["MESSAGE"] = "MESSAGE"
    timestamp: datetime
    turn_number: int
    session_id: str
    agent_id: str
    agent_name: str
    model: str
    channel_id: str  # "public", "team_red", "private_nova_rex", etc.
    recipient_id: str | None = None  # set only for private messages
    text: str
    is_parallel: bool = False  # True when generated during a parallel turn


class MonologueEvent(BaseModel):
    """An agent's internal chain-of-thought.

    NEVER injected into any other agent's context. Observer-only.
    Rendered in MonologuePanel (TUI) / Monologue Drawer (GUI).
    """

    type: Literal["MONOLOGUE"] = "MONOLOGUE"
    timestamp: datetime
    turn_number: int
    session_id: str
    agent_id: str
    agent_name: str
    text: str


class TurnEvent(BaseModel):
    """Signals the start of a new turn.

    Consumers (MonologuePanel, turn indicator) should reset/clear
    their state when this event fires.
    """

    type: Literal["TURN"] = "TURN"
    timestamp: datetime
    turn_number: int
    session_id: str
    agent_ids: list[str]  # agents about to speak this turn
    is_parallel: bool = False


class GameStateEvent(BaseModel):
    """Game state mutation after a turn (scores, round, win/loss, etc.)."""

    type: Literal["GAME_STATE"] = "GAME_STATE"
    timestamp: datetime
    turn_number: int
    session_id: str
    updates: dict[str, Any]  # key-value mutations applied this turn
    full_state: dict[str, Any]  # complete game state snapshot


class RuleViolationEvent(BaseModel):
    """An agent's response violated a game rule."""

    type: Literal["RULE_VIOLATION"] = "RULE_VIOLATION"
    timestamp: datetime
    turn_number: int
    session_id: str
    agent_id: str
    rule: str  # human-readable rule description
    violation_text: str  # excerpt of the violating response


class ChannelCreatedEvent(BaseModel):
    """A message channel was initialized at session start."""

    type: Literal["CHANNEL_CREATED"] = "CHANNEL_CREATED"
    timestamp: datetime
    session_id: str
    channel_id: str
    channel_type: Literal["public", "team", "private"]
    members: list[str]  # agent IDs; empty list means all agents


class SessionEndEvent(BaseModel):
    """The session has concluded."""

    type: Literal["SESSION_END"] = "SESSION_END"
    timestamp: datetime
    turn_number: int
    session_id: str
    reason: Literal[
        "max_turns",
        "win_condition",
        "completion_signal",
        "user_ended",
        "error",
    ]
    message: str | None = None  # optional human-readable summary


# ---------------------------------------------------------------------------
# Discriminated union — the single type used everywhere in the codebase
# ---------------------------------------------------------------------------

SessionEvent = Annotated[
    MessageEvent
    | MonologueEvent
    | TurnEvent
    | GameStateEvent
    | RuleViolationEvent
    | ChannelCreatedEvent
    | SessionEndEvent,
    Field(discriminator="type"),
]
