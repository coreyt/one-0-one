"""
Mafia-game orchestrator.

Extends basic round-robin with narrator interleaving:
  - The narrator (role="moderator") speaks as a solo turn every NARRATOR_EVERY
    public player messages, targeting ~20% of public speaking time.
  - Between narrator turns the same team-batching logic as basic.py applies,
    but the moderator is excluded from the player pool.
  - All other termination checks mirror basic.py.
"""

from __future__ import annotations

from src.logging import get_logger
from src.orchestrators import OrchestratorInput, OrchestratorOutput

log = get_logger(__name__)

_COMPLETION_SCAN_WINDOW = 5

# Narrator speaks once per every N player public messages → ~1/(N+1) share.
# N=4 → 1/5 = 20 %.
NARRATOR_EVERY = 4


def orchestrate(input: OrchestratorInput) -> OrchestratorOutput:
    """
    Determine the next agent(s) to speak.

    Narrates every NARRATOR_EVERY player public messages; otherwise delegates
    to batched round-robin over the non-moderator agents.
    """
    config = input.config
    state = input.state
    turn = state.turn_number

    # ----------------------------------------------------------------
    # Termination checks
    # ----------------------------------------------------------------
    if config.max_turns is not None and turn >= config.max_turns:
        log.info("orchestrator.decision", reason="max_turns", turn=turn)
        return OrchestratorOutput(session_end=True, end_reason="max_turns")

    if (
        config.game is not None
        and config.game.max_rounds is not None
        and state.game_state.round >= config.game.max_rounds
    ):
        log.info("orchestrator.decision", reason="max_rounds")
        return OrchestratorOutput(session_end=True, end_reason="win_condition")

    if config.completion_signal:
        recent = [
            e
            for e in state.events[-_COMPLETION_SCAN_WINDOW:]
            if e.type == "MESSAGE" and e.channel_id == "public"
        ]
        sig_lower = config.completion_signal.lower()
        for msg in recent:
            if sig_lower in msg.text.lower():
                log.info(
                    "orchestrator.decision",
                    reason="completion_signal",
                    signal=config.completion_signal,
                )
                return OrchestratorOutput(
                    session_end=True, end_reason="completion_signal"
                )

    # ----------------------------------------------------------------
    # Agent pools
    # ----------------------------------------------------------------
    eliminated = set(state.game_state.eliminated)

    narrator = next(
        (a for a in config.agents if a.role == "moderator" and a.id not in eliminated),
        None,
    )
    players = [
        a
        for a in config.agents
        if a.role != "moderator" and a.id not in eliminated
    ]

    if not players and narrator is None:
        log.info("orchestrator.decision", reason="all_eliminated")
        return OrchestratorOutput(session_end=True, end_reason="win_condition")

    if not players:
        return OrchestratorOutput(next_agents=[narrator.id], advance_turns=1)

    # ----------------------------------------------------------------
    # Narrator turn trigger
    # Count how many public messages have been posted since the narrator
    # last spoke.  If >= NARRATOR_EVERY (or no public messages yet),
    # it's the narrator's turn.
    #
    # Guard: if the most recent TURN event was already for the narrator
    # (i.e. they were just called but timed out / errored and produced no
    # message), skip them this round so we don't loop forever.
    # ----------------------------------------------------------------
    if narrator is not None:
        # Check whether we just attempted a narrator-only turn
        turn_events = [e for e in state.events if e.type == "TURN"]
        narrator_just_attempted = (
            bool(turn_events)
            and turn_events[-1].agent_ids == [narrator.id]
        )

        public_msgs = [
            e
            for e in state.events
            if e.type == "MESSAGE" and e.channel_id == "public"
        ]

        if not public_msgs and not narrator_just_attempted:
            # Game hasn't started — narrator opens
            log.info(
                "orchestrator.decision",
                reason="narrator_turn",
                msgs_since_narrator=0,
                next_agents=[narrator.id],
                advance_turns=1,
                turn=turn,
            )
            return OrchestratorOutput(next_agents=[narrator.id], advance_turns=1)

        if public_msgs and not narrator_just_attempted:
            # Find narrator's last public message index in state.events
            narrator_last_event_idx = -1
            for i, e in enumerate(state.events):
                if e.type == "MESSAGE" and e.channel_id == "public" and e.agent_id == narrator.id:
                    narrator_last_event_idx = i

            # Force narrator turn if any elimination happened since narrator last spoke
            for e in state.events[narrator_last_event_idx + 1:]:
                if e.type == "GAME_STATE" and e.updates.get("newly_eliminated"):
                    log.info(
                        "orchestrator.decision",
                        reason="narrator_turn",
                        trigger="post_elimination",
                        next_agents=[narrator.id],
                        advance_turns=1,
                        turn=turn,
                    )
                    return OrchestratorOutput(next_agents=[narrator.id], advance_turns=1)

            msgs_since_narrator = 0
            for e in reversed(public_msgs):
                if e.agent_id == narrator.id:
                    break
                msgs_since_narrator += 1

            if msgs_since_narrator >= NARRATOR_EVERY:
                log.info(
                    "orchestrator.decision",
                    reason="narrator_turn",
                    msgs_since_narrator=msgs_since_narrator,
                    next_agents=[narrator.id],
                    advance_turns=1,
                    turn=turn,
                )
                return OrchestratorOutput(next_agents=[narrator.id], advance_turns=1)

    # ----------------------------------------------------------------
    # Round-robin through players (narrator excluded), with team batching
    # ----------------------------------------------------------------
    next_idx = turn % len(players)
    batch = _build_batch(players, next_idx)
    advance = len(batch)

    log.info(
        "orchestrator.decision",
        reason="round_robin",
        next_agents=batch,
        advance_turns=advance,
        turn=turn,
    )
    return OrchestratorOutput(next_agents=batch, advance_turns=advance)


def _build_batch(agents: list, start_idx: int) -> list[str]:
    """
    Same batching logic as basic.py — team agents run in parallel, solo
    (no-team) agents also run in parallel.
    """
    n = len(agents)
    first = agents[start_idx]
    batch = [first.id]

    for offset in range(1, n):
        idx = (start_idx + offset) % n
        candidate = agents[idx]

        if first.team is not None:
            if candidate.team == first.team:
                batch.append(candidate.id)
            else:
                break
        else:
            if candidate.team is None:
                batch.append(candidate.id)
            else:
                break

    return batch
