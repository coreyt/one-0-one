"""
SessionManager — in-process registry of running sessions.

One SessionManager instance (module-level singleton) holds all active sessions.
The FastAPI app creates sessions via session_manager.start() and subscribes
SSE consumers via session_manager.add_sse_subscriber().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.logging import get_logger
from src.session.config import SessionConfig
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus

log = get_logger(__name__)


@dataclass
class ActiveSession:
    session_id: str
    config: SessionConfig
    engine: SessionEngine
    bus: EventBus
    task: asyncio.Task
    sse_queues: list[asyncio.Queue] = field(default_factory=list)


class SessionManager:
    """Registry of active sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ActiveSession] = {}

    def start(self, config: SessionConfig) -> ActiveSession:
        """
        Start a new session and return the ActiveSession handle.

        Creates EventBus, SessionEngine, wires SSE fan-out subscription,
        and launches engine.run() as a background asyncio task.
        """
        bus = EventBus()
        engine = SessionEngine(config, bus)
        session_id = engine._session_id

        active = ActiveSession(
            session_id=session_id,
            config=config,
            engine=engine,
            bus=bus,
            task=asyncio.create_task(engine.run(), name=f"session-{session_id}"),
        )

        # Fan-out: every event → all SSE subscriber queues
        bus.stream().subscribe(
            lambda e: [
                q.put_nowait(e.model_dump_json())
                for q in active.sse_queues
            ]
        )

        self._sessions[session_id] = active
        log.info("session_manager.started", session_id=session_id)
        return active

    def get(self, session_id: str) -> ActiveSession | None:
        return self._sessions.get(session_id)

    def end(self, session_id: str) -> None:
        """Cancel a running session task."""
        active = self._sessions.get(session_id)
        if active is None:
            return
        active.task.cancel()
        del self._sessions[session_id]
        log.info("session_manager.ended", session_id=session_id)

    def add_sse_subscriber(self, session_id: str) -> asyncio.Queue:
        """Register a new SSE subscriber and return its queue."""
        active = self._sessions.get(session_id)
        if active is None:
            raise KeyError(session_id)
        q: asyncio.Queue = asyncio.Queue()
        active.sse_queues.append(q)
        return q

    def remove_sse_subscriber(self, session_id: str, queue: asyncio.Queue) -> None:
        """Remove an SSE subscriber queue (called on disconnect)."""
        active = self._sessions.get(session_id)
        if active is None:
            return
        try:
            active.sse_queues.remove(queue)
        except ValueError:
            pass


# Module-level singleton
session_manager = SessionManager()
