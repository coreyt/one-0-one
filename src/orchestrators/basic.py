"""
Basic round-robin Python orchestrator.

Implements orchestration logic:
    - Skip eliminated agents (REQ-PERF-002)
    - Team parallelization: batch consecutive same-team agents (REQ-PERF-003)
    - DM parallelization: batch consecutive no-team agents (REQ-PERF-004)
    - max_turns / max_rounds enforcement
    - completion_signal heuristic (string match in recent messages)
    - No game-rule enforcement (use a game-specific orchestrator for that)

This is the default orchestrator when none is specified in a session template.
"""

from __future__ import annotations

from src.logging import get_logger
from src.orchestrators import OrchestratorInput, OrchestratorOutput

log = get_logger(__name__)

# How many recent messages to scan for completion_signal
_COMPLETION_SCAN_WINDOW = 5


def orchestrate(input: OrchestratorInput) -> OrchestratorOutput:
    """
    Determine the next agent(s) to speak.

    Applies round-robin ordering over the *active* (non-eliminated) agents.
    Consecutive agents sharing the same team are batched into a single parallel
    turn. Consecutive no-team agents are also batched.
    """
    config = input.config
    state = input.state
    turn = state.turn_number

    # ----------------------------------------------------------------
    # Termination checks (evaluated before selecting next agent)
    # ----------------------------------------------------------------

    # 1. max_turns cap
    if config.max_turns is not None and turn >= config.max_turns:
        log.info(
            "orchestrator.decision",
            reason="max_turns",
            turn=turn,
            max_turns=config.max_turns,
        )
        return OrchestratorOutput(session_end=True, end_reason="max_turns")

    # 2. Game max_rounds cap
    if (
        config.game is not None
        and config.game.max_rounds is not None
        and state.game_state.round >= config.game.max_rounds
    ):
        log.info(
            "orchestrator.decision",
            reason="max_rounds",
            round=state.game_state.round,
        )
        return OrchestratorOutput(session_end=True, end_reason="win_condition")

    # 3. completion_signal heuristic — scan recent public messages
    if config.completion_signal:
        recent_messages = [
            e
            for e in state.events[-_COMPLETION_SCAN_WINDOW:]
            if e.type == "MESSAGE" and e.channel_id == "public"
        ]
        signal_lower = config.completion_signal.lower()
        for msg in recent_messages:
            if signal_lower in msg.text.lower():
                log.info(
                    "orchestrator.decision",
                    reason="completion_signal",
                    signal=config.completion_signal,
                )
                return OrchestratorOutput(
                    session_end=True, end_reason="completion_signal"
                )

    # ----------------------------------------------------------------
    # Build active agent list (REQ-PERF-002: skip eliminated agents)
    # ----------------------------------------------------------------
    eliminated = set(state.game_state.eliminated)
    active_agents = [a for a in config.agents if a.id not in eliminated]

    if not active_agents:
        log.info("orchestrator.decision", reason="all_eliminated")
        return OrchestratorOutput(session_end=True, end_reason="win_condition")

    # ----------------------------------------------------------------
    # Round-robin next-agent selection
    # ----------------------------------------------------------------
    next_idx = turn % len(active_agents)
    next_agent = active_agents[next_idx]

    # ----------------------------------------------------------------
    # Batching (REQ-PERF-003 and REQ-PERF-004)
    # ----------------------------------------------------------------
    batch = _build_batch(active_agents, next_idx)

    advance = len(batch)

    log.info(
        "orchestrator.decision",
        reason="round_robin",
        next_agents=batch,
        advance_turns=advance,
        turn=turn,
    )

    return OrchestratorOutput(next_agents=batch, advance_turns=advance)


def _build_batch(active_agents: list, start_idx: int) -> list[str]:
    """
    Return a batch of agent IDs starting at start_idx.

    REQ-PERF-003: If the starting agent has a team, collect all consecutive
    agents (forward, no wrap) that share the same team — they run in parallel.

    REQ-PERF-004: If the starting agent has no team, collect all consecutive
    no-team agents — they run in parallel (independent private actions / DMs).

    A single agent always returns a batch of 1.
    """
    n = len(active_agents)
    first = active_agents[start_idx]
    batch = [first.id]

    for offset in range(1, n):
        idx = (start_idx + offset) % n
        candidate = active_agents[idx]

        if first.team is not None:
            # Team batch: only include agents on the same team
            if candidate.team == first.team:
                batch.append(candidate.id)
            else:
                break
        else:
            # DM / solo batch: include consecutive no-team agents
            if candidate.team is None:
                batch.append(candidate.id)
            else:
                break

    return batch
