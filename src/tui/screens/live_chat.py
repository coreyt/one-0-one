"""LiveChatScreen — active session view with channel tabs, monologue, and agent roster."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header

from src.session.config import SessionConfig
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus
from src.session.events import MessageEvent, SessionEndEvent, TurnEvent
from src.tui.widgets.agent_roster import AgentRoster
from src.tui.widgets.channel_tabs import ChannelTabs
from src.tui.widgets.hitl_input import HITLInputBar
from src.tui.widgets.monologue_panel import MonologuePanel
from src.tui.widgets.turn_indicator import TurnIndicator


class LiveChatScreen(Screen):
    """
    Active session view.

    Layout: horizontal — main column (ChannelTabs + MonologuePanel + HITLInputBar)
    and sidebar (TurnIndicator + AgentRoster).
    """

    CSS_PATH = ["../styles/live_chat.tcss"]

    BINDINGS = [
        ("p", "toggle_pause", "Pause"),
        ("m", "toggle_monologue", "Monologue"),
        ("e", "end_session", "End"),
        ("escape", "go_back", "Back"),
    ]

    def __init__(self, config: SessionConfig) -> None:
        super().__init__()
        self._config = config
        self._engine: SessionEngine | None = None
        self._paused = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        # Main column
        from textual.containers import Container
        with Container(id="main-column"):
            yield ChannelTabs(id="channel-tabs")
            yield MonologuePanel(id="monologue-panel")
            yield HITLInputBar(id="hitl-input-bar")
        # Sidebar
        with Container(id="sidebar"):
            yield TurnIndicator(id="turn-indicator")
            yield AgentRoster(id="agent-roster")
        yield Footer()

    def on_mount(self) -> None:
        # Populate the agent roster immediately
        roster = self.query_one(AgentRoster)
        roster.populate(self._config.agents)
        # Start the session in a background worker
        self.run_worker(self._run_session_worker(), exclusive=True)

    async def _run_session_worker(self) -> None:
        """
        Drives the SessionEngine from a background asyncio worker.
        All widget updates happen via subscription callbacks on the same
        asyncio event loop — no threading needed.
        """
        bus = EventBus()
        channel_tabs = self.query_one(ChannelTabs)
        monologue_panel = self.query_one(MonologuePanel)
        turn_indicator = self.query_one(TurnIndicator)
        roster = self.query_one(AgentRoster)

        # Wire EventBus subscriptions
        bus.stream() \
            .filter(lambda e: e.type == "CHANNEL_CREATED") \
            .subscribe(channel_tabs.add_channel)

        bus.stream() \
            .filter(lambda e: e.type == "MESSAGE") \
            .subscribe(self._on_message)

        bus.stream() \
            .filter(lambda e: e.type in ("MONOLOGUE", "TURN")) \
            .subscribe(monologue_panel.handle_event)

        bus.stream() \
            .filter(lambda e: e.type in ("TURN", "MESSAGE", "SESSION_END")) \
            .subscribe(turn_indicator.handle_turn)

        bus.stream() \
            .filter(lambda e: e.type in ("GAME_STATE", "RULE_VIOLATION")) \
            .subscribe(self._on_system_event)

        bus.stream() \
            .filter(lambda e: e.type == "TURN") \
            .subscribe(self._on_turn)

        bus.stream() \
            .filter(lambda e: e.type == "SESSION_END") \
            .subscribe(self._on_session_end)

        engine = SessionEngine(self._config, bus)
        self._engine = engine
        await engine.run()

    # ------------------------------------------------------------------
    # EventBus handlers
    # ------------------------------------------------------------------

    def _on_message(self, event: MessageEvent) -> None:
        self.query_one(ChannelTabs).append_message(event)

    def _on_system_event(self, event) -> None:
        if event.type == "RULE_VIOLATION":
            text = f"Rule violation — {event.agent_id}: {event.rule}"
            self.query_one(ChannelTabs).append_system(text)
        elif event.type == "GAME_STATE":
            updates = ", ".join(f"{k}={v}" for k, v in event.updates.items())
            self.query_one(ChannelTabs).append_system(f"Game state updated: {updates}")

    def _on_turn(self, event: TurnEvent) -> None:
        # Update agent roster status
        roster = self.query_one(AgentRoster)
        for agent_id in event.agent_ids:
            roster.set_status(agent_id, "thinking")

    def _on_session_end(self, event: SessionEndEvent) -> None:
        self.notify(
            f"Session ended: {event.reason}",
            title="Session Complete",
            timeout=5,
        )

    # ------------------------------------------------------------------
    # HITL
    # ------------------------------------------------------------------

    def on_hitl_input_bar_hitl_message(self, event: HITLInputBar.HITLMessage) -> None:
        if self._engine is not None:
            self._engine.inject_hitl_message(event.text, event.channel_id)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_toggle_pause(self) -> None:
        if self._engine is None:
            return
        if self._paused:
            self._engine.resume()
            self._paused = False
            self.notify("Session resumed", timeout=2)
        else:
            self._engine.pause()
            self._paused = True
            self.notify("Session paused", timeout=2)

    def action_toggle_monologue(self) -> None:
        self.query_one(MonologuePanel).toggle()

    def action_end_session(self) -> None:
        if self._engine is not None:
            self._engine.pause()
        self.notify("Session ended by user", timeout=3)
        self.app.pop_screen()

    def action_go_back(self) -> None:
        self.action_end_session()
