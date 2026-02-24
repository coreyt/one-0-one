"""TurnIndicator — turn counter + agent status label with spinner."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, LoadingIndicator


class TurnIndicator(Widget):
    """Turn counter + agent status label with loading spinner."""

    def compose(self) -> ComposeResult:
        yield Label("Waiting...", id="turn-label")
        yield LoadingIndicator(id="spinner")

    def on_mount(self) -> None:
        self.query_one("#spinner").display = False

    def handle_turn(self, event) -> None:
        """Called by EventBus subscription — accepts TURN, MESSAGE, SESSION_END events."""
        if event.type == "TURN":
            names = ", ".join(event.agent_ids)
            parallel = " [parallel]" if event.is_parallel else ""
            self.query_one("#turn-label", Label).update(
                f"Turn {event.turn_number}{parallel}\n● {names} thinking..."
            )
            self.query_one("#spinner").display = True

        elif event.type == "MESSAGE":
            # First message of a turn — agent is speaking, not thinking
            self.query_one("#spinner").display = False

        elif event.type == "SESSION_END":
            self.query_one("#turn-label", Label).update("Session ended")
            self.query_one("#spinner").display = False
