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
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from src.channels.router import ChannelRouter
from src.logging import get_logger
from src.memory import load_memory, save_memory
from src.orchestrators import OrchestratorInput, load_orchestrator
from src.personas import assign_random_personalities
from src.providers import ProviderError
from src.providers.litellm_client import LiteLLMClient
from src.response_parser import ResponseParser
from src.session.config import SessionConfig
from src.session.event_bus import EventBus
from src.session.events import (
    ChannelCreatedEvent,
    GameStateEvent,
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


class SessionEngine:
    """Drives a complete multi-agent conversation session."""

    def __init__(self, config: SessionConfig, bus: EventBus) -> None:
        # Assign random personalities using seconds-since-2000-01-01-UTC as seed
        _epoch_2000 = datetime(2000, 1, 1, tzinfo=UTC)
        _seed = int((datetime.now(UTC) - _epoch_2000).total_seconds())
        try:
            config = assign_random_personalities(config, seed=_seed)
            log.info("session.personalities_assigned", seed=_seed)
        except ValueError as exc:
            log.warning("session.personalities_skipped", reason=str(exc))

        self._config = config
        self._bus = bus
        self._session_id = str(uuid.uuid4())
        self._router = ChannelRouter(config)
        self._parser = ResponseParser()
        self._provider = LiteLLMClient()
        self._orchestrate = load_orchestrator(config.orchestrator)
        self._transcript = TranscriptWriter(config)
        # Pause gate — cleared when paused, set when running
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._state: SessionState | None = None
        # Track private channel IDs already announced via CHANNEL_CREATED events
        self._announced_channels: set[str] = set()
        # Attach transcript writer to every event
        self._bus.stream().subscribe(self._transcript.record)

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
            self._bus.emit(end_event)
            state.events.append(end_event)
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
        return SessionState(
            session_id=self._session_id,
            turn_number=0,
            game_state=GameState(),
            events=[],
            agents=agents,
        )

    def _emit_channel_events(self, state: SessionState) -> None:
        # Always emit the public channel
        public_event = ChannelCreatedEvent(
            timestamp=datetime.now(UTC),
            session_id=self._session_id,
            channel_id="public",
            channel_type="public",
            members=[],
        )
        self._bus.emit(public_event)
        state.events.append(public_event)
        self._announced_channels.add("public")

        # Emit configured team/private channels
        for ch in self._config.channels:
            event = ChannelCreatedEvent(
                timestamp=datetime.now(UTC),
                session_id=self._session_id,
                channel_id=ch.id,
                channel_type=ch.type,
                members=ch.members,
            )
            self._bus.emit(event)
            state.events.append(event)
            self._announced_channels.add(ch.id)

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
            # Honour pause — waits here until resume() sets the event
            await self._resume_event.wait()

            orch_input = OrchestratorInput(config=self._config, state=state)
            orch_output = self._orchestrate(orch_input)

            if orch_output.session_end:
                end_event = self._make_end_event(
                    state, orch_output.end_reason or "max_turns"
                )
                self._bus.emit(end_event)
                state.events.append(end_event)
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
                    self._bus.emit(gs_event)
                    state.events.append(gs_event)
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
                self._bus.emit(rv_event)
                state.events.append(rv_event)

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
                self._bus.emit(gs_event)
                state.events.append(gs_event)

            # Emit TURN event
            is_parallel = len(orch_output.next_agents) > 1
            turn_event = TurnEvent(
                timestamp=datetime.now(UTC),
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_ids=orch_output.next_agents,
                is_parallel=is_parallel,
            )
            self._bus.emit(turn_event)
            state.events.append(turn_event)

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
                        self._run_agent(agent_id, state, is_parallel=True)
                        for agent_id in orch_output.next_agents
                    ]
                )
            else:
                for agent_id in orch_output.next_agents:
                    await self._run_agent(agent_id, state, is_parallel=False)

            # Only advance the turn counter if at least one agent produced output
            # (a MESSAGE or MONOLOGUE event).  A pure timeout/error produces neither,
            # so the turn budget is not consumed.
            # Safety valve: if the same turn has failed _MAX_EMPTY_ATTEMPTS times in a
            # row (all providers down), force advancement to avoid an infinite loop.
            new_events = state.events[events_before:]
            produced_output = any(
                e.type in ("MESSAGE", "MONOLOGUE") for e in new_events
            )
            if produced_output:
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

        llm = self._config.llm_defaults
        try:
            result = await self._provider.complete(
                model=f"{agent_config.provider}/{agent_config.model}",
                messages=messages,
                temperature=llm.temperature,
                native_thinking=native_thinking,
                thinking_budget_tokens=llm.thinking_budget,
                timeout=llm.timeout,
                **({"max_tokens": llm.max_tokens} if llm.max_tokens else {}),
            )
        except ProviderError as exc:
            error_str = str(exc).lower()
            incident_type = "timeout" if ("timeout" in error_str or "timed out" in error_str) else "error"
            log.error(
                "llm.error",
                agent_id=agent_id,
                model=f"{agent_config.provider}/{agent_config.model}",
                error=str(exc),
            )
            state.game_state.incidents.append({
                "turn": state.turn_number,
                "agent_id": agent_id,
                "model": f"{agent_config.provider}/{agent_config.model}",
                "type": incident_type,
            })
            incident_event = IncidentEvent(
                timestamp=datetime.now(UTC),
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_config.name,
                model=f"{agent_config.provider}/{agent_config.model}",
                incident_type=incident_type,
                detail=str(exc),
            )
            self._bus.emit(incident_event)
            state.events.append(incident_event)
            agent_state.status = "idle"
            return

        agent_state.status = "speaking"

        # Update token usage
        agent_state.token_usage["prompt_tokens"] = (
            agent_state.token_usage.get("prompt_tokens", 0) + result.usage.prompt_tokens
        )
        agent_state.token_usage["completion_tokens"] = (
            agent_state.token_usage.get("completion_tokens", 0) + result.usage.completion_tokens
        )

        # Parse XML tags (strip leading "AgentName: " prefix if model echoes it)
        parsed = self._parser.parse(result.text, agent_name=agent_config.name)

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
            self._bus.emit(elim_event)
            state.events.append(elim_event)

        # Emit MONOLOGUE event (observer-only — never added to other agents' context)
        if parsed.thinking:
            mono_event = MonologueEvent(
                timestamp=now,
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_config.name,
                text=parsed.thinking,
            )
            self._bus.emit(mono_event)
            state.events.append(mono_event)
            log.debug(
                "agent.monologue",
                agent=agent_config.name,
                text=parsed.thinking[:120],
                turn=state.turn_number,
            )

        # Emit team message
        if parsed.team_message and agent_config.team:
            team_event = MessageEvent(
                timestamp=now,
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_config.name,
                model=result.model,
                channel_id=agent_config.team,
                text=parsed.team_message,
                is_parallel=is_parallel,
            )
            self._bus.emit(team_event)
            state.events.append(team_event)
            log.info(
                "agent.team_message",
                agent=agent_config.name,
                channel=agent_config.team,
                text=parsed.team_message[:120],
                turn=state.turn_number,
            )

        # Emit private message
        if parsed.private_to and parsed.private_message:
            # Resolve recipient name → agent_id
            recipient_id = next(
                (a.id for a in self._config.agents if a.name == parsed.private_to),
                parsed.private_to,  # fallback: use the name as-is
            )
            private_channel_id = f"private_{agent_id}_{recipient_id}"
            # Announce the channel the first time it is used
            if private_channel_id not in self._announced_channels:
                ch_event = ChannelCreatedEvent(
                    timestamp=now,
                    session_id=self._session_id,
                    channel_id=private_channel_id,
                    channel_type="private",
                    members=[agent_id, recipient_id],
                )
                self._bus.emit(ch_event)
                state.events.append(ch_event)
                self._announced_channels.add(private_channel_id)
            priv_event = MessageEvent(
                timestamp=now,
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_config.name,
                model=result.model,
                channel_id=private_channel_id,
                recipient_id=recipient_id,
                text=parsed.private_message,
                is_parallel=is_parallel,
            )
            self._bus.emit(priv_event)
            state.events.append(priv_event)
            log.info(
                "agent.private_message",
                agent=agent_config.name,
                to=parsed.private_to,
                text=parsed.private_message[:120],
                turn=state.turn_number,
            )

        # Emit public message (always, even if empty after tag extraction)
        if parsed.public_message:
            pub_event = MessageEvent(
                timestamp=now,
                turn_number=state.turn_number,
                session_id=self._session_id,
                agent_id=agent_id,
                agent_name=agent_config.name,
                model=result.model,
                channel_id="public",
                text=parsed.public_message,
                is_parallel=is_parallel,
            )
            self._bus.emit(pub_event)
            state.events.append(pub_event)
            log.info(
                "agent.public_message",
                agent=agent_config.name,
                text=parsed.public_message[:120],
                turn=state.turn_number,
            )

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
        self._bus.emit(event)
        self._state.events.append(event)
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
