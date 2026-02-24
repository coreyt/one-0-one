"""
Poker (Texas Hold'em) orchestrator.

Pattern:
  1. Dealer (role="moderator") opens the game as a solo turn.
  2. After any player's public message → Dealer speaks next.
  3. After Dealer speaks → route to the player whose name appears in the last
     3 lines of the Dealer's message (the player being addressed).
  4. Fallback: if no player name is found in the Dealer's closing lines,
     route to the active player with the fewest public messages.
  5. Guard: if the last TURN event was Dealer-only but they produced no message
     (timeout / refusal), skip them this round.

Fold handling: the Dealer simply stops addressing folded players mid-hand;
the Dealer uses <eliminate> only when a player is busted out (0 chips), which
permanently removes them from the turn order via the normal elimination mechanism.

Termination: max_turns, completion_signal, ≤1 active player remaining.
"""

from __future__ import annotations

import re

from src.logging import get_logger
from src.orchestrators import OrchestratorInput, OrchestratorOutput
from src.session.config import AgentConfig

log = get_logger(__name__)

_COMPLETION_SCAN_WINDOW = 5
# Number of trailing lines of the Dealer's message to scan for a player name.
_DEALER_SCAN_LINES = 4


def _find_addressed_player(
    dealer_text: str, players: list[AgentConfig]
) -> AgentConfig | None:
    """
    Return the player whose name appears LAST in the closing lines of the
    Dealer's message — that is the player being addressed next.

    Matches full name OR first name (first word of the name), case-insensitive.
    Word-boundary check avoids false partial matches.
    Rightmost occurrence wins so narrative mentions early in the sentence
    ("Lila folds. Rocky, your move.") don't shadow the actual addressee.
    """
    lines = dealer_text.strip().splitlines()
    tail = " ".join(lines[-_DEALER_SCAN_LINES:]).lower()
    best_player: AgentConfig | None = None
    best_pos: int = -1
    for player in players:
        name_lower = player.name.lower()
        # Build candidate search terms: full name and first name
        terms: set[str] = {name_lower}
        first_word = name_lower.split()[0]
        if first_word != name_lower:
            terms.add(first_word)
        for term in terms:
            pattern = r"(?<![a-z])" + re.escape(term) + r"(?![a-z])"
            matches = list(re.finditer(pattern, tail))
            if matches:
                last_pos = matches[-1].start()
                if last_pos > best_pos:
                    best_pos = last_pos
                    best_player = player
    return best_player


def orchestrate(input: OrchestratorInput) -> OrchestratorOutput:
    """
    Determine the next agent to speak in a poker session.

    Routing:
      - Game start           → Dealer
      - After player speaks  → Dealer
      - After Dealer speaks  → player named in Dealer's closing lines (else fewest-msgs)
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

    dealer = next(
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

    if len(players) == 1:
        log.info("orchestrator.decision", reason="one_player_remaining")
        return OrchestratorOutput(session_end=True, end_reason="win_condition")

    # ----------------------------------------------------------------
    # Dealer-just-attempted guard (timeout / refusal)
    # ----------------------------------------------------------------
    turn_events = [e for e in state.events if e.type == "TURN"]
    dealer_just_attempted = (
        dealer is not None
        and bool(turn_events)
        and turn_events[-1].agent_ids == [dealer.id]
    )

    # ----------------------------------------------------------------
    # Public messages
    # ----------------------------------------------------------------
    public_msgs = [
        e
        for e in state.events
        if e.type == "MESSAGE" and e.channel_id == "public"
    ]

    # ----------------------------------------------------------------
    # Game hasn't started — Dealer opens
    # ----------------------------------------------------------------
    if not public_msgs:
        if dealer and not dealer_just_attempted:
            log.info(
                "orchestrator.decision",
                reason="dealer_turn",
                trigger="game_open",
                next_agents=[dealer.id],
                turn=turn,
            )
            return OrchestratorOutput(next_agents=[dealer.id], advance_turns=1)
        # Dealer timed out at start — first player in config order
        first = players[0]
        log.info(
            "orchestrator.decision",
            reason="player_turn",
            next_agents=[first.id],
            turn=turn,
        )
        return OrchestratorOutput(next_agents=[first.id], advance_turns=1)

    # ----------------------------------------------------------------
    # Last public speaker was a player → Dealer responds
    # ----------------------------------------------------------------
    last_speaker_id = public_msgs[-1].agent_id
    last_was_player = dealer is None or last_speaker_id != dealer.id

    if last_was_player and dealer is not None and not dealer_just_attempted:
        log.info(
            "orchestrator.decision",
            reason="dealer_turn",
            trigger="after_player",
            next_agents=[dealer.id],
            turn=turn,
        )
        return OrchestratorOutput(next_agents=[dealer.id], advance_turns=1)

    # ----------------------------------------------------------------
    # Dealer just spoke (or timed out) → find addressed player
    # ----------------------------------------------------------------

    # Only scan Dealer messages that arrived AFTER the most recent player message.
    # If the Dealer timed out (no message after the player spoke), there are none
    # here, so we fall through to the fewest-messages fallback — this breaks the
    # Rocky→timeout→Rocky→timeout loop that would otherwise occur.
    player_ids_set = {p.id for p in players}
    last_player_idx = -1
    for i, e in enumerate(public_msgs):
        if dealer is None or e.agent_id != dealer.id:
            last_player_idx = i

    dealer_msgs_after_player = [
        e
        for e in public_msgs[last_player_idx + 1:]
        if dealer is not None and e.agent_id == dealer.id
    ]

    if dealer_msgs_after_player:
        last_dealer_text = dealer_msgs_after_player[-1].text
        addressed = _find_addressed_player(last_dealer_text, players)
        if addressed is not None:
            log.info(
                "orchestrator.decision",
                reason="player_turn",
                trigger="dealer_addressed",
                next_agents=[addressed.id],
                turn=turn,
            )
            return OrchestratorOutput(next_agents=[addressed.id], advance_turns=1)

    # ----------------------------------------------------------------
    # Fallback: player with fewest public messages
    # ----------------------------------------------------------------
    player_ids = {p.id for p in players}
    msg_counts: dict[str, int] = {p.id: 0 for p in players}
    for e in public_msgs:
        if e.agent_id in player_ids:
            msg_counts[e.agent_id] += 1

    next_player = min(players, key=lambda p: msg_counts[p.id])
    log.info(
        "orchestrator.decision",
        reason="player_turn",
        trigger="fewest_messages_fallback",
        next_agents=[next_player.id],
        turn=turn,
    )
    return OrchestratorOutput(next_agents=[next_player.id], advance_turns=1)
