"""
SessionEngine — the main async session loop.

Responsibilities:
    1. Initialize session state from config
    2. Emit ChannelCreatedEvents for all defined channels
    3. Inject topic + XML routing instructions into each agent's system prompt
      (done via ChannelRouter — system prompt is built fresh each turn)
    4. Load agent memory stubs (no-ops until issue #1)
    5. Run the session loop:
       a. Call orchestrator → OrchestratorOutput
       b. Handle rule violations (re-prompt violating agents)
       c. Emit TurnEvent
       d. Run next agents (sequential or parallel via asyncio.gather)
       e. Parse responses → emit MessageEvent, MonologueEvent, etc.
    6. Save agent memory stubs
    7. Emit SessionEndEvent
    8. Flush TranscriptWriter

Usage:
    from src.session.engine import SessionEngine
    from src.session.event_bus import EventBus
    from src.session.config import load_session_config

    config = load_session_config(Path("session-templates/game-20-questions.yaml"))
    bus = EventBus()
    engine = SessionEngine(config, bus)
    final_state = await engine.run()
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from src.channels.router import ChannelRouter
from src.games import (
    GameAction,
    GameRuntime,
    HybridAuditRecord,
    LLMModerationBackend,
    ModerationDecision,
)
from src.games.moderation import moderate_turn_async
from src.games.moderator_protocol import ModeratorTurnRequest, build_moderation_messages
from src.games.registry import load_game_from_config
from src.logging import get_logger
from src.memory import load_memory, save_memory
from src.orchestrators import OrchestratorInput, load_orchestrator
from src.personas import assign_random_personalities
from src.providers import CommunicationSegment, ProviderError
from src.providers.litellm_client import LiteLLMClient
from src.response_parser import ResponseParser
from src.session.config import SessionConfig
from src.session.event_bus import EventBus
from src.session.events import (
    ChannelCreatedEvent,
    GameStateEvent,
    HybridAuditEvent,
    IncidentEvent,
    MessageEvent,
    MonologueEvent,
    RuleViolationEvent,
    SessionEndEvent,
    TurnEvent,
)
from src.session.state import AgentState, GameState, SessionState
from src.transcript.writer import TranscriptWriter

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# Maximum retries for a rule-violating agent before skipping their turn
_MAX_VIOLATION_RETRIES = 2
_PROVIDER_BACKOFF_SCHEDULE_SECONDS = [2, 4, 8, 16, 32, 64, 128]
# After this many consecutive retryable errors for the same agent the session
# ends rather than looping at the maximum backoff interval forever.
_MAX_CONSECUTIVE_PROVIDER_ERRORS = len(_PROVIDER_BACKOFF_SCHEDULE_SECONDS)


class SessionEngine:
    """Drives a complete multi-agent conversation session."""

    def __init__(self, config: SessionConfig, bus: EventBus) -> None:
        # Assign random personalities using seconds-since-2000-01-01-UTC as seed
        # unless the session opts out or is a plugin-backed deterministic game.
        if self._should_auto_assign_personalities(config):
            _epoch_2000 = datetime(2000, 1, 1, tzinfo=UTC)
            _seed = int((datetime.now(UTC) - _epoch_2000).total_seconds())
            try:
                config = assign_random_personalities(config, seed=_seed)
                log.info("session.personalities_assigned", seed=_seed)
            except ValueError as exc:
                log.warning("session.personalities_skipped", reason=str(exc))
        else:
            log.info("session.personalities_disabled")

        self._config = config
        self._bus = bus
        self._session_id = str(uuid.uuid4())
        self._router = ChannelRouter(config)
        self._parser = ResponseParser()
        self._provider = LiteLLMClient()
        self._orchestrate = load_orchestrator(config.orchestrator)
        self._transcript = TranscriptWriter(config)
        self._game_runtime = (
            self._build_game_runtime(config)
            if self._uses_engine_authoritative_game(config)
            else None
        )
        self._pending_hitl_turn_inputs: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self._awaiting_hitl_turn = False
        # Pause gate — cleared when paused, set when running
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._state: SessionState | None = None
        # Track private channel IDs already announced via CHANNEL_CREATED events
        self._announced_channels: set[str] = set()

    @staticmethod
    def _should_auto_assign_personalities(config: SessionConfig) -> bool:
        if config.auto_assign_personalities is not None:
            return config.auto_assign_personalities
        return not SessionEngine._uses_engine_authoritative_game(config)

    @staticmethod
    def _uses_engine_authoritative_game(config: SessionConfig) -> bool:
        return bool(
            config.type == "games"
            and config.game is not None
            and config.game.authority_mode == "engine_authoritative"
            and config.game.plugin
        )

    async def run(self) -> SessionState:
        """
        Execute the full session. Returns the final SessionState.
        Emits events to the bus throughout — consumers attach via bus.stream().
        """
        # Bind structlog context vars for the duration of this session
        structlog.contextvars.bind_contextvars(
            session_id=self._session_id,
            title=self._config.title,
        )

        log.info(
            "session.started",
            template=self._config.title,
            setting=self._config.setting,
            agent_count=len(self._config.agents),
        )

        state = self._init_state()
        self._state = state  # expose for pause/resume/inject

        # Emit channel creation events
        self._emit_channel_events(state)
        self._emit_initial_game_state(state)

        # Load agent memory (no-ops until issue #1)
        for agent in self._config.agents:
            _memory = load_memory(agent.id)
            # Memory will be injected into context in a future iteration

        # Main session loop
        try:
            state = await self._run_loop(state)
        except Exception as exc:
            log.exception("session.error", error=str(exc))
            end_event = self._make_end_event(state, "error")
            self._emit_event(end_event, state)
        finally:
            # Save memory stubs (no-ops)
            for agent in self._config.agents:
                save_memory(agent.id, self._session_id, {})
            # Flush transcript
            await self._transcript.flush()
            structlog.contextvars.clear_contextvars()

        return state

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_state(self) -> SessionState:
        agents = {
            a.id: AgentState(config=a) for a in self._config.agents
        }
        state = SessionState(
            session_id=self._session_id,
            turn_number=0,
            game_state=GameState(),
            events=[],
            agents=agents,
        )
        if self._game_runtime is not None:
            self._sync_authoritative_game_state(state)
        return state

    def _emit_channel_events(self, state: SessionState) -> None:
        def announce_channel(
            channel_id: str,
            channel_type: str,
            members: list[str],
        ) -> None:
            if channel_id in self._announced_channels:
                return
            event = ChannelCreatedEvent(
                timestamp=datetime.now(UTC),
                session_id=self._session_id,
                channel_id=channel_id,
                channel_type=channel_type,
                members=members,
            )
            self._emit_event(event, state)
            self._announced_channels.add(channel_id)

        # Always emit the public channel
        announce_channel("public", "public", [])

        # Emit configured team/private channels
        for ch in self._config.channels:
            announce_channel(ch.id, ch.type, ch.members)

        if self._game_runtime is not None:
            for ch in self._game_runtime.game.initial_channels(self._game_runtime.state):
                announce_channel(ch.channel_id, ch.channel_type, ch.members)

    def _emit_initial_game_state(self, state: SessionState) -> None:
        if self._game_runtime is None:
            return
        payload = self._game_runtime.state.model_dump()
        event = GameStateEvent(
            timestamp=datetime.now(UTC),
            turn_number=state.turn_number,
            session_id=self._session_id,
            updates=payload,
            full_state=state.game_state.model_dump(),
        )
        self._emit_event(event, state)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    # Maximum consecutive empty attempts on the same turn before forcing advancement.
    # Prevents infinite loops when all providers are down.
    _MAX_EMPTY_ATTEMPTS = 3

    async def _run_loop(self, state: SessionState) -> SessionState:
        # Maps turn_number → count of consecutive attempts that produced no output.
        # Reset whenever a turn successfully produces a MESSAGE or MONOLOGUE.
        _empty_attempts: dict[int, int] = {}

        while True:
            await self._honor_provider_backoff(state)
            # Honour pause — waits here until resume() sets the event
            await self._resume_event.wait()

            if state.end_reason is not None:
                break

            pending_presentation_agent_id = self._pending_presentation_agent_id(state)
            if (
                self._game_runtime is not None
                and self._game_runtime.is_terminal()
                and pending_presentation_agent_id is None
            ):
                end_event = self._make_end_event(state, "win_condition")
                self._emit_event(end_event, state)
                state.end_reason = end_event.reason
                log.info(
                    "session.ended",
                    reason=end_event.reason,
                    turns=state.turn_number,
                    source="authoritative_game_runtime",
                )
                break

            if (
                self._config.max_turns is not None
                and state.turn_number >= self._config.max_turns
            ):
                end_event = self._make_end_event(state, "max_turns")
                self._emit_event(end_event, state)
                state.end_reason = end_event.reason
                log.info(
                    "session.ended",
                    reason=end_event.reason,
                    turns=state.turn_number,
                    source="engine_max_turns_cap",
                )
                break

            orch_input = OrchestratorInput(config=self._config, state=state)
            orch_output = self._orchestrate(orch_input)

            if pending_presentation_agent_id is not None:
                orch_output.next_agents = [pending_presentation_agent_id]
                orch_output.advance_turns = 0
                orch_output.session_end = False
                orch_output.end_reason = None
            elif self._game_runtime is not None:
                turn_context = self._game_runtime.turn_context()
                if turn_context.active_actor_ids:
                    orch_output.next_agents = turn_context.active_actor_ids
                    orch_output.advance_turns = len(turn_context.active_actor_ids)
                if orch_output.session_end and orch_output.end_reason != "max_turns":
                    orch_output.session_end = False
                    orch_output.end_reason = None

            if orch_output.session_end:
                end_event = self._make_end_event(
                    state, orch_output.end_reason or "max_turns"
                )
                self._emit_event(end_event, state)
                state.end_reason = end_event.reason
                log.info(
                    "session.ended",
                    reason=end_event.reason,
                    turns=state.turn_number,
                )
                break

            # Handle wait_for_hitl — apply game state, pause, then
            # re-enter the loop (which blocks at resume_event.wait())
            if orch_output.wait_for_hitl:
                if orch_output.game_state_updates:
                    for k, v in orch_output.game_state_updates.items():
                        state.game_state.custom[k] = v
                    gs_event = GameStateEvent(
                        timestamp=datetime.now(UTC),
                        turn_number=state.turn_number,
                        session_id=self._session_id,
                        updates=orch_output.game_state_updates,
                        full_state=state.game_state.model_dump(),
                    )
                    self._emit_event(gs_event, state)
                log.info(
                    "session.waiting_for_hitl",
                    turn=state.turn_number,
                )
                self._resume_event.clear()
                continue

            # Handle rule violations from previous turn
            for violation in orch_output.rule_violations:
                rv_event = RuleViolationEvent(
                    timestamp=datetime.now(UTC),
                    turn_number=state.turn_number,
                    session_id=self._session_id,
                    agent_id=violation.agent_id,
                    rule=violation.rule,
                    violation_text=violation.violation_text,
                )
                self._emit_event(rv_event, state)

            # Apply game state updates and emit GameStateEvent
            if orch_output.game_state_updates:
                for k, v in orch_output.game_state_updates.items():
                    state.game_state.custom[k] = v
                gs_event = GameStateEvent(
                    timestamp=datetime.now(UTC),
                    turn_number=state.turn_number,
                    session_id=self._session_id,
                    updates=orch_output.game_state_updates,
                    full_state=state.game_state.model_dump(),
                )
                self._emit_event(gs_event, state)

            # Emit TURN event
            is_parallel = len(orch_output.next_agents) > 1
            turn_event = TurnEvent(
                timestamp=datetime.now(UTC),
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_ids=orch_output.next_agents,
                is_parallel=is_parallel,
            )
            self._emit_event(turn_event, state)

            log.info(
                "turn.started",
                turn=state.turn_number,
                agents=orch_output.next_agents,
                parallel=is_parallel,
            )

            # Snapshot event count before running agents so we can detect timeouts
            events_before = len(state.events)

            # Run agent(s)
            if is_parallel:
                await asyncio.gather(
                    *[
                        (
                            self._run_human_turn(agent_id, state, is_parallel=True)
                            if self._is_human_player_agent(agent_id)
                            else self._run_agent(agent_id, state, is_parallel=True)
                        )
                        for agent_id in orch_output.next_agents
                    ]
                )
            else:
                for agent_id in orch_output.next_agents:
                    if self._is_human_player_agent(agent_id):
                        await self._run_human_turn(agent_id, state, is_parallel=False)
                    else:
                        await self._run_agent(agent_id, state, is_parallel=False)

            # Only advance the turn counter if at least one agent produced output
            # (a MESSAGE or MONOLOGUE event).  A pure timeout/error produces neither,
            # so the turn budget is not consumed.
            # Safety valve: if the same turn has failed _MAX_EMPTY_ATTEMPTS times in a
            # row (all providers down), force advancement to avoid an infinite loop.
            new_events = state.events[events_before:]
            retry_pending = bool(state.game_state.custom.pop("_retry_turn_pending", False))
            provider_backoff_pending = bool(state.game_state.custom.get("_pending_provider_backoff"))
            produced_output = any(
                e.type in ("MESSAGE", "MONOLOGUE") for e in new_events
            )
            if retry_pending:
                _empty_attempts.pop(state.turn_number, None)
                advance = 0
            elif provider_backoff_pending:
                _empty_attempts.pop(state.turn_number, None)
                advance = 0
            elif produced_output:
                _empty_attempts.pop(state.turn_number, None)
                advance = orch_output.advance_turns
            else:
                attempts = _empty_attempts.get(state.turn_number, 0) + 1
                _empty_attempts[state.turn_number] = attempts
                if attempts >= self._MAX_EMPTY_ATTEMPTS:
                    log.warning(
                        "turn.forced_advance",
                        turn=state.turn_number,
                        empty_attempts=attempts,
                    )
                    _empty_attempts.pop(state.turn_number, None)
                    advance = orch_output.advance_turns
                else:
                    advance = 0
            state.turn_number += advance
            # Increment round counter after each full rotation through all agents
            if self._game_runtime is None:
                agent_count = len(self._config.agents)
                if agent_count > 0 and state.turn_number % agent_count == 0:
                    state.game_state.round += 1
            log.info("turn.completed", turn=state.turn_number - advance)

        return state

    # ------------------------------------------------------------------
    # Per-agent execution
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        agent_id: str,
        state: SessionState,
        is_parallel: bool = False,
    ) -> None:
        """Call LLM for one agent and emit resulting events."""
        agent_config = self._config.agents[
            next(i for i, a in enumerate(self._config.agents) if a.id == agent_id)
        ]
        agent_state = state.agents[agent_id]
        agent_state.status = "thinking"

        # Build context for this agent
        messages = self._router.build_context(agent_id, state)

        # Determine native thinking
        native_thinking = (
            agent_config.monologue
            and agent_config.monologue_mode == "native"
        )
        requested_model = agent_config.requested_model(use_airlock=self._provider.uses_router)

        llm = self._config.llm_defaults
        try:
            result = await self._provider.complete(
                model=requested_model,
                messages=messages,
                temperature=llm.temperature,
                native_thinking=native_thinking,
                thinking_budget_tokens=llm.thinking_budget,
                timeout=llm.timeout,
                provider_hint=agent_config.provider,
                airlock_metadata=agent_config.airlock_metadata or None,
                **({"max_tokens": llm.max_tokens} if llm.max_tokens else {}),
            )
        except ProviderError as exc:
            error_str = str(exc).lower()
            incident_type = "timeout" if ("timeout" in error_str or "timed out" in error_str) else "error"
            log.error(
                "llm.error",
                agent_id=agent_id,
                model=requested_model,
                error=str(exc),
                retryable=exc.retryable,
            )
            state.game_state.incidents.append({
                "turn": state.turn_number,
                "agent_id": agent_id,
                "model": requested_model,
                "type": incident_type,
                "retryable": exc.retryable,
            })
            incident_event = IncidentEvent(
                timestamp=datetime.now(UTC),
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_config.name,
                model=requested_model,
                incident_type=incident_type,
                detail=str(exc),
            )
            self._emit_event(incident_event, state)

            if not exc.retryable:
                # Permanent failure — end the session immediately rather than
                # burning time in a retry loop that will never succeed.
                log.error(
                    "llm.fatal_error",
                    agent_id=agent_id,
                    model=requested_model,
                    reason="non_retryable_provider_error",
                    error=str(exc),
                )
                state.game_state.custom["fatal_error"] = {
                    "agent_id": agent_id,
                    "model": requested_model,
                    "error": str(exc),
                }
                if self._pending_presentation_agent_id(state) == agent_id:
                    self._clear_pending_presentation(state)
                agent_state.status = "idle"
                self._end_session_with_error(state, str(exc))
                return

            self._schedule_provider_backoff(
                state=state,
                agent_id=agent_id,
                agent_name=agent_config.name,
                incident_type=incident_type,
                detail=str(exc),
            )
            if self._pending_presentation_agent_id(state) == agent_id:
                self._clear_pending_presentation(state)
            agent_state.status = "idle"
            return

        agent_state.status = "speaking"
        self._reset_provider_backoff(agent_id, state)

        # Update token usage
        agent_state.token_usage["prompt_tokens"] = (
            agent_state.token_usage.get("prompt_tokens", 0) + result.usage.prompt_tokens
        )
        agent_state.token_usage["completion_tokens"] = (
            agent_state.token_usage.get("completion_tokens", 0) + result.usage.completion_tokens
        )

        # Parse XML tags for legacy/prompt-fallback sessions.
        parsed = self._parser.parse(result.text, agent_name=agent_config.name)
        parsed_communication_segments = self._parser.build_communication_segments(parsed)
        parsed_monologue_segments = self._parser.build_monologue_segments(parsed)
        communication_segments = (
            parsed_communication_segments
            if parsed.tags_found
            else (result.communication or parsed_communication_segments)
        )
        should_apply_authoritative_action = self._should_apply_authoritative_action(agent_id)
        should_apply_authoritative_message_turn = self._should_apply_authoritative_message_turn(agent_id)
        structured_action = (
            self._extract_structured_action(result, parsed.public_message)
            if should_apply_authoritative_action
            else None
        )
        if should_apply_authoritative_action and structured_action is not None:
            communication_segments = [
                CommunicationSegment(
                    visibility="public",
                    text=json.dumps(structured_action),
                )
            ]
        monologue_segments = list(result.monologue)
        seen_monologue_texts = {segment.text for segment in monologue_segments}
        for segment in parsed_monologue_segments:
            if segment.text not in seen_monologue_texts:
                monologue_segments.append(segment)
                seen_monologue_texts.add(segment.text)

        # Handle elimination signals from any agent (typically the Narrator)
        newly_eliminated: list[str] = []
        if parsed.eliminated_agents:
            for eid in parsed.eliminated_agents:
                if eid not in state.game_state.eliminated:
                    state.game_state.eliminated.append(eid)
                    newly_eliminated.append(eid)
                    log.info(
                        "game.agent_eliminated",
                        agent_id=eid,
                        signaled_by=agent_id,
                        turn=state.turn_number,
                    )

        now = datetime.now(UTC)

        # Emit a GAME_STATE event for each new elimination so orchestrators can
        # detect that a player was removed since the narrator last spoke.
        for eid in newly_eliminated:
            elim_event = GameStateEvent(
                timestamp=now,
                turn_number=state.turn_number,
                session_id=self._session_id,
                updates={"newly_eliminated": eid},
                full_state=state.game_state.model_dump(),
            )
            self._emit_event(elim_event, state)

        # Emit MONOLOGUE events (observer-only — never added to other agents' context)
        for segment in monologue_segments:
            mono_event = MonologueEvent(
                timestamp=now,
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_config.name,
                text=segment.text,
            )
            self._emit_event(mono_event, state)
            log.debug(
                "agent.monologue",
                agent=agent_config.name,
                source=segment.source,
                text=segment.text[:120],
                turn=state.turn_number,
            )

        for segment in communication_segments:
            self._emit_communication_segment(
                segment=segment,
                agent_id=agent_id,
                agent_name=agent_config.name,
                model=result.model,
                turn_number=state.turn_number,
                is_parallel=is_parallel,
                timestamp=now,
                state=state,
                team_channel_id=agent_config.team,
            )

        if should_apply_authoritative_action:
            await self._apply_authoritative_game_action(
                agent_id=agent_id,
                agent_name=agent_config.name,
                state=state,
                parsed_action=structured_action,
                parsed_public_message=parsed.public_message,
                timestamp=now,
            )
        elif should_apply_authoritative_message_turn:
            await self._apply_authoritative_message_turn(
                agent_id=agent_id,
                agent_name=agent_config.name,
                state=state,
                parsed_public_message=parsed.public_message,
                timestamp=now,
            )
        elif self._pending_presentation_agent_id(state) == agent_id:
            self._clear_pending_presentation(state)

        agent_state.status = "idle"

    async def _run_human_turn(
        self,
        agent_id: str,
        state: SessionState,
        is_parallel: bool = False,
    ) -> None:
        """Wait for and process a human-controlled player-seat turn."""
        agent_config = self._config.agents[
            next(i for i, a in enumerate(self._config.agents) if a.id == agent_id)
        ]
        agent_state = state.agents[agent_id]
        agent_state.status = "awaiting_human"
        self._awaiting_hitl_turn = True
        self._resume_event.clear()
        state.is_paused = True
        log.info(
            "session.waiting_for_hitl_turn",
            agent_id=agent_id,
            turn=state.turn_number,
        )

        try:
            payload = await self._pending_hitl_turn_inputs.get()
        finally:
            self._awaiting_hitl_turn = False
            self._resume_event.set()
            state.is_paused = False

        text = payload["text"].strip()
        channel_id = payload["channel_id"]
        if not text:
            if self._pending_presentation_agent_id(state) == agent_id:
                self._clear_pending_presentation(state)
            agent_state.status = "idle"
            return

        now = datetime.now(UTC)
        event = MessageEvent(
            timestamp=now,
            turn_number=state.turn_number,
            session_id=self._session_id,
            agent_id=agent_id,
            agent_name=agent_config.name,
            model="human",
            channel_id=channel_id,
            text=text,
            is_parallel=is_parallel,
        )
        self._emit_event(event, state)
        log.info(
            "session.hitl_turn_message",
            agent_id=agent_id,
            channel_id=channel_id,
            turn=state.turn_number,
        )

        if self._should_apply_authoritative_action(agent_id) and channel_id == "public":
            await self._apply_authoritative_game_action(
                agent_id=agent_id,
                agent_name=agent_config.name,
                state=state,
                parsed_action=self._extract_structured_action(None, text),
                parsed_public_message=text,
                timestamp=now,
            )
        elif self._should_apply_authoritative_message_turn(agent_id) and channel_id in {"public", agent_config.team or ""}:
            await self._apply_authoritative_message_turn(
                agent_id=agent_id,
                agent_name=agent_config.name,
                state=state,
                parsed_public_message=text,
                timestamp=now,
            )
        elif self._pending_presentation_agent_id(state) == agent_id:
            self._clear_pending_presentation(state)

        agent_state.status = "idle"

    # ------------------------------------------------------------------
    # Control API (for UI layers)
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """Pause the session after the current turn completes."""
        self._resume_event.clear()
        if self._state is not None:
            self._state.is_paused = True
        log.info("session.paused", session_id=self._session_id)

    def resume(self) -> None:
        """Resume a paused session."""
        if self._state is not None:
            self._state.is_paused = False
        self._resume_event.set()
        log.info("session.resumed", session_id=self._session_id)

    def inject_hitl_message(self, text: str, channel_id: str = "public") -> None:
        """
        Inject a human-in-the-loop message into the session.

        Creates a MessageEvent with agent_id="hitl" and emits it to the bus
        so it is included in subsequent agent contexts via the ChannelRouter.
        """
        if self._state is None:
            return
        if self._is_hitl_player_mode():
            if not self._awaiting_hitl_turn:
                log.info(
                    "session.hitl_input_ignored",
                    reason="not_waiting_for_human_turn",
                    channel_id=channel_id,
                )
                return
            self._pending_hitl_turn_inputs.put_nowait(
                {"text": text, "channel_id": channel_id}
            )
            return
        now = datetime.now(UTC)
        event = MessageEvent(
            timestamp=now,
            turn_number=self._state.turn_number,
            session_id=self._session_id,
            agent_id="hitl",
            agent_name=self._config.hitl.role or "Human",
            model="human",
            channel_id=channel_id,
            text=text,
            is_parallel=False,
        )
        self._emit_event(event, self._state)
        log.info(
            "session.hitl_message",
            session_id=self._session_id,
            channel_id=channel_id,
            length=len(text),
        )
        # Auto-resume if the engine is paused (e.g. waiting for HITL input)
        if not self._resume_event.is_set():
            self._resume_event.set()
            log.info("session.hitl_auto_resume", session_id=self._session_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_end_event(self, state: SessionState, reason: str) -> SessionEndEvent:
        valid_reasons = {
            "max_turns", "win_condition", "completion_signal", "user_ended", "error"
        }
        safe_reason = reason if reason in valid_reasons else "error"
        return SessionEndEvent(
            timestamp=datetime.now(UTC),
            turn_number=state.turn_number,
            session_id=self._session_id,
            reason=safe_reason,  # type: ignore[arg-type]
        )

    def _end_session_with_error(self, state: SessionState, detail: str) -> None:
        """Emit SESSION_END with reason='error' and set state.end_reason.

        Called when a provider error is determined to be unrecoverable so the
        main loop can exit cleanly on its next iteration.
        """
        end_event = self._make_end_event(state, "error")
        end_event.message = detail[:200]
        self._emit_event(end_event, state)
        state.end_reason = "error"
        log.error(
            "session.ended",
            reason="error",
            turns=state.turn_number,
            detail=detail[:200],
        )

    async def _honor_provider_backoff(self, state: SessionState) -> None:
        pending = state.game_state.custom.get("_pending_provider_backoff")
        if not isinstance(pending, dict):
            return
        delay = pending.get("delay_seconds")
        if not isinstance(delay, int) or delay <= 0:
            state.game_state.custom.pop("_pending_provider_backoff", None)
            return
        state.game_state.custom.pop("_pending_provider_backoff", None)
        self._resume_event.clear()
        state.is_paused = True
        log.warning(
            "session.paused_for_provider_backoff",
            delay_seconds=delay,
            agent_id=pending.get("agent_id"),
            session_id=self._session_id,
        )
        await asyncio.sleep(delay)
        state.is_paused = False
        self._resume_event.set()
        log.info(
            "session.resumed_after_provider_backoff",
            delay_seconds=delay,
            agent_id=pending.get("agent_id"),
            session_id=self._session_id,
        )

    def _is_hitl_player_mode(self) -> bool:
        return (
            self._config.hitl.enabled
            and self._config.type == "games"
            and self._config.hitl.mode == "player"
            and self._config.hitl.participant_agent_id is not None
        )

    @staticmethod
    def _next_provider_backoff_seconds(attempt: int) -> int:
        index = min(max(attempt - 1, 0), len(_PROVIDER_BACKOFF_SCHEDULE_SECONDS) - 1)
        return _PROVIDER_BACKOFF_SCHEDULE_SECONDS[index]

    def _schedule_provider_backoff(
        self,
        *,
        state: SessionState,
        agent_id: str,
        agent_name: str,
        incident_type: str,
        detail: str,
    ) -> None:
        attempts = state.game_state.custom.setdefault("provider_error_counts", {})
        attempt = int(attempts.get(agent_id, 0)) + 1
        attempts[agent_id] = attempt

        if attempt > _MAX_CONSECUTIVE_PROVIDER_ERRORS:
            # Retryable error but the backoff schedule is exhausted — give up
            # rather than looping at the maximum delay indefinitely.
            log.error(
                "llm.fatal_error",
                agent_id=agent_id,
                reason="max_consecutive_provider_errors_exceeded",
                attempts=attempt,
            )
            state.game_state.custom["fatal_error"] = {
                "agent_id": agent_id,
                "error": detail,
                "attempts": attempt,
            }
            self._end_session_with_error(state, detail)
            return

        delay = self._next_provider_backoff_seconds(attempt)
        state.game_state.custom["_pending_provider_backoff"] = {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "incident_type": incident_type,
            "detail": detail,
            "delay_seconds": delay,
            "attempt": attempt,
        }

    @staticmethod
    def _reset_provider_backoff(agent_id: str, state: SessionState) -> None:
        attempts = state.game_state.custom.setdefault("provider_error_counts", {})
        attempts[agent_id] = 0

    def _is_human_player_agent(self, agent_id: str) -> bool:
        return self._is_hitl_player_mode() and (
            self._config.hitl.participant_agent_id == agent_id
        )

    def _build_game_runtime(self, config: SessionConfig) -> GameRuntime:
        llm_backend = None
        if (
            config.game is not None
            and config.game.moderation.mode in {"llm_moderated", "hybrid_audit"}
        ):
            llm_backend = self._build_provider_backed_llm_backend(config)
        return GameRuntime.from_session_config(config, llm_backend=llm_backend)

    def _build_provider_backed_llm_backend(
        self,
        config: SessionConfig,
    ) -> LLMModerationBackend:
        if config.game is None:
            raise ValueError("Session config does not define a game.")
        moderator_id = config.game.moderation.moderator_agent_id
        moderator_agent = next(
            (agent for agent in config.agents if agent.id == moderator_id),
            None,
        )
        if moderator_agent is None:
            raise ValueError(
                f"Moderator agent {moderator_id!r} is not defined in session agents."
            )

        game = load_game_from_config(config.game)
        state = game.initial_state(config.game, config.agents)
        llm_defaults = config.llm_defaults

        async def moderator_callable(*, actor_id, proposed_action, state, game):
            request = ModeratorTurnRequest(
                game_type=game.game_type,
                actor_id=actor_id,
                proposed_action=proposed_action,
                state=state,
                visible_state=game.visible_state(state, actor_id),
                legal_actions=game.legal_actions(state, actor_id),
            )
            messages = build_moderation_messages(request)
            kwargs = {
                "model": moderator_agent.requested_model(
                    use_airlock=self._provider.uses_router
                ),
                "messages": messages,
                "temperature": 0.0,
                "native_thinking": False,
                "thinking_budget_tokens": llm_defaults.thinking_budget,
                "timeout": llm_defaults.timeout,
                "provider_hint": moderator_agent.provider,
                "airlock_metadata": moderator_agent.airlock_metadata or None,
            }
            if llm_defaults.max_tokens:
                kwargs["max_tokens"] = llm_defaults.max_tokens
            return await self._provider.complete(**kwargs)

        return LLMModerationBackend(
            game=game,
            state=state,
            moderator_callable=moderator_callable,
        )

    def _sync_authoritative_game_state(self, state: SessionState) -> None:
        if self._game_runtime is None:
            return
        authoritative = self._game_runtime.state.model_dump()
        visible_states = {
            agent.id: self._game_runtime.visible_state(agent.id).model_dump()
            for agent in self._config.agents
        }
        legal_actions = {
            agent.id: [spec.model_dump() for spec in self._game_runtime.legal_actions(agent.id)]
            for agent in self._config.agents
        }
        state.game_state.custom["authoritative_state"] = authoritative
        state.game_state.custom["game_type"] = self._game_runtime.game.game_type
        state.game_state.custom["visible_states"] = visible_states
        state.game_state.custom["legal_actions"] = legal_actions
        state.game_state.round = authoritative.get("round_number", state.game_state.round)
        state.game_state.winner = authoritative.get("winner")
        state.game_state.is_over = self._game_runtime.is_terminal()
        if isinstance(authoritative.get("eliminated"), list):
            state.game_state.eliminated = list(authoritative["eliminated"])

    def _pending_presentation_agent_id(self, state: SessionState) -> str | None:
        pending = state.game_state.custom.get("_pending_presentation_agent_id")
        return pending if isinstance(pending, str) and pending else None

    @staticmethod
    def _clear_pending_presentation(state: SessionState) -> None:
        state.game_state.custom.pop("_pending_presentation_agent_id", None)

    def _presentation_agent_id(self) -> str | None:
        if self._game_runtime is None:
            return None
        moderator = next(
            (agent for agent in self._config.agents if agent.role == "moderator"),
            None,
        )
        return moderator.id if moderator is not None else None

    def _should_apply_authoritative_action(self, agent_id: str) -> bool:
        if self._game_runtime is None:
            return False
        if agent_id not in self._game_runtime.turn_context().active_actor_ids:
            return False
        return bool(self._game_runtime.legal_actions(agent_id))

    def _should_apply_authoritative_message_turn(self, agent_id: str) -> bool:
        if self._game_runtime is None:
            return False
        if agent_id not in self._game_runtime.turn_context().active_actor_ids:
            return False
        return not self._game_runtime.legal_actions(agent_id)

    def _extract_structured_action(
        self,
        result,
        public_text: str,
    ) -> dict[str, object] | None:
        candidates: list[object] = []
        if result is not None and result.parsed_action is not None:
            candidates.append(result.parsed_action)
        stripped = public_text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                candidates.append(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        for candidate in candidates:
            if isinstance(candidate, dict):
                return candidate
        return None

    def _resolve_authoritative_action(
        self,
        parsed_action: dict[str, object] | None,
        parsed_public_message: str,
    ) -> GameAction | None:
        if self._game_runtime is None:
            return None

        if isinstance(parsed_action, dict):
            parser = getattr(self._game_runtime.game, "parse_action_payload", None)
            if callable(parser):
                action = parser(parsed_action)
                if action is not None:
                    return action
            if (
                isinstance(parsed_action.get("action_type"), str)
                and isinstance(parsed_action.get("payload"), dict)
            ):
                return GameAction(
                    action_type=parsed_action["action_type"],
                    payload=parsed_action["payload"],
                )

        return self._game_runtime.parse_action_text(parsed_public_message)

    async def _apply_authoritative_game_action(
        self,
        *,
        agent_id: str,
        agent_name: str,
        state: SessionState,
        parsed_action: dict[str, object] | None,
        parsed_public_message: str,
        timestamp: datetime,
    ) -> None:
        if self._game_runtime is None:
            return
        action = self._resolve_authoritative_action(parsed_action, parsed_public_message)
        if action is None:
            self._handle_moderation_failure(
                agent_id=agent_id,
                agent_name=agent_name,
                state=state,
                timestamp=timestamp,
                violation_text=parsed_public_message,
                reason="Could not parse a legal game action from the player's public move.",
                proposed_action={"raw_text": parsed_public_message},
                failure_category="actor",
            )
            return

        try:
            moderation_result = await moderate_turn_async(
                self._game_runtime.moderation_backend,
                actor_id=agent_id,
                proposed_action=action,
            )
            decision = self._resolve_moderation_decision(
                moderation_result,
                actor_id=agent_id,
                proposed_action=action,
                timestamp=timestamp,
                state=state,
            )
        except (ProviderError, ValueError) as exc:
            self._handle_moderation_failure(
                agent_id=agent_id,
                agent_name=agent_name,
                state=state,
                timestamp=timestamp,
                violation_text=parsed_public_message,
                reason=str(exc),
                proposed_action=action.model_dump(),
                failure_category="moderator",
            )
            return
        if not decision.accepted or decision.next_state is None:
            self._handle_moderation_failure(
                agent_id=agent_id,
                agent_name=agent_name,
                state=state,
                timestamp=timestamp,
                violation_text=parsed_public_message,
                reason=decision.reason or "Invalid game action.",
                proposed_action=action.model_dump(),
                failure_category="actor",
            )
            return

        self._game_runtime.state = decision.next_state
        self._reset_moderation_retry_count(state, agent_id)
        self._commit_authoritative_result(
            state=state,
            agent_id=agent_id,
            agent_name=agent_name,
            timestamp=timestamp,
            state_delta=decision.state_delta,
            public_events=decision.public_events or [],
            private_events=decision.private_events or [],
            action_type=(
                decision.applied_action.action_type
                if decision.applied_action is not None
                else action.action_type
            ),
        )

    async def _apply_authoritative_message_turn(
        self,
        *,
        agent_id: str,
        agent_name: str,
        state: SessionState,
        parsed_public_message: str,
        timestamp: datetime,
    ) -> None:
        if self._game_runtime is None:
            return
        result = self._game_runtime.apply_message_turn(agent_id, parsed_public_message)
        if result is None:
            return
        self._commit_authoritative_result(
            state=state,
            agent_id=agent_id,
            agent_name=agent_name,
            timestamp=timestamp,
            state_delta=result.state_delta,
            public_events=result.public_events,
            private_events=result.private_events,
            action_type="message_turn",
        )

    def _commit_authoritative_result(
        self,
        *,
        state: SessionState,
        agent_id: str,
        agent_name: str,
        timestamp: datetime,
        state_delta: dict[str, object],
        public_events: list[dict],
        private_events: list[dict],
        action_type: str,
    ) -> None:
        if self._game_runtime is None:
            return
        self._sync_authoritative_game_state(state)
        self._emit_game_generated_messages(
            state=state,
            timestamp=timestamp,
            turn_number=state.turn_number,
            public_events=public_events,
            private_events=private_events,
        )
        presentation_agent_id = self._presentation_agent_id()
        if (
            presentation_agent_id is not None
            and presentation_agent_id != agent_id
            and public_events
        ):
            state.game_state.custom["_pending_presentation_agent_id"] = presentation_agent_id
        gs_event = GameStateEvent(
            timestamp=timestamp,
            turn_number=state.turn_number,
            session_id=self._session_id,
            updates={
                "authoritative_delta": state_delta,
                "authoritative_state": self._game_runtime.state.model_dump(),
            },
            full_state=state.game_state.model_dump(),
        )
        self._emit_event(gs_event, state)
        log.info(
            "game.action_applied",
            agent=agent_name,
            action_type=action_type,
            delta=json.dumps(state_delta),
            turn=state.turn_number,
        )

    def _emit_game_generated_messages(
        self,
        *,
        state: SessionState,
        timestamp: datetime,
        turn_number: int,
        public_events: list[dict],
        private_events: list[dict],
    ) -> None:
        for payload in public_events:
            text = str(payload.get("text", "")).strip()
            if not text:
                continue
            self._emit_communication_segment(
                segment=CommunicationSegment(visibility="public", text=text),
                agent_id="game_engine",
                agent_name="Game",
                model="game",
                turn_number=turn_number,
                is_parallel=False,
                timestamp=timestamp,
                state=state,
                team_channel_id=None,
            )
        for payload in private_events:
            text = str(payload.get("text", "")).strip()
            recipient = payload.get("recipient_id") or payload.get("recipient_name")
            if not text or not isinstance(recipient, str):
                continue
            self._emit_communication_segment(
                segment=CommunicationSegment(
                    visibility="private",
                    text=text,
                    recipient=recipient,
                ),
                agent_id="game_engine",
                agent_name="Game",
                model="game",
                turn_number=turn_number,
                is_parallel=False,
                timestamp=timestamp,
                state=state,
                team_channel_id=None,
            )

    def _resolve_moderation_decision(
        self,
        moderation_result: ModerationDecision | HybridAuditRecord,
        *,
        actor_id: str,
        proposed_action: object,
        timestamp: datetime,
        state: SessionState,
    ) -> ModerationDecision:
        if isinstance(moderation_result, HybridAuditRecord):
            audits = state.game_state.custom.setdefault("hybrid_audit_records", [])
            audit_record = {
                "turn_number": state.turn_number,
                "actor_id": actor_id,
                "proposed_action": (
                    proposed_action.model_dump()
                    if hasattr(proposed_action, "model_dump")
                    else proposed_action
                ),
                "diverged": moderation_result.diverged,
                "primary_decision": moderation_result.primary.model_dump(),
                "shadow_decision": (
                    moderation_result.shadow.model_dump()
                    if moderation_result.shadow is not None
                    else None
                ),
            }
            audits.append(audit_record)
            audit_event = HybridAuditEvent(
                timestamp=timestamp,
                turn_number=state.turn_number,
                session_id=self._session_id,
                actor_id=actor_id,
                proposed_action=audit_record["proposed_action"],
                diverged=moderation_result.diverged,
                primary_decision=audit_record["primary_decision"],
                shadow_decision=audit_record["shadow_decision"],
            )
            self._emit_event(audit_event, state)
            return moderation_result.primary
        return moderation_result

    def _handle_moderation_failure(
        self,
        *,
        agent_id: str,
        agent_name: str,
        state: SessionState,
        timestamp: datetime,
        violation_text: str,
        reason: str,
        proposed_action: dict,
        failure_category: str,
    ) -> None:
        rv_event = RuleViolationEvent(
            timestamp=timestamp,
            turn_number=state.turn_number,
            session_id=self._session_id,
            agent_id=agent_id,
            rule=reason,
            violation_text=violation_text,
        )
        self._emit_event(rv_event, state)
        failures = state.game_state.custom.setdefault("moderation_failures", [])
        failures.append(
            {
                "turn_number": state.turn_number,
                "agent_id": agent_id,
                "failure_category": failure_category,
                "reason": reason,
                "proposed_action": proposed_action,
            }
        )
        retries = self._increment_moderation_retry_count(state, agent_id)
        retry_limit = self._failure_retry_limit(failure_category)
        if retries > retry_limit:
            self._resolve_failure_exhaustion(
                agent_id=agent_id,
                agent_name=agent_name,
                state=state,
                timestamp=timestamp,
                reason=reason,
                failure_category=failure_category,
            )
            log.warning(
                "game.moderation_retry_exhausted",
                agent=agent_name,
                retries=retries,
                failure_category=failure_category,
                turn=state.turn_number,
            )
            return
        state.game_state.custom["_retry_turn_pending"] = True
        log.info(
            "game.action_rejected",
            agent=agent_name,
            reason=reason,
            retry_count=retries,
            failure_category=failure_category,
            turn=state.turn_number,
        )

    def _failure_retry_limit(self, failure_category: str) -> int:
        if self._config.game is None:
            return _MAX_VIOLATION_RETRIES
        policy = self._config.game.moderation.failure_policy
        if failure_category == "moderator":
            return policy.moderator_retry_limit
        return policy.actor_retry_limit

    def _resolve_failure_exhaustion(
        self,
        *,
        agent_id: str,
        agent_name: str,
        state: SessionState,
        timestamp: datetime,
        reason: str,
        failure_category: str,
    ) -> None:
        action = self._failure_exhaustion_action(failure_category)
        state.game_state.custom["_retry_turn_pending"] = False
        self._reset_moderation_retry_count(state, agent_id)

        if action == "skip_turn":
            self._apply_skip_turn_resolution(
                agent_id=agent_id,
                agent_name=agent_name,
                state=state,
                timestamp=timestamp,
                reason=reason,
                failure_category=failure_category,
            )
            return
        if action == "forfeit":
            self._apply_forfeit_resolution(
                agent_id=agent_id,
                agent_name=agent_name,
                state=state,
                timestamp=timestamp,
                reason=reason,
                failure_category=failure_category,
            )
            return

        resolution = self._record_moderation_resolution(
            state=state,
            timestamp=timestamp,
            updates={
                "policy_action": "session_error",
                "agent_id": agent_id,
                "failure_category": failure_category,
                "reason": reason,
            },
        )
        end_event = self._make_end_event(state, "error")
        end_event.message = (
            f"Moderation failed for {agent_name}; exhaustion policy session_error applied."
        )
        self._emit_event(end_event, state)
        state.end_reason = end_event.reason
        resolution["end_reason"] = end_event.reason

    def _failure_exhaustion_action(self, failure_category: str) -> str:
        if self._config.game is None:
            return "session_error"
        policy = self._config.game.moderation.failure_policy
        if failure_category == "moderator":
            return policy.moderator_retry_exhaustion_action
        return policy.actor_retry_exhaustion_action

    def _apply_skip_turn_resolution(
        self,
        *,
        agent_id: str,
        agent_name: str,
        state: SessionState,
        timestamp: datetime,
        reason: str,
        failure_category: str,
    ) -> None:
        if self._game_runtime is not None:
            self._game_runtime.state = self._advance_runtime_turn_without_action(
                self._game_runtime.state,
                actor_id=agent_id,
            )
            self._sync_authoritative_game_state(state)
        self._record_moderation_resolution(
            state=state,
            timestamp=timestamp,
            updates={
                "policy_action": "skip_turn",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "failure_category": failure_category,
                "reason": reason,
            },
        )

    def _apply_forfeit_resolution(
        self,
        *,
        agent_id: str,
        agent_name: str,
        state: SessionState,
        timestamp: datetime,
        reason: str,
        failure_category: str,
    ) -> None:
        winner = self._resolve_forfeit_winner(agent_id)
        if self._game_runtime is not None:
            self._game_runtime.state = self._mark_runtime_forfeit(
                self._game_runtime.state,
                winner=winner,
            )
            self._sync_authoritative_game_state(state)
        state.game_state.winner = winner
        state.game_state.is_over = True
        resolution = self._record_moderation_resolution(
            state=state,
            timestamp=timestamp,
            updates={
                "policy_action": "forfeit",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "failure_category": failure_category,
                "reason": reason,
                "winner": winner,
            },
        )
        end_event = self._make_end_event(state, "win_condition")
        end_event.message = f"{agent_name} forfeited after repeated moderated failures."
        self._emit_event(end_event, state)
        state.end_reason = end_event.reason
        resolution["end_reason"] = end_event.reason

    def _record_moderation_resolution(
        self,
        *,
        state: SessionState,
        timestamp: datetime,
        updates: dict,
    ) -> dict:
        resolutions = state.game_state.custom.setdefault("moderation_resolutions", [])
        resolution = {"turn_number": state.turn_number, **updates}
        resolutions.append(resolution)
        gs_event = GameStateEvent(
            timestamp=timestamp,
            turn_number=state.turn_number,
            session_id=self._session_id,
            updates={"moderation_resolution": resolution},
            full_state=state.game_state.model_dump(),
        )
        self._emit_event(gs_event, state)
        return resolution

    def _advance_runtime_turn_without_action(self, runtime_state, *, actor_id: str):
        players = list(getattr(runtime_state, "players", []))
        if not players or actor_id not in players:
            return runtime_state
        next_index = (players.index(actor_id) + 1) % len(players)
        updates = {
            "active_player": players[next_index],
            "turn_index": getattr(runtime_state, "turn_index", 0) + 1,
        }
        if hasattr(runtime_state, "round_number"):
            updates["round_number"] = getattr(runtime_state, "round_number", 0) + 1
        return runtime_state.model_copy(update=updates)

    def _mark_runtime_forfeit(self, runtime_state, *, winner: str):
        updates = {
            "winner": winner,
            "active_player": "",
            "phase": "complete",
        }
        if hasattr(runtime_state, "is_draw"):
            updates["is_draw"] = False
        return runtime_state.model_copy(update=updates)

    def _resolve_forfeit_winner(self, forfeiting_agent_id: str) -> str:
        if self._game_runtime is not None:
            players = list(getattr(self._game_runtime.state, "players", []))
            for player_id in players:
                if player_id != forfeiting_agent_id:
                    return player_id
        for agent in self._config.agents:
            if agent.id != forfeiting_agent_id and agent.role != "moderator":
                return agent.id
        return ""

    @staticmethod
    def _increment_moderation_retry_count(state: SessionState, agent_id: str) -> int:
        retry_counts = state.game_state.custom.setdefault("moderation_retry_counts", {})
        current = int(retry_counts.get(agent_id, 0)) + 1
        retry_counts[agent_id] = current
        return current

    @staticmethod
    def _reset_moderation_retry_count(state: SessionState, agent_id: str) -> None:
        retry_counts = state.game_state.custom.setdefault("moderation_retry_counts", {})
        if agent_id in retry_counts:
            retry_counts[agent_id] = 0

    def _emit_communication_segment(
        self,
        *,
        segment: CommunicationSegment,
        agent_id: str,
        agent_name: str,
        model: str,
        turn_number: int,
        is_parallel: bool,
        timestamp: datetime,
        state: SessionState,
        team_channel_id: str | None,
    ) -> None:
        text = segment.text.strip()
        if not text:
            return

        if segment.visibility == "team":
            if not team_channel_id:
                log.warning(
                    "agent.team_message_dropped",
                    agent=agent_name,
                    reason="no team assigned",
                    turn=turn_number,
                )
                return
            event = MessageEvent(
                timestamp=timestamp,
                turn_number=turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                model=model,
                channel_id=team_channel_id,
                text=text,
                is_parallel=is_parallel,
            )
            self._emit_event(event, state)
            log.info(
                "agent.team_message",
                agent=agent_name,
                channel=team_channel_id,
                text=text[:120],
                turn=turn_number,
            )
            return

        if segment.visibility == "private":
            if not segment.recipient:
                log.warning(
                    "agent.private_message_dropped",
                    agent=agent_name,
                    reason="missing recipient",
                    turn=turn_number,
                )
                return
            recipient_id = next(
                (a.id for a in self._config.agents if a.name == segment.recipient),
                segment.recipient,
            )
            private_channel_id = f"private_{agent_id}_{recipient_id}"
            if private_channel_id not in self._announced_channels:
                ch_event = ChannelCreatedEvent(
                    timestamp=timestamp,
                    session_id=self._session_id,
                    channel_id=private_channel_id,
                    channel_type="private",
                    members=[agent_id, recipient_id],
                )
                self._emit_event(ch_event, state)
                self._announced_channels.add(private_channel_id)
            event = MessageEvent(
                timestamp=timestamp,
                turn_number=turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_name,
                model=model,
                channel_id=private_channel_id,
                recipient_id=recipient_id,
                text=text,
                is_parallel=is_parallel,
            )
            self._emit_event(event, state)
            log.info(
                "agent.private_message",
                agent=agent_name,
                to=segment.recipient,
                text=text[:120],
                turn=turn_number,
            )
            return

        event = MessageEvent(
            timestamp=timestamp,
            turn_number=turn_number,
            session_id=self._session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            model=model,
            channel_id="public",
            text=text,
            is_parallel=is_parallel,
        )
        self._emit_event(event, state)
        log.info(
            "agent.public_message",
            agent=agent_name,
            text=text[:120],
            turn=turn_number,
        )

    def _emit_event(self, event, state: SessionState | None = None) -> None:
        self._transcript.record(event)
        self._bus.emit(event)
        if state is not None:
            state.events.append(event)
