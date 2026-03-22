"""
SessionManager — in-process registry of running sessions.

One SessionManager instance (module-level singleton) holds all active sessions.
The FastAPI app creates sessions via session_manager.start() and subscribes
SSE consumers via session_manager.add_sse_subscriber().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from src.logging import get_logger
from src.session.config import SessionConfig
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus
from src.settings import settings

log = get_logger(__name__)

_VOICE_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass
class ActiveSession:
    session_id: str
    config: SessionConfig
    engine: SessionEngine
    bus: EventBus
    task: asyncio.Task
    sse_queues: list[asyncio.Queue] = field(default_factory=list)
    streamer: Any | None = None  # SessionTTSStreamer | None


class SessionManager:
    """Registry of active sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ActiveSession] = {}

    async def start(self, config: SessionConfig) -> ActiveSession:
        """
        Start a new session and return the ActiveSession handle.

        Creates EventBus, SessionEngine, wires SSE fan-out subscription,
        optionally initialises a SessionTTSStreamer (when tts_enabled=true and
        an ElevenLabs API key is present), and launches engine.run() as a
        background asyncio task.
        """
        bus = EventBus()
        engine = SessionEngine(config, bus)
        session_id = engine._session_id

        # --- TTS streamer (optional) ---
        streamer = None
        if settings.tts_enabled and settings.eleven_labs_api_key:
            streamer = await _init_streamer(engine)
            if streamer is not None:
                streamer.start()

        active = ActiveSession(
            session_id=session_id,
            config=config,
            engine=engine,
            bus=bus,
            task=asyncio.create_task(engine.run(), name=f"session-{session_id}"),
            streamer=streamer,
        )

        # Fan-out: every event → all SSE subscriber queues
        bus.stream().subscribe(
            lambda e: [
                q.put_nowait(e.model_dump_json())
                for q in active.sse_queues
            ]
        )

        # TTS: forward public MESSAGE events to the streamer work queue
        if streamer is not None:
            bus.stream().filter(
                lambda e: e.type == "MESSAGE" and e.channel_id == "public"
            ).subscribe(
                lambda e: streamer.enqueue_message(e.agent_id, e.text)
            )

        self._sessions[session_id] = active
        log.info("session_manager.started", session_id=session_id, tts=streamer is not None)
        return active

    def get(self, session_id: str) -> ActiveSession | None:
        return self._sessions.get(session_id)

    def end(self, session_id: str) -> None:
        """Cancel a running session task."""
        active = self._sessions.get(session_id)
        if active is None:
            return
        active.task.cancel()
        if active.streamer is not None:
            active.streamer.stop()
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


# ---------------------------------------------------------------------------
# TTS initialisation helper
# ---------------------------------------------------------------------------


def _build_voice_map_sync(agent_dicts: list[dict], seed: int) -> dict[str, str]:
    """
    Fetch ElevenLabs voices and build agent_id → voice_id mapping.
    Runs synchronously — call via asyncio.to_thread().
    """
    from elevenlabs.client import ElevenLabs
    from src.tts.voices import assign_voices

    client = ElevenLabs(api_key=settings.eleven_labs_api_key)
    all_voices: list[Any] = []
    next_page_token: str | None = None
    while True:
        page = client.voices.search(
            page_size=100,
            include_total_count=False,
            next_page_token=next_page_token,
        )
        all_voices.extend(page.voices)
        if not page.has_more:
            break
        next_page_token = page.next_page_token

    return assign_voices(agent_dicts, all_voices, seed=seed)


async def _init_streamer(engine: SessionEngine) -> Any | None:
    """
    Build voice map and return an (unstarted) SessionTTSStreamer, or None on error.

    Personalities are assigned during SessionEngine.__init__, so
    engine.config.agents already carries persona data at this point.
    """
    from src.tts.streamer import SessionTTSStreamer

    seed = int((datetime.now(UTC) - _VOICE_EPOCH).total_seconds())
    agent_dicts = [a.model_dump() for a in engine.config.agents]

    try:
        voice_map = await asyncio.to_thread(_build_voice_map_sync, agent_dicts, seed)
        log.info(
            "tts.voices_assigned",
            session_id=engine._session_id,
            agents=list(voice_map.keys()),
            seed=seed,
        )
        return SessionTTSStreamer(voice_map)
    except Exception as exc:
        log.warning("tts.streamer_init_failed", error=str(exc))
        return None


# Module-level singleton
session_manager = SessionManager()
