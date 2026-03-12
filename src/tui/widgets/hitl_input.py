"""HITLInputBar — text input for the human participant."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input, Select


class HITLInputBar(Widget):
    """
    HITL message input bar.

    When enabled, stays visible so the human can type at any time.
    Emits HITLInputBar.HITLMessage when the user submits.
    """

    class HITLMessage(Message):
        """Posted when the human submits a message."""

        def __init__(self, text: str, channel_id: str) -> None:
            super().__init__()
            self.text = text
            self.channel_id = channel_id

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._role: str = "Human"

    def compose(self) -> ComposeResult:
        yield Select(
            [("Public", "public"), ("Team", "team")],
            id="channel-select",
            value="public",
            allow_blank=False,
        )
        yield Input(placeholder="Your message...", id="hitl-input")
        yield Button("Send", id="hitl-send", variant="primary")

    def enable(self, role: str | None = None, has_team: bool = False) -> None:
        """Activate the bar for the session. Stays visible until session ends."""
        self._role = role or "Human"
        self.display = True
        self.query_one("#channel-select").display = has_team
        self.query_one("#hitl-input", Input).placeholder = (
            f"Type as {self._role}..."
        )

    def show_for_turn(self, has_team: bool = False) -> None:
        """Make the bar visible and focus the input."""
        self.display = True
        self.query_one("#channel-select").display = has_team
        self.query_one("#hitl-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "hitl-send":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        text = self.query_one("#hitl-input", Input).value.strip()
        channel_select = self.query_one("#channel-select", Select)
        channel = str(channel_select.value) if channel_select.value else "public"
        if text:
            self.post_message(self.HITLMessage(text=text, channel_id=channel))
            self.query_one("#hitl-input", Input).clear()
