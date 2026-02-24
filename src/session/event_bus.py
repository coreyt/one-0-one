"""
asyncio-native EventBus with fluent Observable API.

No RxPY — pure asyncio.Queue fan-out with chainable filter/map/subscribe.

Architecture:
    EventBus         — emitter side; the session engine calls emit()
    AsyncStream      — observable side; consumers chain operators and subscribe
    AsyncSubscription — handle returned by subscribe(); call cancel() to stop

Usage:
    bus = EventBus()

    # Subscribe to public messages only
    sub = (
        bus.stream()
           .filter(lambda e: e.type == "MESSAGE" and e.channel_id == "public")
           .map(format_message)
           .subscribe(chat_log.append)
    )

    # Or iterate with async-for in a @work task
    async for event in bus.stream().filter(lambda e: e.type == "TURN"):
        monologue_panel.clear()

    # Emit from the engine
    bus.emit(MessageEvent(...))

    # Tear down
    sub.cancel()
    await bus.close()
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

from src.logging import get_logger

if TYPE_CHECKING:
    from src.session.events import SessionEvent

log = get_logger(__name__)


class AsyncSubscription:
    """Handle for a running subscription task. Call cancel() to stop."""

    def __init__(self, task: asyncio.Task) -> None:
        self._task = task

    def cancel(self) -> None:
        self._task.cancel()

    @property
    def done(self) -> bool:
        return self._task.done()


class AsyncStream:
    """
    Chainable observable stream backed by an asyncio.Queue.

    Each AsyncStream instance has its own queue fed by the EventBus
    fan-out. Chaining filter/map creates a new AsyncStream with its
    own transformation applied at pull time.
    """

    def __init__(self, queue: asyncio.Queue, bus: "EventBus") -> None:
        self._queue: asyncio.Queue = queue
        self._bus = bus
        self._transforms: list[Callable] = []
        self._filters: list[Callable] = []

    # ------------------------------------------------------------------
    # Fluent operators — each returns a new AsyncStream
    # ------------------------------------------------------------------

    def filter(self, predicate: Callable[["SessionEvent"], bool]) -> "AsyncStream":
        """Return a new stream that only passes events matching predicate."""
        child = AsyncStream(asyncio.Queue(), self._bus)
        child._transforms = list(self._transforms)
        child._filters = list(self._filters) + [predicate]
        self._bus._register_child(self, child, predicate=predicate, transform=None)
        return child

    def map(self, transform: Callable[["SessionEvent"], Any]) -> "AsyncStream":
        """Return a new stream with each event transformed."""
        child = AsyncStream(asyncio.Queue(), self._bus)
        child._transforms = list(self._transforms) + [transform]
        child._filters = list(self._filters)
        self._bus._register_child(self, child, predicate=None, transform=transform)
        return child

    def subscribe(self, handler: Callable) -> AsyncSubscription:
        """
        Spawn an asyncio.Task that pulls from this stream and calls handler.

        handler may be sync or async.
        Returns an AsyncSubscription — call .cancel() to stop.
        """
        async def _drain() -> None:
            while True:
                event = await self._queue.get()
                try:
                    if inspect.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        handler(event)
                except Exception:
                    log.exception("eventbus.handler_error", handler=repr(handler))

        task = asyncio.ensure_future(_drain())
        return AsyncSubscription(task)

    def __aiter__(self) -> AsyncIterator:
        """Enable `async for event in stream:` usage."""
        return self._aiter_impl()

    async def _aiter_impl(self) -> AsyncIterator:
        while True:
            event = await self._queue.get()
            yield event


class EventBus:
    """
    Central event emitter for a session.

    One EventBus per session. The engine calls emit(); all consumers
    subscribe via stream().
    """

    def __init__(self) -> None:
        # list of (source_stream_id, child_stream, predicate, transform)
        self._root_streams: list[AsyncStream] = []
        self._children: dict[int, list[tuple[Callable | None, Callable | None, AsyncStream]]] = {}

    def emit(self, event: "SessionEvent") -> None:
        """Emit an event to all root subscribers and their chained children."""
        log.debug(
            "eventbus.emit",
            event_type=event.type,
            session_id=getattr(event, "session_id", None),
            agent_id=getattr(event, "agent_id", None),
        )
        for stream in self._root_streams:
            self._fan_out(stream, event)

    def _fan_out(self, stream: AsyncStream, event: "SessionEvent") -> None:
        """Put event into stream's queue, then propagate to registered children."""
        stream._queue.put_nowait(event)
        children = self._children.get(id(stream), [])
        for predicate, transform, child in children:
            if predicate is not None and not predicate(event):
                continue
            value = transform(event) if transform is not None else event
            self._fan_out(child, value)

    def stream(self) -> AsyncStream:
        """Create and return a new root AsyncStream subscribed to all events."""
        s = AsyncStream(asyncio.Queue(), self)
        self._root_streams.append(s)
        return s

    def _register_child(
        self,
        parent: AsyncStream,
        child: AsyncStream,
        predicate: Callable | None,
        transform: Callable | None,
    ) -> None:
        """Register a child stream derived from a parent via filter/map."""
        parent_id = id(parent)
        if parent_id not in self._children:
            self._children[parent_id] = []
        self._children[parent_id].append((predicate, transform, child))

    async def close(self) -> None:
        """Signal all root streams with a sentinel (None). No more events."""
        for stream in self._root_streams:
            await stream._queue.put(None)
        self._root_streams.clear()
        self._children.clear()
