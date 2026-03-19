"""LiveChatScreen — active session view with channel tabs, monologue, and agent roster."""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header

from src.games.connect_four import render_connect_four_board
from src.session.config import SessionConfig
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus
from src.session.events import IncidentEvent, MessageEvent, SessionEndEvent, TurnEvent
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
        # Enable HITL input bar if configured
        if self._config.hitl.enabled:
            hitl_bar = self.query_one(HITLInputBar)
            role = self._human_display_name()
            hitl_bar.enable(
                role=role,
                has_team=self._human_has_team_channel(),
            )
            if self._is_hitl_player_mode():
                hitl_bar.display = False
                self.query_one(MonologuePanel).display = False
        elif not self._session_supports_monologue():
            self.query_one(MonologuePanel).show_placeholder(
                "▌ Monologue capture disabled",
                "This session is configured for gameplay-only output.",
            )
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
            .subscribe(self._handle_channel_created)

        bus.stream() \
            .filter(lambda e: e.type == "MESSAGE") \
            .subscribe(self._handle_chat_message)

        if not self._is_hitl_player_mode() and self._session_supports_monologue():
            bus.stream() \
                .filter(lambda e: e.type in ("MONOLOGUE", "TURN")) \
                .subscribe(monologue_panel.handle_event)

        bus.stream() \
            .filter(lambda e: e.type in ("TURN", "MESSAGE", "SESSION_END")) \
            .subscribe(turn_indicator.handle_turn)

        bus.stream() \
            .filter(lambda e: e.type in ("GAME_STATE", "RULE_VIOLATION", "INCIDENT")) \
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

    def _handle_channel_created(self, event) -> None:
        if self._human_can_see_channel(event.channel_id, event.channel_type, event.members):
            self.query_one(ChannelTabs).add_channel(event)

    def _handle_chat_message(self, event: MessageEvent) -> None:
        if self._is_message_visible_to_human(event):
            self.query_one(ChannelTabs).append_message(event)

    def _on_system_event(self, event) -> None:
        if event.type == "RULE_VIOLATION":
            if self._is_hitl_player_mode() and event.agent_id != self._config.hitl.participant_agent_id:
                return
            text = f"Rule violation — {event.agent_id}: {event.rule}"
            self.query_one(ChannelTabs).append_system(text)
        elif event.type == "GAME_STATE":
            summary = self._human_game_state_summary()
            if summary:
                self.query_one(ChannelTabs).append_system(summary)
        elif event.type == "INCIDENT":
            self.query_one(ChannelTabs).append_system(self._incident_summary(event))

    def _on_turn(self, event: TurnEvent) -> None:
        # Update agent roster status
        roster = self.query_one(AgentRoster)
        for agent_id in event.agent_ids:
            roster.set_status(agent_id, "thinking")
        turn_summary = self._turn_summary(event)
        if turn_summary:
            self.query_one(ChannelTabs).append_system(turn_summary)
        if self._config.hitl.enabled:
            hitl_bar = self.query_one(HITLInputBar)
            if self._is_hitl_player_mode():
                if self._config.hitl.participant_agent_id in event.agent_ids:
                    hitl_bar.show_for_turn(has_team=self._human_has_team_channel())
                else:
                    hitl_bar.display = False

    def _on_session_end(self, event: SessionEndEvent) -> None:
        if self._config.hitl.enabled:
            self.query_one(HITLInputBar).display = False
        if event.reason == "error":
            self.notify(
                "Session ended due to an error. Check the logs for details.",
                title="Session Error",
                severity="error",
                timeout=10,
            )
        else:
            self.notify(
                f"Session ended: {event.reason}",
                title="Session Complete",
                timeout=5,
            )

    # ------------------------------------------------------------------
    # HITL
    # ------------------------------------------------------------------

    def on_hitlinput_bar_hitlmessage(self, event: HITLInputBar.HITLMessage) -> None:
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
        if self._is_hitl_player_mode():
            self.notify("Monologue is hidden in human player mode.", timeout=2)
            return
        self.query_one(MonologuePanel).toggle()

    def action_end_session(self) -> None:
        if self._engine is not None:
            self._engine.pause()
        self.notify("Session ended by user", timeout=3)
        self.app.pop_screen()

    def action_go_back(self) -> None:
        self.action_end_session()

    def _is_hitl_player_mode(self) -> bool:
        return (
            self._config.hitl.enabled
            and self._config.type == "games"
            and self._config.hitl.mode == "player"
            and self._config.hitl.participant_agent_id is not None
        )

    def _human_display_name(self) -> str:
        if self._is_hitl_player_mode():
            participant_id = self._config.hitl.participant_agent_id
            participant = next(
                (agent for agent in self._config.agents if agent.id == participant_id),
                None,
            )
            if participant is not None:
                return participant.name
        return self._config.hitl.role or "Human"

    def _human_has_team_channel(self) -> bool:
        if not self._is_hitl_player_mode():
            return False
        participant_id = self._config.hitl.participant_agent_id
        participant = next(
            (agent for agent in self._config.agents if agent.id == participant_id),
            None,
        )
        return participant is not None and participant.team is not None

    def _session_supports_monologue(self) -> bool:
        return any(agent.monologue for agent in self._config.agents)

    def _human_can_see_channel(
        self,
        channel_id: str,
        channel_type: str,
        members: list[str],
    ) -> bool:
        if not self._is_hitl_player_mode():
            return True
        if self._config.hitl.see_non_public_information:
            return True
        participant_id = self._config.hitl.participant_agent_id
        if channel_id == "public" or channel_type == "public":
            return True
        if channel_type == "team":
            return participant_id in members
        if channel_type == "private":
            return participant_id in members
        return False

    def _is_message_visible_to_human(self, event: MessageEvent) -> bool:
        if not self._is_hitl_player_mode():
            return True
        if self._config.hitl.see_non_public_information:
            return True
        participant_id = self._config.hitl.participant_agent_id
        if event.channel_id == "public":
            return True
        if event.recipient_id is not None:
            return participant_id in {event.agent_id, event.recipient_id}
        return any(
            channel.id == event.channel_id
            and channel.type == "team"
            and participant_id in channel.members
            for channel in self._config.channels
        )

    def _human_game_state_summary(self) -> str | None:
        if self._engine is None:
            return None
        if self._engine._game_runtime is None:
            return "Game state updated."
        game_type = self._engine._state.game_state.custom.get("game_type") if self._engine._state else None
        if self._config.hitl.see_non_public_information:
            payload = self._engine._game_runtime.state.model_dump()
            if game_type == "connect_four":
                return self._format_connect_four_summary(payload, prefix="Authoritative board")
            return f"Game state updated: {json.dumps(payload)}"
        participant_id = self._config.hitl.participant_agent_id
        if participant_id is None:
            authoritative = self._engine._state.game_state.custom.get("authoritative_state") if self._engine._state else None
            if game_type == "connect_four" and isinstance(authoritative, dict):
                return self._format_connect_four_summary(authoritative, prefix="Authoritative board")
            return "Game state updated."
        visible_state = self._engine._game_runtime.visible_state(participant_id).model_dump()
        if game_type == "connect_four":
            return self._format_connect_four_summary(visible_state.get("payload", {}), prefix="Your game view")
        return f"Your game view: {json.dumps(visible_state)}"

    def _turn_summary(self, event: TurnEvent) -> str | None:
        if not event.agent_ids:
            return None
        names = [
            next((agent.name for agent in self._config.agents if agent.id == agent_id), agent_id)
            for agent_id in event.agent_ids
        ]
        if len(names) == 1:
            return f"Turn {event.turn_number}: {names[0]} is thinking."
        return f"Turn {event.turn_number}: {', '.join(names)} are thinking."

    def _incident_summary(self, event: IncidentEvent) -> str:
        detail = event.detail.strip().replace("\n", " ")
        if len(detail) > 180:
            detail = f"{detail[:177]}..."
        label = "Timeout" if event.incident_type == "timeout" else "Provider error"
        return f"{label} — {event.agent_name} ({event.model}): {detail}"

    @staticmethod
    def _format_connect_four_summary(payload: dict, prefix: str = "Game state updated") -> str:
        board = payload.get("board")
        if not isinstance(board, list):
            return f"{prefix}: {json.dumps(payload)}"
        rendered_board = render_connect_four_board(board, bordered=True, empty_cell="·")
        lines = [
            prefix,
            f"Active player: {payload.get('active_player')}",
            f"Winner: {payload.get('winner')}",
            f"Draw: {payload.get('is_draw')}",
            rendered_board,
        ]
        return "\n".join(lines)
