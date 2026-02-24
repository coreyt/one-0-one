"""
Quick CLI runner — runs a session to completion and streams events to stdout.

Usage:
    uv run python run_game.py session-templates/game-mafia-12.yaml
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from src.session.config import load_session_config
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus
from src.session.events import MessageEvent, SessionEndEvent


async def main(template_path: Path) -> None:
    config = load_session_config(template_path)
    bus = EventBus()

    def on_event(event) -> None:
        if isinstance(event, MessageEvent) and event.channel_id == "public":
            print(f"\n[{event.agent_name}]  {event.text[:300]}")
        elif isinstance(event, MessageEvent) and event.channel_id == "mafia":
            print(f"\n  🔒 [MAFIA/{event.agent_name}]  {event.text[:200]}")
        elif isinstance(event, SessionEndEvent):
            print(f"\n{'='*60}")
            print(f"SESSION ENDED — {event.reason}")
            print(f"{'='*60}")

    bus.stream().subscribe(on_event)

    print(f"Starting: {config.title}  ({len(config.agents)} agents)")
    print(f"Topic: {config.topic}")
    print("─" * 60)

    engine = SessionEngine(config, bus)
    await engine.run()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python run_game.py <template.yaml>")
        sys.exit(1)
    asyncio.run(main(Path(sys.argv[1])))
