"""SetupWizardScreen — 5-tab session config editor."""

from __future__ import annotations

import yaml
from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
    Switch,
    Tab,
    Tabs,
    TextArea,
)

from src.session.config import AgentConfig, SessionConfig
from src.settings import settings


_SETTINGS = ["social", "research", "game", "task", "problem-solve"]


class SetupWizardScreen(Screen):
    """Create or edit a session config then launch or save."""

    CSS_PATH = ["../styles/wizard.tcss"]

    BINDINGS = [
        ("s", "save_template", "Save"),
        ("r", "run_session", "Run"),
        ("escape", "go_back", "Back"),
    ]

    def __init__(self, config: SessionConfig | None = None) -> None:
        super().__init__()
        self._config = config
        self._validation_error: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Tabs(id="wizard-tabs"):
            yield Tab("Topic", id="tab_topic")
            yield Tab("Setting", id="tab_setting")
            yield Tab("Agents", id="tab_agents")
            yield Tab("Orchestrator", id="tab_orchestrator")
            yield Tab("HITL", id="tab_hitl")

        # Topic tab content
        yield Static(id="topic-pane")
        yield Static(id="setting-pane")
        yield Static(id="agents-pane")
        yield Static(id="orchestrator-pane")
        yield Static(id="hitl-pane")

        yield Label("", id="validation-error")
        yield Footer()

    def on_mount(self) -> None:
        self._build_topic_pane()
        self._show_pane("topic")
        # Populate from existing config if editing
        if self._config:
            self._populate_from_config()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        pane_name = event.tab.id.removeprefix("tab_")
        self._show_pane(pane_name)

    def action_save_template(self) -> None:
        config = self._build_config()
        if config is None:
            return
        self.run_worker(self._save_async(config), exclusive=True)

    def action_run_session(self) -> None:
        config = self._build_config()
        if config is None:
            return
        from src.tui.screens.live_chat import LiveChatScreen
        self.app.push_screen(LiveChatScreen(config))

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # ------------------------------------------------------------------
    # Pane builders
    # ------------------------------------------------------------------

    def _build_topic_pane(self) -> None:
        pane = self.query_one("#topic-pane")
        pane.remove_children()
        pane.mount(Label("Session title:", classes="field-label"))
        pane.mount(Input(placeholder="My Session", id="input-title"))
        pane.mount(Label("Topic:", classes="field-label"))
        pane.mount(TextArea(id="input-topic"))

    def _show_pane(self, name: str) -> None:
        panes = ["topic", "setting", "agents", "orchestrator", "hitl"]
        for p in panes:
            pane = self.query_one(f"#{p}-pane")
            pane.display = p == name

    def _populate_from_config(self) -> None:
        if self._config is None:
            return
        try:
            self.query_one("#input-title", Input).value = self._config.title
            self.query_one("#input-topic", TextArea).load_text(self._config.topic or "")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Config builder
    # ------------------------------------------------------------------

    def _build_config(self) -> SessionConfig | None:
        """Gather widget values, validate, show errors on failure."""
        try:
            title = self.query_one("#input-title", Input).value.strip() or "Untitled"
            topic = self.query_one("#input-topic", TextArea).text.strip()
            # Build a minimal config using the loaded config as base if available
            if self._config:
                data = self._config.model_dump()
                data["title"] = title
                data["topic"] = topic
            else:
                data = {
                    "title": title,
                    "topic": topic,
                    "setting": "social",
                    "agents": [
                        {
                            "id": "agent_1",
                            "name": "Agent 1",
                            "provider": "anthropic",
                            "model": "claude-sonnet-4-6",
                            "role": "participant",
                        }
                    ],
                    "orchestrator": {"type": "python", "module": "basic"},
                }
            config = SessionConfig.model_validate(data)
            self.query_one("#validation-error", Label).update("")
            return config
        except Exception as exc:
            self.query_one("#validation-error", Label).update(
                f"[red]Validation error: {exc}[/red]"
            )
            return None

    async def _save_async(self, config: SessionConfig) -> None:
        templates_dir = Path(settings.session_templates_path)
        templates_dir.mkdir(parents=True, exist_ok=True)
        slug = config.title.lower().replace(" ", "-")[:40]
        path = templates_dir / f"{slug}.yaml"
        data = config.model_dump(exclude_none=True)
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
        self.notify(f"Saved: {path.name}", title="Template saved")
        self.app.pop_screen()
