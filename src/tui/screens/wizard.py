"""SetupWizardScreen — 5-tab session config editor."""

from __future__ import annotations

import yaml
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    Switch,
    Tab,
    Tabs,
    TextArea,
)

from src.session.config import (
    AgentConfig,
    ChannelConfig,
    GameConfig,
    HITLConfig,
    LLMDefaults,
    OrchestratorConfig,
    SessionConfig,
    TranscriptConfig,
)
from src.settings import settings


_SESSION_TYPES = [
    ("Games", "games"),
    ("Social", "social"),
    ("Research", "research"),
    ("Task Completion", "task-completion"),
    ("Problem Solve", "problem-solve"),
]

_TURN_ORDERS = [
    ("Round Robin", "round-robin"),
    ("Moderator Driven", "moderator-driven"),
    ("Orchestrator", "orchestrator"),
]

_PROVIDERS = [
    ("Anthropic", "anthropic"),
    ("OpenAI", "openai"),
    ("Google", "google"),
    ("Gemini", "gemini"),
    ("Mistral", "mistral"),
]

_ORCHESTRATOR_MODULES = [
    ("Basic", "basic"),
    ("Mafia", "mafia"),
    ("Telephone", "telephone"),
    ("Turn Based", "turn_based"),
    ("Poker", "poker"),
    ("Market Research", "market_research"),
]

_HITL_ROLES = [
    ("The Caller — starts the whisper chain", "The Caller"),
    ("Moderator — guides discussion", "Moderator"),
    ("Participant — plays as a regular agent", "Participant"),
    ("Observer — watches, comments when needed", "Observer"),
]

_TRANSCRIPT_FORMATS = [
    ("Markdown", "markdown"),
    ("JSON", "json"),
    ("Both", "both"),
]


class AgentEditModal(ModalScreen[dict | None]):
    """Modal for editing a single agent's fields."""

    DEFAULT_CSS = """
    AgentEditModal {
        align: center middle;
    }
    #agent-modal-box {
        width: 70;
        height: auto;
        max-height: 36;
        border: thick $panel;
        background: $surface;
        padding: 1 2;
    }
    #agent-modal-box .field-label {
        margin-top: 1;
        text-style: bold;
    }
    #agent-modal-buttons {
        margin-top: 1;
        height: 3;
    }
    """

    def __init__(self, agent: dict) -> None:
        super().__init__()
        self._agent = agent

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-modal-box"):
            yield Label("Edit Agent", classes="field-label")
            yield Label("Name:", classes="field-label")
            yield Input(value=self._agent.get("name", ""), id="modal-name")
            yield Label("Provider:", classes="field-label")
            yield Select(
                _PROVIDERS,
                value=self._agent.get("provider", "anthropic"),
                id="modal-provider",
            )
            yield Label("Model:", classes="field-label")
            yield Input(value=self._agent.get("model", "claude-sonnet-4-6"), id="modal-model")
            yield Label("Role:", classes="field-label")
            yield Input(value=self._agent.get("role", "participant"), id="modal-role")
            yield Label("Team:", classes="field-label")
            yield Input(value=self._agent.get("team", "") or "", id="modal-team")
            yield Label("Persona:", classes="field-label")
            yield TextArea(id="modal-persona")
            with Horizontal(id="agent-modal-buttons"):
                yield Button("Save", variant="primary", id="modal-save")
                yield Button("Cancel", id="modal-cancel")

    def on_mount(self) -> None:
        self.query_one("#modal-persona", TextArea).load_text(
            self._agent.get("persona", "")
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "modal-save":
            name = self.query_one("#modal-name", Input).value.strip() or "Agent"
            agent_id = name.lower().replace(" ", "_")
            result = {
                "id": agent_id,
                "name": name,
                "provider": self.query_one("#modal-provider", Select).value,
                "model": self.query_one("#modal-model", Input).value.strip(),
                "role": self.query_one("#modal-role", Input).value.strip() or "participant",
                "team": self.query_one("#modal-team", Input).value.strip() or None,
                "persona": self.query_one("#modal-persona", TextArea).text.strip(),
            }
            self.dismiss(result)
        else:
            self.dismiss(None)


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
        self._wizard_agents: list[dict] = []
        if config:
            self._wizard_agents = [
                a.model_dump() for a in config.agents
            ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Tabs(
            Tab("Topic (Metadata)", id="tab_topic"),
            Tab("Setting", id="tab_setting"),
            Tab("Agents", id="tab_agents"),
            Tab("Orchestrator", id="tab_orchestrator"),
            Tab("HITL", id="tab_hitl"),
            id="wizard-tabs",
        )

        yield Static(id="topic-pane")
        yield Static(id="setting-pane")
        yield Static(id="agents-pane")
        yield Static(id="orchestrator-pane")
        yield Static(id="hitl-pane")

        yield Label("", id="validation-error")
        yield Footer()

    def on_mount(self) -> None:
        self._build_topic_pane()
        self._build_setting_pane()
        self._build_agents_pane()
        self._build_orchestrator_pane()
        self._build_hitl_pane()
        self._show_pane("topic")
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
        pane.mount(
            Label(
                "What this tab is for: name the session and optionally tweak the prompt topic shown to participants. "
                "If you opened the wizard from a template, these fields are already seeded and you usually only change them if you want a variant run.",
                classes="field-help",
            )
        )
        pane.mount(Label("Session title:", classes="field-label"))
        pane.mount(Input(placeholder="My Session", id="input-title"))
        pane.mount(Label("Topic (Metadata):", classes="field-label"))
        pane.mount(TextArea(id="input-topic"))

    def _build_setting_pane(self) -> None:
        pane = self.query_one("#setting-pane")
        pane.remove_children()
        pane.mount(
            Label(
                "What this tab is for: session-level runtime settings. For a game template, most users only review this tab. "
                "Use Session Type/Setting for classification, Max Turns to cap the run, and the Game Configuration section only if you are intentionally changing the rules copy or metadata.",
                classes="field-help",
            )
        )

        pane.mount(Label("Session Type:", classes="field-label"))
        pane.mount(Select(_SESSION_TYPES, value="social", id="input-type"))
        pane.mount(Label("Setting Name:", classes="field-label"))
        pane.mount(Input(placeholder="social", id="input-setting"))
        pane.mount(Label("Description:", classes="field-label"))
        pane.mount(TextArea(id="input-description"))
        pane.mount(Label("Max Turns:", classes="field-label"))
        pane.mount(Input(placeholder="e.g. 20", id="input-max-turns"))
        pane.mount(Label("Completion Signal:", classes="field-label"))
        pane.mount(Input(placeholder="e.g. WINS!", id="input-completion-signal"))

        # ── Game Configuration (conditional) ──
        pane.mount(Static(id="game-config-section"))
        game_section = self.query_one("#game-config-section")
        game_section.mount(Label("━━ Game Configuration ━━", classes="section-header"))
        game_section.mount(Label("Game Name:", classes="field-label"))
        game_section.mount(Input(placeholder="e.g. Mafia", id="input-game-name"))
        game_section.mount(Label("Rules (one per line):", classes="field-label"))
        game_section.mount(TextArea(id="input-game-rules"))
        game_section.mount(Label("How to Play:", classes="field-label"))
        game_section.mount(TextArea(id="input-game-howto"))
        game_section.mount(Label("Turn Order:", classes="field-label"))
        game_section.mount(Select(_TURN_ORDERS, value="round-robin", id="input-game-turn-order"))
        game_section.mount(Label("Win Condition:", classes="field-label"))
        game_section.mount(Input(placeholder="e.g. First to connect four", id="input-game-win"))
        game_section.mount(Label("Max Rounds:", classes="field-label"))
        game_section.mount(Input(placeholder="e.g. 12", id="input-game-max-rounds"))
        game_section.mount(Label("HITL Compatible:", classes="field-label"))
        game_section.mount(Switch(value=True, id="input-game-hitl"))
        game_section.display = False

        # ── Advanced Settings ──
        pane.mount(Static(id="advanced-section"))
        adv = self.query_one("#advanced-section")
        adv.mount(Label("━━ Advanced Settings ━━", classes="section-header"))
        adv.mount(Label("Temperature:", classes="field-label"))
        adv.mount(Input(value="0.7", id="input-temperature"))
        adv.mount(Label("Max Response Tokens:", classes="field-label"))
        adv.mount(Input(placeholder="Provider default", id="input-max-tokens"))
        adv.mount(Label("Thinking Budget:", classes="field-label"))
        adv.mount(Input(value="8000", id="input-thinking-budget"))
        adv.mount(Label("LLM Timeout (sec):", classes="field-label"))
        adv.mount(Input(value="30", id="input-timeout"))

        # ── Transcript ──
        pane.mount(Static(id="transcript-section"))
        tx = self.query_one("#transcript-section")
        tx.mount(Label("━━ Transcript ━━", classes="section-header"))
        tx.mount(Label("Auto-save:", classes="field-label"))
        tx.mount(Switch(value=True, id="input-transcript-auto-save"))
        tx.mount(Label("Format:", classes="field-label"))
        tx.mount(Select(_TRANSCRIPT_FORMATS, value="both", id="input-transcript-format"))
        tx.mount(Label("Save Path:", classes="field-label"))
        tx.mount(Input(value="./sessions/", id="input-transcript-path"))

    def _build_agents_pane(self) -> None:
        pane = self.query_one("#agents-pane")
        pane.remove_children()
        pane.mount(
            Label(
                "What this tab is for: choose who is playing. Add/edit/remove agents only when you want a different roster, model, persona, or team layout. "
                "If you started from a template, leaving the roster alone is valid.",
                classes="field-help",
            )
        )
        pane.mount(Label("Agent Roster:", classes="field-label"))
        table = DataTable(id="agents-table")
        table.add_columns("Name", "Provider", "Model", "Role", "Team")
        pane.mount(table)
        pane.mount(Horizontal(
            Button("Add Agent", variant="primary", id="btn-add-agent"),
            Button("Edit Agent", id="btn-edit-agent"),
            Button("Remove Agent", variant="error", id="btn-remove-agent"),
            id="agent-buttons",
        ))
        self._refresh_agents_table()

    def _build_orchestrator_pane(self) -> None:
        pane = self.query_one("#orchestrator-pane")
        pane.remove_children()
        pane.mount(
            Label(
                "What this tab is for: select how turns are scheduled. For most built-in game templates, keep the existing Python orchestrator. "
                "Only switch to an LLM orchestrator if you are explicitly experimenting with LLM-based turn control.",
                classes="field-help",
            )
        )

        pane.mount(Label("Orchestrator Type:", classes="field-label"))
        pane.mount(Select(
            [("Python", "python"), ("LLM", "llm")],
            value="python",
            id="input-orch-type",
        ))

        # Python section
        pane.mount(Static(id="orch-python-section"))
        py_sec = self.query_one("#orch-python-section")
        py_sec.mount(Label("Module:", classes="field-label"))
        py_sec.mount(Select(_ORCHESTRATOR_MODULES, value="basic", id="input-orch-module"))

        # LLM section
        pane.mount(Static(id="orch-llm-section"))
        llm_sec = self.query_one("#orch-llm-section")
        llm_sec.mount(Label("Provider:", classes="field-label"))
        llm_sec.mount(Select(_PROVIDERS, value="anthropic", id="input-orch-provider"))
        llm_sec.mount(Label("Model:", classes="field-label"))
        llm_sec.mount(Input(placeholder="claude-sonnet-4-6", id="input-orch-model"))
        llm_sec.mount(Label("Persona:", classes="field-label"))
        llm_sec.mount(TextArea(id="input-orch-persona"))
        llm_sec.display = False

    def _build_hitl_pane(self) -> None:
        pane = self.query_one("#hitl-pane")
        pane.remove_children()
        pane.mount(
            Label(
                "What this tab is for: optional human-in-the-loop participation. Leave HITL disabled unless you want a human to inject messages during the live session.",
                classes="field-help",
            )
        )

        pane.mount(Label("Enable HITL:", classes="field-label"))
        pane.mount(Switch(value=False, id="input-hitl-enabled"))

        pane.mount(Static(id="hitl-settings-section"))
        settings_sec = self.query_one("#hitl-settings-section")
        settings_sec.mount(Label("Role:", classes="field-label"))
        settings_sec.mount(Select(
            _HITL_ROLES,
            value="The Caller",
            id="input-hitl-role",
            allow_blank=False,
        ))
        settings_sec.display = False

    # ------------------------------------------------------------------
    # Pane visibility
    # ------------------------------------------------------------------

    def _show_pane(self, name: str) -> None:
        panes = ["topic", "setting", "agents", "orchestrator", "hitl"]
        for p in panes:
            pane = self.query_one(f"#{p}-pane")
            pane.display = p == name

    # ------------------------------------------------------------------
    # Reactive UI handlers
    # ------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        select_id = event.select.id
        if select_id == "input-type":
            is_game = event.value == "games"
            try:
                self.query_one("#game-config-section").display = is_game
            except Exception:
                pass
        elif select_id == "input-orch-type":
            is_python = event.value == "python"
            try:
                self.query_one("#orch-python-section").display = is_python
                self.query_one("#orch-llm-section").display = not is_python
            except Exception:
                pass

    def on_switch_changed(self, event: Switch.Changed) -> None:
        switch_id = event.switch.id
        if switch_id == "input-hitl-enabled":
            try:
                self.query_one("#hitl-settings-section").display = event.value
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Agent table management
    # ------------------------------------------------------------------

    def _refresh_agents_table(self) -> None:
        try:
            table = self.query_one("#agents-table", DataTable)
        except Exception:
            return
        table.clear()
        for agent in self._wizard_agents:
            table.add_row(
                agent.get("name", ""),
                agent.get("provider", ""),
                agent.get("model", ""),
                agent.get("role", ""),
                agent.get("team", "") or "—",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "btn-add-agent":
            new_agent = {
                "id": f"agent_{len(self._wizard_agents) + 1}",
                "name": f"Agent {len(self._wizard_agents) + 1}",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "role": "participant",
                "team": None,
                "persona": "",
            }
            self._wizard_agents.append(new_agent)
            self._refresh_agents_table()
        elif btn == "btn-remove-agent":
            table = self.query_one("#agents-table", DataTable)
            if table.cursor_row is not None and 0 <= table.cursor_row < len(self._wizard_agents):
                self._wizard_agents.pop(table.cursor_row)
                self._refresh_agents_table()
        elif btn == "btn-edit-agent":
            table = self.query_one("#agents-table", DataTable)
            if table.cursor_row is not None and 0 <= table.cursor_row < len(self._wizard_agents):
                agent = self._wizard_agents[table.cursor_row]
                row_idx = table.cursor_row

                def on_modal_result(result: dict | None) -> None:
                    if result is not None:
                        self._wizard_agents[row_idx] = result
                        self._refresh_agents_table()

                self.app.push_screen(AgentEditModal(agent), on_modal_result)

    # ------------------------------------------------------------------
    # Populate from existing config
    # ------------------------------------------------------------------

    def _populate_from_config(self) -> None:
        if self._config is None:
            return
        cfg = self._config
        try:
            self.query_one("#input-title", Input).value = cfg.title
            self.query_one("#input-topic", TextArea).load_text(cfg.topic or "")
        except Exception:
            pass

        # Setting tab
        try:
            self.query_one("#input-type", Select).value = cfg.type
            self.query_one("#input-setting", Input).value = cfg.setting or ""
            self.query_one("#input-description", TextArea).load_text(cfg.description or "")
            self.query_one("#input-max-turns", Input).value = str(cfg.max_turns) if cfg.max_turns else ""
            self.query_one("#input-completion-signal", Input).value = cfg.completion_signal or ""
        except Exception:
            pass

        # Game config
        if cfg.game:
            try:
                self.query_one("#game-config-section").display = True
                self.query_one("#input-game-name", Input).value = cfg.game.name
                self.query_one("#input-game-rules", TextArea).load_text(
                    "\n".join(cfg.game.rules)
                )
                self.query_one("#input-game-howto", TextArea).load_text(cfg.game.how_to_play or "")
                self.query_one("#input-game-turn-order", Select).value = cfg.game.turn_order
                self.query_one("#input-game-win", Input).value = cfg.game.win_condition or ""
                self.query_one("#input-game-max-rounds", Input).value = (
                    str(cfg.game.max_rounds) if cfg.game.max_rounds else ""
                )
                self.query_one("#input-game-hitl", Switch).value = cfg.game.hitl_compatible
            except Exception:
                pass

        # Advanced / LLM defaults
        try:
            llm = cfg.llm_defaults
            self.query_one("#input-temperature", Input).value = str(llm.temperature)
            self.query_one("#input-max-tokens", Input).value = str(llm.max_tokens) if llm.max_tokens else ""
            self.query_one("#input-thinking-budget", Input).value = str(llm.thinking_budget)
            self.query_one("#input-timeout", Input).value = str(llm.timeout)
        except Exception:
            pass

        # Transcript
        try:
            tx = cfg.transcript
            self.query_one("#input-transcript-auto-save", Switch).value = tx.auto_save
            self.query_one("#input-transcript-format", Select).value = tx.format
            self.query_one("#input-transcript-path", Input).value = str(tx.path)
        except Exception:
            pass

        # Orchestrator
        try:
            orch = cfg.orchestrator
            self.query_one("#input-orch-type", Select).value = orch.type
            if orch.type == "python":
                self.query_one("#orch-python-section").display = True
                self.query_one("#orch-llm-section").display = False
                self.query_one("#input-orch-module", Select).value = orch.module
            else:
                self.query_one("#orch-python-section").display = False
                self.query_one("#orch-llm-section").display = True
                if orch.provider:
                    self.query_one("#input-orch-provider", Select).value = orch.provider
                if orch.model:
                    self.query_one("#input-orch-model", Input).value = orch.model
                self.query_one("#input-orch-persona", TextArea).load_text(orch.persona or "")
        except Exception:
            pass

        # HITL
        try:
            self.query_one("#input-hitl-enabled", Switch).value = cfg.hitl.enabled
            if cfg.hitl.enabled:
                self.query_one("#hitl-settings-section").display = True
                role = cfg.hitl.role or "The Caller"
                self.query_one("#input-hitl-role", Select).value = role
        except Exception:
            pass

        # Agents table
        self._refresh_agents_table()

    # ------------------------------------------------------------------
    # Config builder
    # ------------------------------------------------------------------

    def _build_config(self) -> SessionConfig | None:
        """Gather widget values from all tabs, validate, show errors on failure."""
        try:
            # Topic tab
            title = self.query_one("#input-title", Input).value.strip() or "Untitled"
            topic = self.query_one("#input-topic", TextArea).text.strip()

            # Setting tab
            session_type = self.query_one("#input-type", Select).value
            setting = self.query_one("#input-setting", Input).value.strip() or str(session_type)
            description = self.query_one("#input-description", TextArea).text.strip()
            max_turns_str = self.query_one("#input-max-turns", Input).value.strip()
            max_turns = int(max_turns_str) if max_turns_str else None
            completion_signal = self.query_one("#input-completion-signal", Input).value.strip() or None

            # Game config (only for games type)
            game_data = None
            if session_type == "games":
                game_name = self.query_one("#input-game-name", Input).value.strip()
                rules_text = self.query_one("#input-game-rules", TextArea).text.strip()
                rules = [r.strip() for r in rules_text.splitlines() if r.strip()]
                how_to_play = self.query_one("#input-game-howto", TextArea).text.strip()
                turn_order = self.query_one("#input-game-turn-order", Select).value
                win_condition = self.query_one("#input-game-win", Input).value.strip()
                max_rounds_str = self.query_one("#input-game-max-rounds", Input).value.strip()
                max_rounds = int(max_rounds_str) if max_rounds_str else None
                hitl_compatible = self.query_one("#input-game-hitl", Switch).value
                game_data = {
                    "name": game_name or title,
                    "rules": rules,
                    "how_to_play": how_to_play,
                    "turn_order": turn_order,
                    "win_condition": win_condition,
                    "hitl_compatible": hitl_compatible,
                    "max_rounds": max_rounds,
                }

            # LLM defaults
            temp_str = self.query_one("#input-temperature", Input).value.strip()
            temperature = float(temp_str) if temp_str else 0.7
            max_tokens_str = self.query_one("#input-max-tokens", Input).value.strip()
            llm_max_tokens = int(max_tokens_str) if max_tokens_str else None
            thinking_str = self.query_one("#input-thinking-budget", Input).value.strip()
            thinking_budget = int(thinking_str) if thinking_str else 8000
            timeout_str = self.query_one("#input-timeout", Input).value.strip()
            timeout = int(timeout_str) if timeout_str else 30

            # Transcript
            auto_save = self.query_one("#input-transcript-auto-save", Switch).value
            tx_format = self.query_one("#input-transcript-format", Select).value
            tx_path = self.query_one("#input-transcript-path", Input).value.strip() or "./sessions/"

            # Orchestrator
            orch_type = self.query_one("#input-orch-type", Select).value
            orch_data: dict = {"type": orch_type}
            if orch_type == "python":
                orch_data["module"] = self.query_one("#input-orch-module", Select).value
            else:
                orch_data["provider"] = self.query_one("#input-orch-provider", Select).value
                orch_data["model"] = self.query_one("#input-orch-model", Input).value.strip()
                orch_data["persona"] = self.query_one("#input-orch-persona", TextArea).text.strip()

            # HITL
            hitl_enabled = self.query_one("#input-hitl-enabled", Switch).value
            hitl_role = None
            if hitl_enabled:
                hitl_role = self.query_one("#input-hitl-role", Select).value or None

            # Agents — use wizard agent list
            agents = self._wizard_agents
            if not agents:
                agents = [{
                    "id": "agent_1",
                    "name": "Agent 1",
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-6",
                    "role": "participant",
                }]

            # Build channels from agent teams
            channels = self._build_channels_from_agents(agents)

            data = {
                "title": title,
                "description": description,
                "type": session_type,
                "setting": setting,
                "topic": topic,
                "orchestrator": orch_data,
                "agents": agents,
                "channels": channels,
                "hitl": {"enabled": hitl_enabled, "role": hitl_role},
                "transcript": {
                    "auto_save": auto_save,
                    "format": tx_format,
                    "path": tx_path,
                },
                "llm_defaults": {
                    "temperature": temperature,
                    "max_tokens": llm_max_tokens,
                    "thinking_budget": thinking_budget,
                    "timeout": timeout,
                },
                "max_turns": max_turns,
                "completion_signal": completion_signal,
                "game": game_data,
            }

            config = SessionConfig.model_validate(data)
            self.query_one("#validation-error", Label).update("")
            return config
        except Exception as exc:
            self.query_one("#validation-error", Label).update(
                f"[red]Validation error: {exc}[/red]"
            )
            return None

    def _build_channels_from_agents(self, agents: list[dict]) -> list[dict]:
        """Auto-generate team channels from agent team assignments."""
        teams: dict[str, list[str]] = {}
        for agent in agents:
            team = agent.get("team")
            if team:
                teams.setdefault(team, []).append(agent.get("id", ""))
        channels = [{"id": "public", "type": "public", "members": []}]
        for team_id, members in teams.items():
            channels.append({
                "id": team_id,
                "type": "team",
                "members": members,
            })
        return channels

    async def _save_async(self, config: SessionConfig) -> None:
        templates_dir = Path(settings.session_templates_path)
        templates_dir.mkdir(parents=True, exist_ok=True)
        slug = config.title.lower().replace(" ", "-")[:40]
        path = templates_dir / f"{slug}.yaml"
        data = config.model_dump(mode="json", exclude_none=True)
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
        self.notify(f"Saved: {path.name}", title="Template saved")
        self.app.pop_screen()
