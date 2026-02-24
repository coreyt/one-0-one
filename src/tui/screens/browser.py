"""SessionBrowserScreen — home screen listing session templates."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Tab, Tabs

from src.session.config import SessionConfig, load_session_config
from src.settings import settings


_TYPE_FILTERS = ["All", "Games", "Social", "Research", "Task", "Problem-Solve"]
_TYPE_MAP = {
    "Games": "game",
    "Social": "social",
    "Research": "research",
    "Task": "task",
    "Problem-Solve": "problem-solve",
}


class TemplateItem(ListItem):
    """A list item representing a session template."""

    def __init__(self, config: SessionConfig) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        setting = self.config.setting or "general"
        yield Label(
            f"[bold]{self.config.title}[/bold]  [dim]\\[{setting}][/dim]"
        )
        desc = (self.config.description or "")[:80]
        if desc:
            yield Label(f"  [dim]{desc}[/dim]")


class SessionBrowserScreen(Screen):
    """Home screen — browse and launch session templates."""

    CSS_PATH = ["../styles/browser.tcss"]

    BINDINGS = [
        ("enter", "launch", "Launch"),
        ("n", "new_session", "New"),
        ("q", "quit_app", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._all_configs: list[SessionConfig] = []
        self._active_filter: str = "All"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Tabs(id="type-filter"):
            for f in _TYPE_FILTERS:
                yield Tab(f, id=f"filter_{f.lower().replace('-', '_')}")
        yield ListView(id="template-list")
        yield Footer()

    def on_mount(self) -> None:
        self.load_templates()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        tab_id = event.tab.id or ""
        label = tab_id.removeprefix("filter_").replace("_", "-").title()
        # Normalise label back to display label
        for f in _TYPE_FILTERS:
            if f.lower().replace("-", "_") == tab_id.removeprefix("filter_"):
                label = f
                break
        self._active_filter = label
        self._populate_list()

    def action_launch(self) -> None:
        lv = self.query_one(ListView)
        highlighted = lv.highlighted_child
        if highlighted is not None and isinstance(highlighted, TemplateItem):
            # Import here to avoid circular; LiveChatScreen imported lazily
            from src.tui.screens.live_chat import LiveChatScreen
            self.app.push_screen(LiveChatScreen(highlighted.config))

    def action_new_session(self) -> None:
        from src.tui.screens.wizard import SetupWizardScreen
        self.app.push_screen(SetupWizardScreen())

    def action_quit_app(self) -> None:
        self.app.action_quit()

    # ------------------------------------------------------------------
    # Template loading
    # ------------------------------------------------------------------

    def load_templates(self) -> None:
        """Load templates from disk in a worker task."""
        self.run_worker(self._load_templates_async(), exclusive=True)

    async def _load_templates_async(self) -> None:
        templates_dir = Path(settings.session_templates_path)
        configs: list[SessionConfig] = []
        if templates_dir.exists():
            for path in sorted(templates_dir.glob("*.yaml")):
                try:
                    configs.append(load_session_config(path))
                except Exception:
                    pass  # skip invalid templates silently
        self._all_configs = configs
        self._populate_list()

    def _populate_list(self) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        for cfg in self._all_configs:
            if self._matches_filter(cfg):
                lv.append(TemplateItem(cfg))

    def _matches_filter(self, config: SessionConfig) -> bool:
        if self._active_filter == "All":
            return True
        expected_type = _TYPE_MAP.get(self._active_filter, "")
        return (config.setting or "").lower() == expected_type
