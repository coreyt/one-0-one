"""MonologuePanel — collapsible bottom strip for agent chain-of-thought."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, RichLog

from src.session.events import MonologueEvent, TurnEvent


class MonologuePanel(Widget):
    """
    Collapsible bottom strip showing the active agent's monologue.

    Hidden by default (height: 0). Toggle via .open CSS class.
    Clears on every TurnEvent. Never receives MessageEvents.
    """

    def compose(self) -> ComposeResult:
        yield Label("", id="mono-header")
        yield RichLog(id="mono-log", highlight=False, markup=True)

    def handle_event(self, event) -> None:
        """Called by EventBus subscription — accepts TURN and MONOLOGUE events."""
        if event.type == "TURN":
            agent_name = event.agent_ids[0] if event.agent_ids else "Agent"
            self._clear(agent_name)
        elif event.type == "MONOLOGUE":
            self._append(event.agent_name, event.text)

    def toggle(self) -> None:
        """Toggle the panel open/closed."""
        self.toggle_class("open")

    def show_placeholder(self, header: str, text: str) -> None:
        """Render a static explanation when monologue capture is unavailable."""
        self.query_one("#mono-log", RichLog).clear()
        self.query_one("#mono-header", Label).update(header)
        self.query_one("#mono-log", RichLog).write(f"[dim]{text}[/dim]")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear(self, next_agent_name: str) -> None:
        self.query_one("#mono-log", RichLog).clear()
        self.query_one("#mono-header", Label).update(
            f"▌ {next_agent_name} — thinking..."
        )

    def _append(self, agent_name: str, text: str) -> None:
        self.query_one("#mono-log", RichLog).write(f"[dim]{text}[/dim]")
