"""SessionHistoryScreen — browse and view saved session transcripts."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, RichLog

from src.session.config import TranscriptConfig


# Pattern: {slug}_{setting}_{YYYYMMDD_HHMMSS}.md
_FILENAME_RE = re.compile(
    r"^(.+?)_(.+?)_(\d{8}_\d{6})\.md$"
)


class SessionItem(ListItem):
    """A list item representing a saved session transcript."""

    def __init__(self, path: Path, title: str, setting: str, date_str: str) -> None:
        super().__init__()
        self.path = path
        self.title = title
        self.setting = setting
        self.date_str = date_str

    def compose(self) -> ComposeResult:
        yield Label(f"[bold]{self.title}[/bold]")
        size_kb = self.path.stat().st_size / 1024
        yield Label(f"  [dim]{self.setting}  {self.date_str}  {size_kb:.0f}KB[/dim]")


class SessionHistoryScreen(Screen):
    """Browse saved session transcripts from ./sessions/."""

    CSS_PATH = ["../styles/history.tcss"]

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("d", "delete_session", "Delete"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._session_paths: list[Path] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="history-split"):
            yield ListView(id="history-list")
            yield RichLog(id="transcript-view", wrap=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_sessions(), exclusive=True)

    async def _load_sessions(self) -> None:
        sessions_dir = Path(TranscriptConfig().path)
        if not sessions_dir.exists():
            self.query_one("#transcript-view", RichLog).write(
                "[dim]No sessions directory found.[/dim]"
            )
            return

        md_files = sorted(
            sessions_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        lv = self.query_one("#history-list", ListView)
        for path in md_files:
            match = _FILENAME_RE.match(path.name)
            if match:
                slug, setting, ts = match.groups()
                title = slug.replace("-", " ").title()
                try:
                    dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
                    date_str = dt.strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    date_str = ts
            else:
                title = path.stem.replace("-", " ").replace("_", " ").title()
                setting = "—"
                date_str = datetime.fromtimestamp(path.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
            item = SessionItem(path, title, setting, date_str)
            self._session_paths.append(path)
            lv.append(item)

        if not md_files:
            self.query_one("#transcript-view", RichLog).write(
                "[dim]No session transcripts found in ./sessions/[/dim]"
            )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Load transcript content when a session is highlighted."""
        view = self.query_one("#transcript-view", RichLog)
        if event.item is not None and isinstance(event.item, SessionItem):
            self.run_worker(
                self._load_transcript(event.item.path), exclusive=True
            )
        else:
            view.clear()

    async def _load_transcript(self, path: Path) -> None:
        """Load transcript file content in a worker to avoid blocking."""
        view = self.query_one("#transcript-view", RichLog)
        view.clear()
        try:
            content = path.read_text(encoding="utf-8")
            # Write in chunks to avoid overwhelming the RichLog
            for line in content.splitlines():
                view.write(line)
        except Exception as exc:
            view.write(f"[red]Error loading transcript: {exc}[/red]")

    def action_delete_session(self) -> None:
        lv = self.query_one("#history-list", ListView)
        highlighted = lv.highlighted_child
        if highlighted is not None and isinstance(highlighted, SessionItem):
            path = highlighted.path
            # Delete the file and its checkpoint/mp3 siblings
            for ext in [".md", ".checkpoint.json", ".checkpoint.mp3"]:
                sibling = path.with_suffix(ext)
                if sibling.exists():
                    sibling.unlink()
            self.notify(f"Deleted: {path.name}", title="Session deleted")
            # Refresh the list
            self.query_one("#transcript-view", RichLog).clear()
            self.run_worker(self._reload_list(), exclusive=True)

    async def _reload_list(self) -> None:
        lv = self.query_one("#history-list", ListView)
        lv.clear()
        self._session_paths.clear()
        await self._load_sessions()

    def action_go_back(self) -> None:
        self.app.pop_screen()
