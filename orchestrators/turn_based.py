"""
Turn-based 2-player game orchestrator.

Pattern:
  1. Narrator (role="moderator") opens the game as a solo turn.
  2. After any player's public message → Narrator speaks next.
  3. After Narrator speaks → the player with the fewest public messages goes next
     (ensures strict P1 → P2 → P1 alternation for a 2-player game).
  4. Guard: if the last TURN event was Narrator-only but they produced no message
     (timeout / refusal), skip them this round to avoid an infinite loop.

Termination checks mirror mafia.py (max_turns, max_rounds, completion_signal,
all_eliminated).
"""

from __future__ import annotations

from src.logging import get_logger
from src.orchestrators import OrchestratorInput, OrchestratorOutput

log = get_logger(__name__)

_COMPLETION_SCAN_WINDOW = 5


def orchestrate(input: OrchestratorInput) -> OrchestratorOutput:
    """
    Determine the next agent to speak.

    Strict pattern: Narrator opens → Player → Narrator → Player → Narrator → …
    The player chosen after each Narrator turn is whoever has the fewest public
    messages so far, guaranteeing strict alternation in 2-player games.
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

    if not players:
        log.info("orchestrator.decision", reason="all_eliminated")
        return OrchestratorOutput(session_end=True, end_reason="win_condition")

    # Only one player left (other eliminated) → game over
    if len(players) == 1:
        log.info("orchestrator.decision", reason="one_player_remaining")
        return OrchestratorOutput(session_end=True, end_reason="win_condition")

    # ----------------------------------------------------------------
    # Narrator-just-attempted guard
    # ----------------------------------------------------------------
    turn_events = [e for e in state.events if e.type == "TURN"]
    narrator_just_attempted = (
        narrator is not None
        and bool(turn_events)
        and turn_events[-1].agent_ids == [narrator.id]
    )

    # ----------------------------------------------------------------
    # All public messages
    # ----------------------------------------------------------------
    public_msgs = [
        e
        for e in state.events
        if e.type == "MESSAGE" and e.channel_id == "public"
    ]

    # ----------------------------------------------------------------
    # Game hasn't started — narrator opens (or first player if no narrator)
    # ----------------------------------------------------------------
    if not public_msgs:
        if narrator and not narrator_just_attempted:
            log.info(
                "orchestrator.decision",
                reason="narrator_turn",
                trigger="game_open",
                next_agents=[narrator.id],
                turn=turn,
            )
            return OrchestratorOutput(next_agents=[narrator.id], advance_turns=1)
        # No narrator or narrator timed out at very start — go to first player
        first = players[0]
        log.info(
            "orchestrator.decision",
            reason="player_turn",
            next_agents=[first.id],
            turn=turn,
        )
        return OrchestratorOutput(next_agents=[first.id], advance_turns=1)

    # ----------------------------------------------------------------
    # Last public speaker was a player → narrator goes next
    # ----------------------------------------------------------------
    last_speaker_id = public_msgs[-1].agent_id
    last_was_player = narrator is None or last_speaker_id != narrator.id

    if last_was_player and narrator is not None and not narrator_just_attempted:
        log.info(
            "orchestrator.decision",
            reason="narrator_turn",
            trigger="after_player",
            next_agents=[narrator.id],
            turn=turn,
        )
        return OrchestratorOutput(next_agents=[narrator.id], advance_turns=1)

    # ----------------------------------------------------------------
    # Narrator just spoke (or no narrator / narrator timed out) →
    # pick the player with the fewest public messages (strict alternation)
    # ----------------------------------------------------------------
    player_ids = {p.id for p in players}
    msg_counts: dict[str, int] = {p.id: 0 for p in players}
    for e in public_msgs:
        if e.agent_id in player_ids:
            msg_counts[e.agent_id] += 1

    # Tiebreak: follow config order (preserves P1 → P2 preference on first turn)
    next_player = min(players, key=lambda p: msg_counts[p.id])

    log.info(
        "orchestrator.decision",
        reason="player_turn",
        next_agents=[next_player.id],
        turn=turn,
    )
    return OrchestratorOutput(next_agents=[next_player.id], advance_turns=1)
