"""
Full Connect Four CLI runner — streams every meaningful event to stdout.

Usage:
    uv run python run_c4.py [template]
    uv run python run_c4.py session-templates/game-connect-four.yaml
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from src.games.connect_four import render_connect_four_board
from src.session.config import load_session_config
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus
from src.session.events import (
    GameStateEvent,
    IncidentEvent,
    MessageEvent,
    MonologueEvent,
    RuleViolationEvent,
    SessionEndEvent,
)

SEP = "─" * 64


def render_board_from_state(gs_event: GameStateEvent) -> str:
    auth = gs_event.updates.get("authoritative_state") or {}
    board = auth.get("board", [])
    if not board:
        return ""
    active = auth.get("active_player", "?")
    winner = auth.get("winner")
    is_draw = auth.get("is_draw", False)
    move_count = auth.get("move_count", "?")

    rendered = render_connect_four_board(board, bordered=True)
    if winner:
        status = f"  WINNER: {winner}"
    elif is_draw:
        status = "  DRAW"
    else:
        status = f"  Move {move_count} — {active} to play"
    return f"{rendered}\n{status}"


async def main(template_path: Path) -> None:
    config = load_session_config(template_path)
    bus = EventBus()

    def on_event(event) -> None:
        if isinstance(event, GameStateEvent):
            delta = event.updates.get("authoritative_delta") or {}
            last_move = delta.get("last_move")
            if last_move:
                disc = last_move.get("disc", "?")
                col = last_move.get("column", "?")
                row = last_move.get("row", "?")
                print(f"\n  >> {disc} dropped in column {col} (row {row})")
            board_str = render_board_from_state(event)
            if board_str:
                print(board_str)

        elif isinstance(event, MonologueEvent):
            print(f"\n  [{event.agent_name} thinking]  {event.text[:400]}")

        elif isinstance(event, MessageEvent) and event.channel_id == "public":
            text = event.text.strip()
            if text:
                print(f"\n[{event.agent_name}]  {text[:400]}")

        elif isinstance(event, RuleViolationEvent):
            print(f"\n  ! RULE VIOLATION ({event.agent_name}): {event.reason}")

        elif isinstance(event, IncidentEvent):
            print(f"\n  ! INCIDENT ({event.agent_name}): {event.incident_type} — {event.detail[:120]}")

        elif isinstance(event, SessionEndEvent):
            print(f"\n{SEP}")
            print(f"SESSION ENDED — {event.reason}")
            print(SEP)

    bus.stream().subscribe(on_event)

    print(f"\nConnect Four: {config.title}")
    print(f"Players: {', '.join(a.name for a in config.agents if a.role == 'player')}")
    print(SEP)

    engine = SessionEngine(config, bus)
    state = await engine.run()

    auth = state.game_state.custom.get("authoritative_state", {})
    winner = auth.get("winner")
    is_draw = auth.get("is_draw", False)
    move_count = auth.get("move_count", 0)
    incidents = state.game_state.incidents

    print(f"\nFinal: {move_count} moves played")
    if winner:
        print(f"Result: {winner} wins")
    elif is_draw:
        print("Result: Draw")
    else:
        print(f"Result: Game ended ({state.end_reason})")
    if incidents:
        print(f"Incidents: {len(incidents)} provider error(s)")

    transcript = sorted(Path("sessions").glob("connect-four*.md"))
    if transcript:
        print(f"Transcript: {transcript[-1]}")


if __name__ == "__main__":
    template = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("session-templates/game-connect-four.yaml")
    asyncio.run(main(template))
