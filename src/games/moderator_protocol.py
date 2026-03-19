"""Prompt/response contract helpers for LLM-moderated game turns."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from src.games.contracts import ActionSpec, GameAction, GameStateBase, VisibleGameState
from src.providers import CompletionResult


class ModeratorTurnRequest(BaseModel):
    """Normalized turn request passed to an LLM moderator."""

    game_type: str
    actor_id: str
    proposed_action: GameAction
    state: GameStateBase
    visible_state: VisibleGameState
    legal_actions: list[ActionSpec] = Field(default_factory=list)


def build_moderation_messages(request: ModeratorTurnRequest) -> list[dict[str, str]]:
    """Build the provider-facing moderation prompt for one game turn."""

    system_content = (
        "You are the authoritative game moderator.\n"
        "Respond with JSON only.\n"
        "Required field: accepted.\n"
        "Optional fields: reason, applied_action, next_state, state_delta, public_events, private_events.\n"
        "If you accept the move and do not provide next_state, the engine will attempt to apply applied_action "
        "or the proposed_action against the authoritative game plugin.\n"
        "If you provide next_state, it must be a complete game state object."
    )
    user_payload = {
        "game_type": request.game_type,
        "actor_id": request.actor_id,
        "proposed_action": request.proposed_action.model_dump(),
        "visible_state": request.visible_state.model_dump(),
        "legal_actions": [spec.model_dump() for spec in request.legal_actions],
        "state": request.state.model_dump(),
    }
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True, indent=2)},
    ]


def extract_moderation_payload(result: CompletionResult) -> dict[str, Any]:
    """Extract a structured moderation payload from a provider result."""

    payload = result.metadata.get("moderation_decision")
    if isinstance(payload, dict):
        return payload
    if result.text.strip():
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "CompletionResult did not include a structured moderation_decision payload."
            ) from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("CompletionResult did not include a structured moderation_decision payload.")
