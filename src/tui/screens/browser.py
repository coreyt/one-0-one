"""SessionBrowserScreen — home screen listing session templates."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static, Tab, Tabs

from src.session.config import SessionConfig, load_session_config
from src.settings import settings
from src.tui.widgets.detail_panel import TemplateDetailPanel


_TYPE_FILTERS = ["All", "Games", "Social", "Research", "Task", "Problem-Solve"]
_TYPE_MAP = {
    "Games": "games",
    "Social": "social",
    "Research": "research",
    "Task": "task-completion",
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
            f"[bold]> {self.config.title}[/bold]  [dim]\\[{setting}][/dim]",
            classes="template-row-title",
        )
        desc = (self.config.description or "")[:80]
        if desc:
            yield Label(f"  [dim]{desc}[/dim]", classes="template-row-description")


class SessionBrowserScreen(Screen):
    """Home screen — browse and launch session templates."""

    CSS_PATH = ["../styles/browser.tcss"]

    BINDINGS = [
        ("enter", "new_session", "New"),
        ("n", "new_session", "New"),
        ("h", "open_history", "History"),
        ("q", "quit_app", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._all_configs: list[SessionConfig] = []
        self._active_filter: str = "All"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Tabs(
            *(Tab(f, id=f"filter_{f.lower().replace('-', '_')}") for f in _TYPE_FILTERS),
            id="type-filter",
            active="filter_all",
        )
        yield Static(
            "Templates: use ↑/↓ to select, Enter or n to open a new run from the selected template.",
            id="browser-instructions",
        )
        with Horizontal(id="browser-split"):
            with Static(id="template-list-pane"):
                yield Label("Templates", id="template-list-title")
                yield ListView(id="template-list")
            yield TemplateDetailPanel(id="detail-panel")
        yield Label("", id="primary-action-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.load_templates()
        self.call_after_refresh(self._focus_template_list)

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

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Update detail panel when a template is highlighted."""
        panel = self.query_one("#detail-panel", TemplateDetailPanel)
        if event.item is not None and isinstance(event.item, TemplateItem):
            panel.show_config(event.item.config)
            self._update_primary_action_hint(event.item.config.title)
        else:
            panel.show_config(None)
            self._update_primary_action_hint(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on the ListView opens a new run from the selected template."""
        if isinstance(event.item, TemplateItem):
            from src.tui.screens.wizard import SetupWizardScreen
            self.app.push_screen(SetupWizardScreen(event.item.config))

    def action_launch(self) -> None:
        self.action_new_session()

    def action_new_session(self) -> None:
        from src.tui.screens.wizard import SetupWizardScreen
        lv = self.query_one(ListView)
        highlighted = lv.highlighted_child
        if highlighted is None:
            try:
                highlighted = next(iter(lv.query(TemplateItem)))
            except StopIteration:
                highlighted = None
        config = highlighted.config if isinstance(highlighted, TemplateItem) else None
        self.app.push_screen(SetupWizardScreen(config))

    def action_open_history(self) -> None:
        from src.tui.screens.history import SessionHistoryScreen
        self.app.push_screen(SessionHistoryScreen())

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
        if list(lv.query(TemplateItem)):
            lv.index = 0
            first = next(iter(lv.query(TemplateItem)))
            self.query_one("#detail-panel", TemplateDetailPanel).show_config(first.config)
            self._update_primary_action_hint(first.config.title)
        else:
            self._update_primary_action_hint(None)

    def _matches_filter(self, config: SessionConfig) -> bool:
        if self._active_filter == "All":
            return True
        expected_type = _TYPE_MAP.get(self._active_filter, "")
        return config.type == expected_type

    def _focus_template_list(self) -> None:
        self.query_one("#template-list", ListView).focus()
        if self._active_filter != "All":
            self._active_filter = "All"
            self._populate_list()

    def _update_primary_action_hint(self, title: str | None) -> None:
        label = self.query_one("#primary-action-hint", Label)
        if title:
            label.update(f"Primary action: Enter or n to create a new run from [bold]{title}[/bold].")
        else:
            label.update("Primary action: choose a template on the left to create a new run.")
