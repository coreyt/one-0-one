"""Tests for the asyncio-native EventBus and AsyncStream."""

import asyncio
from datetime import UTC, datetime

import pytest

from src.session.event_bus import EventBus
from src.session.events import MessageEvent, MonologueEvent, TurnEvent

NOW = datetime.now(UTC)


def _msg(channel: str = "public", text: str = "hello") -> MessageEvent:
    return MessageEvent(
        timestamp=NOW,
        turn_number=1,
        session_id="s1",
        agent_id="a1",
        agent_name="Nova",
        model="anthropic/claude-sonnet-4-6",
        channel_id=channel,
        text=text,
    )


def _turn() -> TurnEvent:
    return TurnEvent(
        timestamp=NOW, turn_number=1, session_id="s1", agent_ids=["a1"]
    )


def _mono() -> MonologueEvent:
    return MonologueEvent(
        timestamp=NOW, turn_number=1, session_id="s1",
        agent_id="a1", agent_name="Nova", text="thinking..."
    )


class TestEventBusEmitAndSubscribe:
    async def test_subscriber_receives_events(self):
        bus = EventBus()
        received = []
        bus.stream().subscribe(received.append)

        bus.emit(_msg())
        bus.emit(_turn())
        await asyncio.sleep(0.05)  # allow task to drain queue

        assert len(received) == 2

    async def test_multiple_subscribers_each_receive(self):
        bus = EventBus()
        rx1, rx2 = [], []
        bus.stream().subscribe(rx1.append)
        bus.stream().subscribe(rx2.append)

        bus.emit(_msg())
        await asyncio.sleep(0.05)

        assert len(rx1) == 1
        assert len(rx2) == 1

    async def test_no_cross_contamination(self):
        """Events emitted before a stream is created are not received by it."""
        bus = EventBus()
        bus.emit(_msg(text="before"))

        received = []
        bus.stream().subscribe(received.append)
        bus.emit(_msg(text="after"))
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].text == "after"


class TestFilterOperator:
    async def test_filter_passes_matching_events(self):
        bus = EventBus()
        received = []
        bus.stream().filter(lambda e: e.type == "MESSAGE").subscribe(received.append)

        bus.emit(_msg())
        bus.emit(_turn())
        bus.emit(_mono())
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].type == "MESSAGE"

    async def test_filter_channel(self):
        bus = EventBus()
        received = []
        bus.stream() \
           .filter(lambda e: e.type == "MESSAGE" and e.channel_id == "public") \
           .subscribe(received.append)

        bus.emit(_msg(channel="public"))
        bus.emit(_msg(channel="team_red"))
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0].channel_id == "public"


class TestMapOperator:
    async def test_map_transforms_values(self):
        bus = EventBus()
        received = []
        bus.stream() \
           .filter(lambda e: e.type == "MESSAGE") \
           .map(lambda e: e.text.upper()) \
           .subscribe(received.append)

        bus.emit(_msg(text="hello"))
        await asyncio.sleep(0.05)

        assert received == ["HELLO"]


class TestAsyncIteration:
    async def test_async_for_yields_events(self):
        bus = EventBus()
        stream = bus.stream().filter(lambda e: e.type == "TURN")

        results = []

        async def collect():
            async for event in stream:
                results.append(event)
                if len(results) >= 2:
                    break

        task = asyncio.ensure_future(collect())
        bus.emit(_turn())
        bus.emit(_msg())   # filtered out
        bus.emit(_turn())
        await asyncio.sleep(0.05)
        task.cancel()

        assert len(results) == 2
        assert all(e.type == "TURN" for e in results)


class TestMonologueNeverInOtherContext:
    """Monologue events must be filterable (engine ensures they never go to agents)."""

    async def test_monologue_events_emitted(self):
        bus = EventBus()
        all_events = []
        monologue_only = []

        bus.stream().subscribe(all_events.append)
        bus.stream().filter(lambda e: e.type == "MONOLOGUE").subscribe(monologue_only.append)

        bus.emit(_mono())
        bus.emit(_msg())
        await asyncio.sleep(0.05)

        assert len(all_events) == 2
        assert len(monologue_only) == 1
