"""
Telephone (whisper-down-the-lane) game orchestrator.

State machine tracking five phases via game_state.custom:
  waiting_for_hitl → Engine pauses until HITL provides starting phrase
  chain            → Operator introduces + whispers to Player1, then
                     players whisper to each other in sequence
  discussion       → Operator reveals original, then each player discusses
  reveal           → Operator publishes the full MSDM transformation chain

Turn flow:
  [Engine pauses — HITL injects phrase] →
  Operator (public intro + private whisper→Player1) →
  Player1 (private→Player2) → Player2 (private→Player3) → ... →
  Player6 (public announcement — says what they heard) →
  Operator (reveals original phrase) →
  Player1 (discusses) → ... → Player6 (discusses) →
  Operator (full chain reveal + analysis + "GAME COMPLETE") →
    completion_signal ends session

Each player applies their own decay based on their education, vocabulary,
and background — this is NOT a uniform phonetic pipeline. The Operator
handles the initial handoff and the final reveal, but players pass the
message directly to each other.
"""

from __future__ import annotations

from src.logging import get_logger
from src.orchestrators import OrchestratorInput, OrchestratorOutput

log = get_logger(__name__)

_COMPLETION_SCAN_WINDOW = 5


def orchestrate(input: OrchestratorInput) -> OrchestratorOutput:
    """Determine the next agent to speak in the Telephone game."""
    config = input.config
    state = input.state
    turn = state.turn_number
    custom = state.game_state.custom

    phase = custom.get("phase", "waiting_for_hitl")

    # ------------------------------------------------------------------
    # Termination checks (mirror turn_based.py / mafia.py)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Agent pools
    # ------------------------------------------------------------------
    operator = next(
        (a for a in config.agents if a.role == "moderator"), None
    )
    players = [a for a in config.agents if a.role != "moderator"]
    player_ids = {p.id for p in players}

    if operator is None:
        log.error(
            "orchestrator.error",
            reason="no_operator",
            detail="Telephone requires an agent with role='moderator' (The Operator). "
            "Launch from the Telephone template or add a moderator agent.",
        )
        return OrchestratorOutput(session_end=True, end_reason="error")

    if not players:
        log.error(
            "orchestrator.error",
            reason="no_players",
            detail="Telephone requires at least 2 agents with role='player'. "
            "Launch from the Telephone template or add player agents.",
        )
        return OrchestratorOutput(session_end=True, end_reason="error")

    # ------------------------------------------------------------------
    # Timeout guard: if last TURN fired but the agent produced no
    # message, skip them to avoid an infinite loop
    # ------------------------------------------------------------------
    turn_events = [e for e in state.events if e.type == "TURN"]
    if turn_events and phase in ("chain", "discussion"):
        last_turn = turn_events[-1]
        last_turn_agent = (
            last_turn.agent_ids[0]
            if len(last_turn.agent_ids) == 1
            else None
        )
        if last_turn_agent is not None:
            last_turn_idx = len(state.events) - 1 - state.events[
                ::-1
            ].index(last_turn)
            msgs_after = [
                e
                for e in state.events[last_turn_idx + 1 :]
                if e.type == "MESSAGE" and e.agent_id == last_turn_agent
            ]
            if not msgs_after:
                return _handle_timeout(
                    last_turn_agent, operator, players, player_ids,
                    phase, custom, turn,
                )

    # ------------------------------------------------------------------
    # All messages (for routing logic)
    # ------------------------------------------------------------------
    all_msgs = [e for e in state.events if e.type == "MESSAGE"]
    hitl_msgs = [e for e in all_msgs if e.agent_id == "hitl"]

    # ------------------------------------------------------------------
    # Phase routing
    # ------------------------------------------------------------------

    # WAITING FOR HITL: Pause the engine until the human provides a phrase
    if phase == "waiting_for_hitl":
        if not hitl_msgs:
            log.info(
                "orchestrator.decision",
                reason="waiting_for_hitl",
                phase="waiting_for_hitl",
                turn=turn,
            )
            return OrchestratorOutput(
                wait_for_hitl=True,
                game_state_updates={"phase": "waiting_for_hitl"},
            )
        # HITL phrase arrived → Operator introduces game + whispers to Player 1
        log.info(
            "orchestrator.decision",
            reason="hitl_received",
            phase="chain",
            next_player=players[0].name,
            turn=turn,
        )
        return OrchestratorOutput(
            next_agents=[operator.id],
            game_state_updates={
                "phase": "chain",
                "current_player_idx": 0,
                "next_player": players[0].name,
            },
        )

    # CHAIN PHASE: players whisper to each other in sequence
    if phase == "chain":
        return _route_chain(all_msgs, operator, players, player_ids, turn)

    # DISCUSSION PHASE: group tries to work backwards
    if phase == "discussion":
        return _route_discussion(custom, operator, players, turn)

    # REVEAL PHASE: Operator speaks last with "GAME COMPLETE"
    if phase == "reveal":
        log.info(
            "orchestrator.decision",
            reason="reveal",
            phase="reveal",
            turn=turn,
        )
        return OrchestratorOutput(
            next_agents=[operator.id],
            game_state_updates={"phase": "reveal"},
        )

    # Fallback — shouldn't happen, but route to operator
    log.warning(
        "orchestrator.decision",
        reason="fallback",
        phase=phase,
        turn=turn,
    )
    return OrchestratorOutput(next_agents=[operator.id])


# ----------------------------------------------------------------------
# Phase helpers
# ----------------------------------------------------------------------


def _route_chain(all_msgs, operator, players, player_ids, turn):
    """Route within the chain phase: Operator → P1 → P2 → ... → P6."""
    last_msg = all_msgs[-1]
    last_speaker = last_msg.agent_id

    # Operator just spoke (initial handoff) → route to Player 1
    if last_speaker == operator.id:
        log.info(
            "orchestrator.decision",
            reason="operator_handoff_done",
            phase="chain",
            next_agents=[players[0].id],
            turn=turn,
        )
        return OrchestratorOutput(
            next_agents=[players[0].id],
            game_state_updates={
                "phase": "chain",
                "current_player_idx": 0,
            },
        )

    # A player just spoke → route to next player or start discussion
    if last_speaker in player_ids:
        spoke_idx = next(
            i for i, p in enumerate(players) if p.id == last_speaker
        )
        next_idx = spoke_idx + 1

        if next_idx >= len(players):
            # Last player announced publicly → start discussion
            log.info(
                "orchestrator.decision",
                reason="chain_complete",
                phase="discussion",
                turn=turn,
            )
            return OrchestratorOutput(
                next_agents=[operator.id],
                game_state_updates={
                    "phase": "discussion",
                    "discussion_idx": 0,
                },
            )
        else:
            next_player = players[next_idx]
            log.info(
                "orchestrator.decision",
                reason="next_player",
                phase="chain",
                current_player_idx=next_idx,
                next_player=next_player.name,
                turn=turn,
            )
            return OrchestratorOutput(
                next_agents=[next_player.id],
                game_state_updates={
                    "phase": "chain",
                    "current_player_idx": next_idx,
                    "next_player": next_player.name,
                },
            )

    # Fallback within chain
    return OrchestratorOutput(next_agents=[operator.id])


def _route_discussion(custom, operator, players, turn):
    """Route within the discussion phase.

    The Operator already speaks when transitioning from chain → discussion
    (that turn is where they reveal the original phrase). So discussion_idx
    tracks only the player discussion turns:

      0..N-1     → Each player discusses (reveals what they heard / passed on)
      N          → All discussed → final reveal phase
    """
    discussion_idx = custom.get("discussion_idx", 0)

    if discussion_idx < len(players):
        player = players[discussion_idx]
        log.info(
            "orchestrator.decision",
            reason="player_discusses",
            phase="discussion",
            player=player.name,
            discussion_idx=discussion_idx,
            turn=turn,
        )
        return OrchestratorOutput(
            next_agents=[player.id],
            game_state_updates={
                "phase": "discussion",
                "discussion_idx": discussion_idx + 1,
            },
        )

    # All players discussed → final reveal
    log.info(
        "orchestrator.decision",
        reason="discussion_complete",
        phase="reveal",
        turn=turn,
    )
    return OrchestratorOutput(
        next_agents=[operator.id],
        game_state_updates={"phase": "reveal"},
    )


def _handle_timeout(
    timed_out_agent, operator, players, player_ids, phase, custom, turn,
):
    """Skip a timed-out agent to the next in sequence."""
    if phase == "chain":
        if timed_out_agent == operator.id:
            log.info(
                "orchestrator.decision",
                reason="operator_timeout_skip",
                next_agents=[players[0].id],
                turn=turn,
            )
            return OrchestratorOutput(
                next_agents=[players[0].id],
                game_state_updates={
                    "phase": "chain",
                    "current_player_idx": 0,
                },
            )
        if timed_out_agent in player_ids:
            spoke_idx = next(
                i
                for i, p in enumerate(players)
                if p.id == timed_out_agent
            )
            next_idx = spoke_idx + 1
            if next_idx >= len(players):
                return OrchestratorOutput(
                    next_agents=[operator.id],
                    game_state_updates={
                        "phase": "discussion",
                        "discussion_idx": 0,
                    },
                )
            else:
                return OrchestratorOutput(
                    next_agents=[players[next_idx].id],
                    game_state_updates={
                        "phase": "chain",
                        "current_player_idx": next_idx,
                    },
                )

    if phase == "discussion":
        discussion_idx = custom.get("discussion_idx", 0)
        # Advance discussion_idx past the timed-out player
        if timed_out_agent in player_ids:
            next_idx = discussion_idx  # already incremented by game_state
            if next_idx >= len(players):
                return OrchestratorOutput(
                    next_agents=[operator.id],
                    game_state_updates={"phase": "reveal"},
                )
            else:
                return OrchestratorOutput(
                    next_agents=[players[next_idx].id],
                    game_state_updates={
                        "phase": "discussion",
                        "discussion_idx": next_idx + 1,
                    },
                )

    # Fallback
    return OrchestratorOutput(next_agents=[operator.id])
