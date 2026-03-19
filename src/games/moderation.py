"""Shared moderation backend contract and hybrid audit helpers."""

from __future__ import annotations

from collections.abc import Sequence
import inspect
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from src.games.contracts import ApplyResult, Game, GameAction, GameStateBase
from src.games.moderator_protocol import extract_moderation_payload
from src.providers import CompletionResult


class ModerationDecision(BaseModel):
    """Normalized moderation result returned by any backend."""

    accepted: bool
    moderator_mode: str
    applied_action: GameAction | None = None
    next_state: GameStateBase | None = None
    reason: str | None = None
    state_delta: dict = Field(default_factory=dict)
    public_events: list[dict] = Field(default_factory=list)
    private_events: list[dict] = Field(default_factory=list)

    @classmethod
    def from_apply_result(
        cls,
        *,
        mode: str,
        action: GameAction,
        result: ApplyResult,
    ) -> "ModerationDecision":
        return cls(
            accepted=True,
            moderator_mode=mode,
            applied_action=action,
            next_state=result.next_state,
            reason=None,
            state_delta=result.state_delta,
            public_events=result.public_events,
            private_events=result.private_events,
        )


class LLMModerationPayload(BaseModel):
    """Structured moderation payload returned by an LLM moderator adapter."""

    accepted: bool
    applied_action: GameAction | None = None
    next_state: dict[str, Any] | GameStateBase | None = None
    reason: str | None = None
    state_delta: dict = Field(default_factory=dict)
    public_events: list[dict] = Field(default_factory=list)
    private_events: list[dict] = Field(default_factory=list)


@runtime_checkable
class ModerationBackend(Protocol):
    """Common backend interface for deterministic and LLM-moderated play."""

    def moderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision: ...

    async def amoderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision | "HybridAuditRecord": ...


@runtime_checkable
class ModeratorCallable(Protocol):
    """Provider-agnostic moderator callable used by the LLM backend."""

    def __call__(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
        state: GameStateBase,
        game: Game,
    ) -> ModerationDecision | CompletionResult | dict[str, Any] | Any: ...


class DeterministicModerationBackend:
    """Code-based moderation adapter over an authoritative game implementation."""

    def __init__(self, *, game: Game, state: GameStateBase) -> None:
        self.game = game
        self.state = state

    def moderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision:
        validation = self.game.validate_action(self.state, actor_id, proposed_action)
        if not validation.is_valid or validation.normalized_action is None:
            return ModerationDecision(
                accepted=False,
                moderator_mode="deterministic",
                next_state=self.state,
                reason=validation.reason,
            )

        result = self.game.apply_action(self.state, actor_id, validation.normalized_action)
        self.state = result.next_state
        return ModerationDecision.from_apply_result(
            mode="deterministic",
            action=validation.normalized_action,
            result=result,
        )

    async def amoderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision:
        return self.moderate_turn(actor_id=actor_id, proposed_action=proposed_action)


class LLMModerationBackend:
    """Callable-backed moderator adapter for LLM-moderated experiments."""

    def __init__(
        self,
        *,
        game: Game,
        state: GameStateBase,
        moderator_callable: ModeratorCallable,
    ) -> None:
        self.game = game
        self.state = state
        self.moderator_callable = moderator_callable

    def moderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision:
        raw_decision = self.moderator_callable(
            actor_id=actor_id,
            proposed_action=proposed_action,
            state=self.state,
            game=self.game,
        )
        if inspect.isawaitable(raw_decision):
            raise RuntimeError("Async moderator callables must be used via amoderate_turn().")
        return self._finalize_decision(
            raw_decision=raw_decision,
            actor_id=actor_id,
            proposed_action=proposed_action,
        )

    async def amoderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision:
        raw_decision = self.moderator_callable(
            actor_id=actor_id,
            proposed_action=proposed_action,
            state=self.state,
            game=self.game,
        )
        if inspect.isawaitable(raw_decision):
            raw_decision = await raw_decision
        return self._finalize_decision(
            raw_decision=raw_decision,
            actor_id=actor_id,
            proposed_action=proposed_action,
        )

    def _finalize_decision(
        self,
        *,
        raw_decision: ModerationDecision | CompletionResult | dict[str, Any],
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision:
        decision = self._normalize_decision(raw_decision)
        resolved_action = decision.applied_action or proposed_action

        if decision.next_state is not None:
            self.state = decision.next_state
            return decision.model_copy(update={"applied_action": resolved_action})

        if not decision.accepted:
            return decision.model_copy(
                update={
                    "applied_action": resolved_action if decision.applied_action else None,
                    "next_state": self.state,
                }
            )

        validation = self.game.validate_action(self.state, actor_id, resolved_action)
        if not validation.is_valid or validation.normalized_action is None:
            raise ValueError(
                "Moderator accepted an action that cannot be applied without an explicit next_state: "
                f"{validation.reason or 'invalid action'}"
            )

        result = self.game.apply_action(self.state, actor_id, validation.normalized_action)
        self.state = result.next_state
        fallback = ModerationDecision.from_apply_result(
            mode="llm_moderated",
            action=validation.normalized_action,
            result=result,
        )
        return fallback.model_copy(
            update={
                "reason": decision.reason,
                "state_delta": decision.state_delta or fallback.state_delta,
                "public_events": decision.public_events or fallback.public_events,
                "private_events": decision.private_events or fallback.private_events,
            }
        )

    def _normalize_decision(
        self,
        raw_decision: ModerationDecision | CompletionResult | dict[str, Any],
    ) -> ModerationDecision:
        if isinstance(raw_decision, ModerationDecision):
            next_state = self._coerce_next_state(raw_decision.next_state)
            return raw_decision.model_copy(
                update={
                    "moderator_mode": raw_decision.moderator_mode or "llm_moderated",
                    "next_state": next_state,
                }
            )

        payload_source: Any
        if isinstance(raw_decision, CompletionResult):
            payload_source = extract_moderation_payload(raw_decision)
        else:
            payload_source = raw_decision

        try:
            payload = LLMModerationPayload.model_validate(payload_source)
        except Exception as exc:
            raise ValueError(
                "Malformed moderator payload; expected structured moderation decision with an 'accepted' field."
            ) from exc

        return ModerationDecision(
            accepted=payload.accepted,
            moderator_mode="llm_moderated",
            applied_action=payload.applied_action,
            next_state=self._coerce_next_state(payload.next_state),
            reason=payload.reason,
            state_delta=payload.state_delta,
            public_events=payload.public_events,
            private_events=payload.private_events,
        )

    def _coerce_next_state(
        self,
        next_state: dict[str, Any] | GameStateBase | None,
    ) -> GameStateBase | None:
        if next_state is None:
            return None
        if isinstance(next_state, GameStateBase):
            return next_state
        return self.state.__class__.model_validate(next_state)


class ScriptedModerationBackend:
    """Simple scripted backend used for tests and future experiment fixtures."""

    def __init__(self, *, decisions: Sequence[ModerationDecision]) -> None:
        self._decisions = list(decisions)
        self._index = 0

    def moderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision:
        if self._index >= len(self._decisions):
            raise IndexError("No scripted moderation decisions remain.")
        decision = self._decisions[self._index]
        self._index += 1
        return decision

    async def amoderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> ModerationDecision:
        return self.moderate_turn(actor_id=actor_id, proposed_action=proposed_action)


class HybridAuditRecord(BaseModel):
    """Primary + shadow moderation results for one audited turn."""

    primary: ModerationDecision
    shadow: ModerationDecision | None = None
    diverged: bool = False


class HybridAuditBackend:
    """Runs a primary moderation backend with an optional shadow comparator."""

    def __init__(
        self,
        *,
        primary: ModerationBackend,
        shadow: ModerationBackend | None = None,
    ) -> None:
        self.primary = primary
        self.shadow = shadow

    def moderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> HybridAuditRecord:
        primary = self.primary.moderate_turn(
            actor_id=actor_id,
            proposed_action=proposed_action,
        )
        shadow = None
        if self.shadow is not None:
            shadow = self.shadow.moderate_turn(
                actor_id=actor_id,
                proposed_action=proposed_action,
            )
            self._resynchronize_shadow(primary)
        return HybridAuditRecord(
            primary=primary,
            shadow=shadow,
            diverged=self._has_diverged(primary, shadow),
        )

    async def amoderate_turn(
        self,
        *,
        actor_id: str,
        proposed_action: GameAction,
    ) -> HybridAuditRecord:
        primary = await moderate_turn_async(
            self.primary,
            actor_id=actor_id,
            proposed_action=proposed_action,
        )
        shadow = None
        if self.shadow is not None:
            shadow = await moderate_turn_async(
                self.shadow,
                actor_id=actor_id,
                proposed_action=proposed_action,
            )
            self._resynchronize_shadow(primary)
        return HybridAuditRecord(
            primary=primary,
            shadow=shadow,
            diverged=self._has_diverged(primary, shadow),
        )

    @staticmethod
    def _has_diverged(
        primary: ModerationDecision,
        shadow: ModerationDecision | None,
    ) -> bool:
        if shadow is None:
            return False
        if primary.accepted != shadow.accepted:
            return True
        if primary.applied_action != shadow.applied_action:
            return True
        if primary.next_state != shadow.next_state:
            return True
        return False

    def _resynchronize_shadow(self, primary: ModerationDecision) -> None:
        if self.shadow is None or primary.next_state is None:
            return
        if not hasattr(self.shadow, "state"):
            return
        self.shadow.state = primary.next_state.model_copy(deep=True)


async def moderate_turn_async(
    backend: ModerationBackend,
    *,
    actor_id: str,
    proposed_action: GameAction,
) -> ModerationDecision | HybridAuditRecord:
    return await backend.amoderate_turn(
        actor_id=actor_id,
        proposed_action=proposed_action,
    )
