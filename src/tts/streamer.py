"""
Near real-time TTS streaming for live sessions.

SessionTTSStreamer subscribes to a session EventBus and converts each
public MESSAGE event into ElevenLabs audio using the ultra-low-latency
eleven_flash_v2_5 model (~75 ms first-chunk latency). Audio is dispatched
to registered WebSocket consumers as chunks arrive.

Architecture:

    EventBus (sync emit)
        → enqueue_message()       [non-blocking; safe from sync callbacks]
        → asyncio.Queue (work items)
        → _worker coroutine       [sequential; one message at a time]
            → stream_turn()       [ElevenLabs SDK in thread pool via bridge queue]
            → dispatches bytes to audio subscriber queues
        → WebSocket consumers     [bytes frames]

Usage (session_manager.py):

    streamer = SessionTTSStreamer(voice_map)
    streamer.start()

    bus.stream()
        .filter(lambda e: e.type == "MESSAGE" and e.channel_id == "public")
        .subscribe(lambda e: streamer.enqueue_message(e.agent_id, e.text))

    # on session end:
    streamer.stop()

Usage (WebSocket handler):

    audio_q = streamer.add_audio_subscriber()
    try:
        while (chunk := await audio_q.get()) is not None:
            await websocket.send_bytes(chunk)
    finally:
        streamer.remove_audio_subscriber(audio_q)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from src.logging import get_logger
from src.settings import settings
from src.tts.renderer import _strip_markdown

log = get_logger(__name__)

_OUTPUT_FORMAT = "mp3_44100_128"


class SessionTTSStreamer:
    """Per-session near real-time TTS streamer backed by ElevenLabs Flash."""

    def __init__(
        self,
        voice_map: dict[str, str],
        model: str | None = None,
        output_format: str = _OUTPUT_FORMAT,
    ) -> None:
        self._voice_map = voice_map
        self._model = model or settings.tts_streaming_model
        self._output_format = output_format
        # Queue items: (agent_id, text_for_tts, voice_settings_override)
        self._work_queue: asyncio.Queue[tuple[str, str, dict[str, float]] | None] = asyncio.Queue()
        self._audio_queues: list[asyncio.Queue[bytes | None]] = []
        self._worker_task: asyncio.Task | None = None
        self._client: Any | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background worker. Call once after __init__."""
        self._worker_task = asyncio.create_task(
            self._worker(), name="tts-streamer-worker"
        )

    def stop(self) -> None:
        """Cancel the worker and signal end-of-stream to all audio consumers."""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        for q in self._audio_queues:
            q.put_nowait(None)  # None = end-of-stream sentinel

    # ------------------------------------------------------------------
    # Event ingestion — sync, called from EventBus subscriber callbacks
    # ------------------------------------------------------------------

    def enqueue_message(
        self,
        agent_id: str,
        text: str,
        *,
        tts_text: str | None = None,
        voice_settings: dict[str, float] | None = None,
    ) -> None:
        """Put a turn on the work queue. Non-blocking; safe from sync callbacks.

        Args:
            agent_id:      Speaking agent.
            text:          Clean text (fallback when tts_text is absent).
            tts_text:      ElevenLabs-annotated text (preferred over text when present).
            voice_settings: voice_settings overrides from <feeling> tags.
        """
        if agent_id not in self._voice_map:
            return
        effective_text = tts_text if tts_text is not None else text
        self._work_queue.put_nowait((agent_id, effective_text, voice_settings or {}))

    # ------------------------------------------------------------------
    # Audio consumer registration
    # ------------------------------------------------------------------

    def add_audio_subscriber(self) -> asyncio.Queue[bytes | None]:
        """Register a new audio consumer. Returns a queue; None = stream end."""
        q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._audio_queues.append(q)
        return q

    def remove_audio_subscriber(self, queue: asyncio.Queue) -> None:
        try:
            self._audio_queues.remove(queue)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Streaming core
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            from elevenlabs.client import ElevenLabs
            self._client = ElevenLabs(api_key=settings.eleven_labs_api_key)
        return self._client

    async def stream_turn(
        self,
        agent_id: str,
        text: str,
        voice_settings: dict[str, float] | None = None,
    ) -> AsyncIterator[bytes]:
        """
        Async generator — yields MP3 audio bytes for one message turn.

        The ElevenLabs SDK is synchronous; it runs in a thread pool executor
        with a bridge queue so chunks are forwarded to the async generator as
        they arrive from the network, rather than waiting for the full response.

        Args:
            agent_id:      Speaking agent (must be in voice_map).
            text:          Text to synthesise (may contain eleven_v3 [audio_tag]s).
            voice_settings: Per-turn voice_settings overrides from <feeling> tags.
        """
        voice_id = self._voice_map.get(agent_id)
        if not voice_id:
            return

        text = _strip_markdown(text)
        if not text:
            return

        client = self._get_client()
        model = self._model
        output_format = self._output_format
        loop = asyncio.get_running_loop()
        bridge: asyncio.Queue[bytes | None] = asyncio.Queue()

        # Build optional VoiceSettings from feeling-tag overrides
        sdk_voice_settings = None
        if voice_settings:
            from elevenlabs import VoiceSettings
            sdk_voice_settings = VoiceSettings(**voice_settings)

        def _produce() -> None:
            """Run in thread pool — forwards chunks to bridge queue."""
            try:
                kwargs: dict = dict(
                    voice_id=voice_id,
                    text=text,
                    model_id=model,
                    output_format=output_format,
                )
                if sdk_voice_settings is not None:
                    kwargs["voice_settings"] = sdk_voice_settings
                for chunk in client.text_to_speech.stream(**kwargs):
                    if isinstance(chunk, bytes):
                        loop.call_soon_threadsafe(bridge.put_nowait, chunk)
            except Exception as exc:
                log.warning("tts.stream_error", agent_id=agent_id, error=str(exc))
            finally:
                loop.call_soon_threadsafe(bridge.put_nowait, None)

        # Schedule producer in thread pool (do not await — runs concurrently)
        producer_future = loop.run_in_executor(None, _produce)

        # Consume chunks as they arrive
        while True:
            chunk = await bridge.get()
            if chunk is None:
                break
            yield chunk

        # Ensure thread is fully done before returning
        await producer_future

    # ------------------------------------------------------------------
    # Background worker — sequential message processing
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        """Pull work items and stream audio to all registered consumers."""
        while True:
            try:
                item = await self._work_queue.get()
            except asyncio.CancelledError:
                break

            if item is None:
                break

            agent_id, text, voice_settings = item
            log.debug("tts.streaming_turn", agent_id=agent_id, chars=len(text))

            async for chunk in self.stream_turn(agent_id, text, voice_settings or None):
                for q in self._audio_queues:
                    q.put_nowait(chunk)

            self._work_queue.task_done()
