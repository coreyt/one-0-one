"""
Market Research focus group orchestrator.

Three-phase structure:

  Phase 1 — Individual Q&A
    The moderator asks each participant a targeted 1-on-1 question based
    on their specific persona. Participants respond one at a time.
    Pattern: moderator → participant (least messages) → repeat until all
    participants have spoken once.

  Phase 2 — Group Discussion
    The moderator poses GROUP_ROUNDS open-ended questions to the full group.
    All participants respond in parallel after each moderator prompt.

  Phase 3 — Synthesis
    The moderator poses a final collaborative question. All participants
    respond in parallel, then the moderator delivers a closing synthesis
    and signals "RESEARCH COMPLETE".

Phase derivation (M = number of moderator public messages so far, N = participants):
  Phase 1 : M in [1 .. N]               – one individual question per participant
  Phase 2 : M in [N+1 .. N+GROUP_ROUNDS] – group discussion rounds
  Phase 3 : M == N+GROUP_ROUNDS+1        – synthesis question → all respond
  Closing : M == N+GROUP_ROUNDS+2        – moderator closes → session ends
"""

from __future__ import annotations

from src.logging import get_logger
from src.orchestrators import OrchestratorInput, OrchestratorOutput

log = get_logger(__name__)

# Number of open group discussion rounds before synthesis
GROUP_ROUNDS = 3

_COMPLETION_SCAN_WINDOW = 5


def orchestrate(input: OrchestratorInput) -> OrchestratorOutput:
    """
    Determine the next speaker(s) for a market research focus group.

    Turn pattern:
      - After any participant(s) speak → moderator speaks next.
      - After moderator speaks in Phase 1 → one participant (fewest messages).
      - After moderator speaks in Phase 2/3 → all participants in parallel.
      - After moderator's closing message (M ≥ CLOSE_AT) → session ends.
    """
    config = input.config
    state = input.state
    turn = state.turn_number

    # ------------------------------------------------------------------
    # Termination: max_turns hard cap
    # ------------------------------------------------------------------
    if config.max_turns is not None and turn >= config.max_turns:
        log.info("orchestrator.decision", reason="max_turns", turn=turn)
        return OrchestratorOutput(session_end=True, end_reason="max_turns")

    # ------------------------------------------------------------------
    # Termination: completion_signal (e.g. "RESEARCH COMPLETE")
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Agent pools
    # ------------------------------------------------------------------
    moderator = next(
        (a for a in config.agents if a.role == "moderator"), None
    )
    players = [a for a in config.agents if a.role != "moderator"]

    if not players:
        log.info("orchestrator.decision", reason="no_players")
        return OrchestratorOutput(session_end=True, end_reason="error")

    N = len(players)
    # M at which the closing message will have been delivered
    CLOSE_AT = N + GROUP_ROUNDS + 2

    # ------------------------------------------------------------------
    # Public message history
    # ------------------------------------------------------------------
    public_msgs = [
        e for e in state.events if e.type == "MESSAGE" and e.channel_id == "public"
    ]

    # Guard: if moderator was the last TURN attempted (timed out / errored),
    # skip them this round to prevent an infinite loop.
    turn_events = [e for e in state.events if e.type == "TURN"]
    moderator_just_attempted = (
        moderator is not None
        and bool(turn_events)
        and turn_events[-1].agent_ids == [moderator.id]
    )

    # ------------------------------------------------------------------
    # No messages yet → moderator opens the session
    # ------------------------------------------------------------------
    if not public_msgs:
        if moderator and not moderator_just_attempted:
            log.info(
                "orchestrator.decision",
                reason="moderator_opens",
                next_agents=[moderator.id],
                turn=turn,
            )
            return OrchestratorOutput(next_agents=[moderator.id], advance_turns=1)
        # No moderator or timed out at very start → first participant
        return OrchestratorOutput(next_agents=[players[0].id], advance_turns=1)

    # ------------------------------------------------------------------
    # Count moderator public messages
    # ------------------------------------------------------------------
    moderator_msg_count = sum(
        1 for e in public_msgs if moderator and e.agent_id == moderator.id
    )

    last_speaker_id = public_msgs[-1].agent_id
    last_was_moderator = (
        moderator is not None and last_speaker_id == moderator.id
    )

    # ------------------------------------------------------------------
    # A participant just spoke → moderator goes next
    # ------------------------------------------------------------------
    if not last_was_moderator:
        if moderator is not None and not moderator_just_attempted:
            # If the moderator has already delivered the closing message,
            # the session should end (completion_signal check above may not
            # have fired yet if the moderator's last message was the Nth one).
            if moderator_msg_count >= CLOSE_AT:
                log.info(
                    "orchestrator.decision",
                    reason="close_at_reached",
                    moderator_msgs=moderator_msg_count,
                )
                return OrchestratorOutput(
                    session_end=True, end_reason="completion_signal"
                )
            log.info(
                "orchestrator.decision",
                reason="moderator_turn_after_participant",
                next_agents=[moderator.id],
                moderator_msgs=moderator_msg_count,
                turn=turn,
            )
            return OrchestratorOutput(next_agents=[moderator.id], advance_turns=1)

        # Fallback (no moderator or timed out): pick player with fewest messages
        msg_counts = {
            p.id: sum(1 for e in public_msgs if e.agent_id == p.id)
            for p in players
        }
        next_player = min(players, key=lambda p: (msg_counts[p.id], players.index(p)))
        return OrchestratorOutput(next_agents=[next_player.id], advance_turns=1)

    # ------------------------------------------------------------------
    # Moderator just spoke → determine next participant(s) by phase
    # ------------------------------------------------------------------
    M = moderator_msg_count  # messages delivered so far (including this one)

    # Closing message has been delivered → end session
    if M >= CLOSE_AT:
        log.info(
            "orchestrator.decision",
            reason="session_end_after_closing",
            moderator_msgs=M,
        )
        return OrchestratorOutput(session_end=True, end_reason="completion_signal")

    # Phase 1 — Individual Q&A: ask one participant at a time
    if M <= N:
        msg_counts = {
            p.id: sum(1 for e in public_msgs if e.agent_id == p.id)
            for p in players
        }
        # Pick the participant with the fewest messages (config order as tiebreak)
        next_player = min(players, key=lambda p: (msg_counts[p.id], players.index(p)))
        log.info(
            "orchestrator.decision",
            phase="individual",
            next_agents=[next_player.id],
            moderator_msgs=M,
            turn=turn,
        )
        return OrchestratorOutput(next_agents=[next_player.id], advance_turns=1)

    # Phase 2 / Phase 3 — Group or Synthesis: all participants respond in parallel
    all_ids = [p.id for p in players]
    phase = "synthesis" if M == N + GROUP_ROUNDS + 1 else "group"
    log.info(
        "orchestrator.decision",
        phase=phase,
        next_agents=all_ids,
        moderator_msgs=M,
        turn=turn,
    )
    return OrchestratorOutput(next_agents=all_ids, advance_turns=len(all_ids))
