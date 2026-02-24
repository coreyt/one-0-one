"""
OneOhOneApp — Textual TUI entry point.

Launch with:
    uv run one-0-one
or:
    python -m src.tui.app
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from src.logging import configure_logging
from src.tui.screens.browser import SessionBrowserScreen


class OneOhOneApp(App):
    """one-0-one: multi-agent conversation sessions in your terminal."""

    TITLE = "one-0-one"
    CSS_PATH = ["styles/app.tcss"]

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
    ]

    def on_mount(self) -> None:
        self.push_screen(SessionBrowserScreen())

    def action_quit(self) -> None:
        self.exit()


def main() -> None:
    configure_logging()
    app = OneOhOneApp()
    app.run()


if __name__ == "__main__":
    main()
