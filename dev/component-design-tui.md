# one-0-one — TUI Component Design

**Status:** Approved for implementation
**References:** `dev/preliminary-solution-design.md`, `agents/tui-architect.md`, `src/session/`

---

## 1. Overview

The TUI is a Textual Python application that embeds the session engine in-process. It has
three primary screens and runs the `SessionEngine` in a background `@work` task, receiving
live events from the `EventBus` and driving widget updates on the main thread.

```
┌─────────────────────────────────────────────────────────────────────┐
│  OneOhOneApp (Textual App)                                           │
│                                                                      │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────────┐  │
│  │ SessionBrowser  │  │ SetupWizard      │  │ LiveChat          │  │
│  │ Screen          │→ │ Screen           │→ │ Screen            │  │
│  │                 │  │                  │  │                   │  │
│  │ ListView        │  │ Tabs (5)         │  │ ChannelTabs       │  │
│  │ type-filter     │  │ + validation     │  │ ChatLog           │  │
│  │ Tabs            │  │ + YAML writer    │  │ MonologuePanel    │  │
│  └─────────────────┘  └──────────────────┘  │ AgentRoster       │  │
│                                              │ TurnIndicator     │  │
│                                              │ HITLInputBar      │  │
│                                              └───────────────────┘  │
│                                                        ↑             │
│                         SessionEngine ────── EventBus ┘             │
│                         (in @work task)                              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. File Structure

```
src/tui/
├── app.py                    # OneOhOneApp — entry point, screen registration
├── screens/
│   ├── __init__.py
│   ├── browser.py            # SessionBrowserScreen
│   ├── wizard.py             # SetupWizardScreen
│   └── live_chat.py          # LiveChatScreen
├── widgets/
│   ├── __init__.py
│   ├── agent_roster.py       # AgentRoster (DataTable wrapper)
│   ├── channel_tabs.py       # ChannelTabs (Tabs + per-tab RichLog)
│   ├── monologue_panel.py    # MonologuePanel (collapsible bottom strip)
│   ├── turn_indicator.py     # TurnIndicator (Label + LoadingIndicator)
│   └── hitl_input.py        # HITLInputBar (Input + channel selector)
└── styles/
    ├── app.tcss              # Global layout, color palette, focus states
    ├── browser.tcss          # SessionBrowserScreen styles
    ├── wizard.tcss           # SetupWizardScreen styles
    └── live_chat.tcss        # LiveChatScreen styles
```

---

## 3. Engine Integration Pattern

The TUI never calls the engine directly from the main thread. All engine
interaction happens through an `@work` task. The `EventBus` bridges the two.

### 3.1 Starting a session

```python
# In LiveChatScreen
@work(exclusive=True, thread=False)
async def run_session(self, config: SessionConfig) -> None:
    bus = EventBus()

    # Attach subscribers BEFORE starting the engine
    bus.stream() \
       .filter(lambda e: e.type == "MESSAGE") \
       .subscribe(self._on_message)

    bus.stream() \
       .filter(lambda e: e.type in ("MONOLOGUE", "TURN")) \
       .subscribe(self.query_one(MonologuePanel).handle_event)

    bus.stream() \
       .filter(lambda e: e.type == "TURN") \
       .subscribe(self.query_one(TurnIndicator).handle_turn)

    bus.stream() \
       .filter(lambda e: e.type in ("GAME_STATE", "RULE_VIOLATION")) \
       .subscribe(self._on_system_event)

    bus.stream() \
       .filter(lambda e: e.type == "SESSION_END") \
       .subscribe(self._on_session_end)

    engine = SessionEngine(config, bus)
    self._engine = engine   # keep reference for pause/inject/end
    await engine.run()
```

### 3.2 EventBus → widget update thread safety

Textual widgets must be updated from the main thread. Subscriptions run on the
asyncio event loop (same thread as Textual's main loop), so direct widget calls
are safe. **Never** spawn a new `Thread` for event handling.

```python
def _on_message(self, event: MessageEvent) -> None:
    # Called on the asyncio loop — direct widget access is safe
    tabs = self.query_one(ChannelTabs)
    tabs.append_message(event)
```

### 3.3 HITL turn handling

When the engine emits a `TurnEvent` containing the HITL agent ID (a reserved
sentinel, e.g., `"hitl"`), `LiveChatScreen` shows `HITLInputBar` and pauses
waiting for the human's message. The human's submission is injected directly
into the engine via `engine.inject_hitl_message(text, channel_id)`.

---

## 4. Screen Designs

### 4.1 SessionBrowserScreen

**Purpose:** Home screen. Lists session templates. Fast path to launch.

**Layout:**
```
┌──────────────────────────────────────────────────────┐
│  one-0-one                                  [N] [L]  │  ← Header
├──────────────────────────────────────────────────────┤
│ [All] [Games] [Social] [Research] [Task] [Problem]   │  ← Type filter Tabs
├──────────────────────────────────────────────────────┤
│  20 Questions                              [games]   │
│  Classic 20 Questions — one agent secretly...        │
├──────────────────────────────────────────────────────┤
│  Claude vs GPT Debate                      [social]  │  ← ListView (selected)
│  Free-form discussion on AI and employment           │
├──────────────────────────────────────────────────────┤
│  Climate Policy Research                [research]   │
│  Multi-model research panel on climate policy        │
└──────────────────────────────────────────────────────┘
│ ↑↓ Navigate  Enter Launch  N New  L Load  Q Quit    │  ← Footer
```

**Widgets:**
- `Tabs` — type filter: All / Games / Social / Research / Task / Problem-solve
- `ListView` — one `ListItem` per template (title + description + type badge)
- Key bindings: `↑↓` navigate, `Enter` launch, `N` new session, `L` load external, `Q` quit, `Esc` no-op (already home)

**Engine interaction:**
- On mount: `@work` scan of `settings.session_templates_path` → populate `ListView`
- On `Enter`: `push_screen(SetupWizardScreen(config))` OR direct `push_screen(LiveChatScreen(config))` if no customization needed
- On `L`: `push_screen(FilePickerModal())` → on confirm, `push_screen(SetupWizardScreen(loaded_config))`

**Template loading:**
```python
@work
async def load_templates(self) -> None:
    templates_dir = settings.session_templates_path
    configs = []
    for path in sorted(templates_dir.glob("*.yaml")):
        try:
            configs.append(load_session_config(path))
        except Exception:
            pass   # skip invalid templates silently
    self.call_from_thread(self._populate_list, configs)
```

---

### 4.2 SetupWizardScreen

**Purpose:** Create or edit a session config. Output: a `SessionConfig` (in memory
or written as YAML).

**Layout (tabbed):**
```
┌──────────────────────────────────────────────────────┐
│  Setup Wizard                                  [Esc] │
├──[Topic]──[Setting]──[Agents]──[Orchestrator]──[HITL]┤
│                                                      │
│  Session title: _________________________________    │
│                                                      │
│  Topic:                                              │
│  ┌────────────────────────────────────────────────┐ │
│  │ Should AI systems be granted legal personhood? │ │
│  └────────────────────────────────────────────────┘ │
│                                                      │
└──────────────────────────────────────────────────────┘
│ Tab Next  S Save  R Run  Esc Back                    │
```

**Tabs and their widgets:**

| Tab | Key widgets | Notes |
|-----|-------------|-------|
| Topic | `Input` (title), `TextArea` (topic) | Required before proceeding |
| Setting | `RadioSet` (social/research/game/task/problem-solve) | Selection auto-populates Agents defaults |
| Agents | `DataTable` (agent list) + `[A]dd` `[D]elete` `[E]dit` | Edit pushes `AgentEditModal` |
| Orchestrator | `RadioSet` (python/llm), `Select` (module or model), `TextArea` (persona) | python=basic default |
| HITL | `Switch` (enabled), `Input` (role name) | Role input only visible when Switch on |

**Validation:**
- `S` / `R` trigger `SessionConfig.model_validate()` — validation errors displayed inline as `Label` below the offending field
- Required fields highlighted in red border on error

**Engine interaction:**
- `S` (save): `@work` write YAML to `settings.session_templates_path / slug.yaml` → snackbar + return to browser
- `R` (run): build `SessionConfig` in memory → `push_screen(LiveChatScreen(config))`

---

### 4.3 LiveChatScreen

**Purpose:** The active session view. Displays live agent messages across
channel tabs, shows monologue in a separate panel, and exposes HITL controls.

**Layout:**
```
┌──────────────────────────────────────────────────┬────────────────────┐
│ [Public] [Team: Red] [Team: Blue] [Private]      │ Turn 7 / 20        │
│                                                   │ ● Nova (thinking)  │
│  Nova: I believe the strongest argument is...    │                    │
│                                                   │ Agents             │
│  Rex: That premise conflates legal personhood... │ ─────────────────  │
│                                                   │ Nova  Anthropic    │
│  🔒 Nova [→ Rex]  (dimmed)                       │ Rex   OpenAI       │
│  Don't counter me on the ethics point yet.       │ Sage  Google       │
│                                                   │ Judge Mistral      │
│  [system] Rule violation — Turn 4: answer must...│                    │
│                                                   │                    │
├──────────────────────────────────────────────────┤                    │
│ ▌ Nova — thinking...                [M to close] │                    │
│ I should pivot to the economic angle here. The   │                    │
│ judge seems to weight practical arguments more...│                    │
├──────────────────────────────────────────────────┴────────────────────┤
│ P Pause  M Monologue  I Inject  E End  Q Quit                         │
```

**Primary regions:**

| Region | Widget | Notes |
|--------|--------|-------|
| Channel tabs | `ChannelTabs` (custom) | `Tabs` + per-tab `RichLog` |
| Agent sidebar | `AgentRoster` (custom) | `DataTable`, read-only |
| Turn indicator | `TurnIndicator` (custom) | `Label` + `LoadingIndicator` |
| Monologue panel | `MonologuePanel` (custom) | Collapsible bottom strip |
| HITL input | `HITLInputBar` (custom) | Hidden unless HITL turn |

---

## 5. Custom Widget Designs

### 5.1 ChannelTabs

**Responsibility:** One tab per channel (Public / Team: X / Private). Each tab
has its own `RichLog`. Switching tabs does not pause the session. Observer sees
all tabs; HITL player sees only their permitted channels.

```python
class ChannelTabs(Widget):
    """Tabbed channel view. One RichLog per channel."""

    def compose(self) -> ComposeResult:
        with Tabs():
            for ch in self._visible_channels():
                yield Tab(self._tab_label(ch), id=f"tab_{ch.id}")
        for ch in self._visible_channels():
            yield RichLog(id=f"log_{ch.id}", highlight=True, markup=True)

    def append_message(self, event: MessageEvent) -> None:
        """Route a MessageEvent to the correct channel log."""
        log = self.query_one(f"#log_{event.channel_id}", RichLog)
        text = self._format_message(event)
        log.write(text)
        self._increment_unread(event.channel_id)

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """Show the correct log, hide others; clear unread badge."""
        ch_id = event.tab.id.removeprefix("tab_")
        for log in self.query(RichLog):
            log.display = log.id == f"log_{ch_id}"
        self._clear_unread(ch_id)
```

**Tab labels:** `Public`, `Team: Red`, `Team: Blue`, `Private`
**Unread badge:** appended to tab label as `[3]` when messages arrive on inactive tab
**Private messages:** rendered in the `Private` tab AND inline on `Public` tab (observer only) with dimmed markup + lock glyph

### 5.2 MonologuePanel

**Responsibility:** Bottom strip showing the active agent's chain-of-thought. Hidden
by default. Cleared on every `TurnEvent`. Never receives `MessageEvent`.

```python
class MonologuePanel(Widget):
    """Collapsible bottom strip for agent monologue."""

    DEFAULT_CSS = """
    MonologuePanel {
        height: 0;            /* hidden by default */
        border-top: solid $monologue-border;
        background: $monologue-bg;
    }
    MonologuePanel.open {
        height: 8;            /* 8 terminal rows when open */
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("", id="mono-header")
        yield RichLog(id="mono-log", highlight=False, markup=True)

    def handle_event(self, event) -> None:
        """Called by EventBus subscription."""
        if event.type == "TURN":
            self._clear(event.agent_ids[0] if event.agent_ids else "")
        elif event.type == "MONOLOGUE":
            self._append(event.agent_name, event.text)

    def _clear(self, next_agent_name: str) -> None:
        self.query_one("#mono-log", RichLog).clear()
        self.query_one("#mono-header", Label).update(
            f"▌ {next_agent_name} — thinking..."
        )

    def _append(self, agent_name: str, text: str) -> None:
        self.query_one("#mono-log", RichLog).write(
            f"[dim]{text}[/dim]"
        )

    def toggle(self) -> None:
        self.toggle_class("open")
```

**Key constraint:** `MonologuePanel` only receives `MONOLOGUE` and `TURN` events —
never `MESSAGE` events. This is enforced by the EventBus filter in `LiveChatScreen`.

### 5.3 AgentRoster

**Responsibility:** Read-only sidebar showing agent name, model, role, team, and
live status. Updated on `TurnEvent` (status changes) and `SessionEndEvent`.

```python
class AgentRoster(Widget):
    """DataTable showing agent pool status."""

    COLUMNS = ("Name", "Model", "Role", "Team", "Status")

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="none")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*self.COLUMNS)

    def populate(self, agents: list[AgentConfig]) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for i, agent in enumerate(agents):
            color = AGENT_PALETTE[i % len(AGENT_PALETTE)]
            table.add_row(
                f"[{color}]{agent.name}[/]",
                f"{agent.provider}/{agent.model}",
                agent.role,
                agent.team or "—",
                "idle",
                key=agent.id,
            )

    def set_status(self, agent_id: str, status: str) -> None:
        table = self.query_one(DataTable)
        row_key = table.get_row_key(agent_id)
        table.update_cell(row_key, "Status", status)
```

### 5.4 TurnIndicator

**Responsibility:** Shows who is speaking and the turn number. Animates while
an agent is generating.

```python
class TurnIndicator(Widget):
    """Turn counter + agent status label with spinner."""

    def compose(self) -> ComposeResult:
        yield Label("", id="turn-label")
        yield LoadingIndicator(id="spinner")

    def handle_turn(self, event) -> None:
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
```

### 5.5 HITLInputBar

**Responsibility:** Text input for the human participant. Visible only on the
human's turn. Includes a channel selector when the human is on a team.

```python
class HITLInputBar(Widget):
    """HITL message input — hidden until the human's turn arrives."""

    DEFAULT_CSS = "HITLInputBar { display: none; }"

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Select(
                [("Public", "public"), ("Team", "team")],
                id="channel-select",
                value="public",
            )
            yield Input(placeholder="Your message...", id="hitl-input")
            yield Button("Send", id="hitl-send", variant="primary")

    def show_for_turn(self, hitl_config) -> None:
        self.display = True
        self.query_one("#hitl-input", Input).focus()
        # Hide channel selector if no team
        self.query_one("#channel-select").display = bool(hitl_config.team)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "hitl-send":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        text = self.query_one("#hitl-input", Input).value.strip()
        channel = self.query_one("#channel-select", Select).value
        if text:
            self.post_message(self.HITLMessage(text=text, channel_id=channel))
            self.query_one("#hitl-input", Input).clear()
            self.display = False

    class HITLMessage(Message):
        def __init__(self, text: str, channel_id: str) -> None:
            super().__init__()
            self.text = text
            self.channel_id = channel_id
```

---

## 6. CSS Design System

All colors are semantic tokens — never raw hex. Defined in `app.tcss`.

```css
/* app.tcss — semantic color tokens */

$agent-0: #4fc3f7;   /* light blue */
$agent-1: #81c784;   /* green */
$agent-2: #ffb74d;   /* orange */
$agent-3: #f06292;   /* pink */
$agent-4: #ba68c8;   /* purple */
$agent-5: #4db6ac;   /* teal */

$hitl-color: white;
$orchestrator-color: #9e9e9e;

$monologue-bg: #1a2a2f;
$monologue-border: #546e7a;
$monologue-text: #90a4ae;

$private-bg: #2a1f1a;
$private-border: #8d6e63;

/* Focus visibility — required on every focusable widget */
*:focus {
    border: tall $accent;
}

/* Screen layout */
LiveChatScreen {
    layout: horizontal;
}

#main-column {
    width: 1fr;
    layout: vertical;
}

#sidebar {
    width: 24;
    layout: vertical;
    border-left: solid $panel;
}
```

---

## 7. Agent Color Palette

Six colors cycled by agent index. Consistent across all screens and sessions.

```python
# src/tui/colors.py
AGENT_PALETTE = [
    "#4fc3f7",   # 0 — light blue
    "#81c784",   # 1 — green
    "#ffb74d",   # 2 — orange
    "#f06292",   # 3 — pink
    "#ba68c8",   # 4 — purple
    "#4db6ac",   # 5 — teal
]

def agent_color(index: int) -> str:
    return AGENT_PALETTE[index % len(AGENT_PALETTE)]
```

---

## 8. Key Bindings Map

Defined in `OneOhOneApp.BINDINGS` and per-screen where applicable.

| Key | Context | Action |
|-----|---------|--------|
| `Q` | Any | Quit (with confirmation if session active) |
| `Esc` | Any | Back / cancel |
| `N` | Browser | New session (→ SetupWizardScreen) |
| `L` | Browser | Load external template |
| `Enter` | Browser | Launch selected template |
| `Tab` | Wizard | Next wizard tab |
| `S` | Wizard | Save template to YAML |
| `R` | Wizard | Run session immediately |
| `P` | LiveChat | Pause / resume session |
| `M` | LiveChat | Toggle MonologuePanel |
| `I` | LiveChat | Inject message (out-of-turn) |
| `E` | LiveChat | End session |

---

## 9. Navigation Flow

```
OneOhOneApp
    │
    ├── push_screen → SessionBrowserScreen  (default)
    │       │
    │       ├── Enter/N → push_screen → SetupWizardScreen
    │       │       │
    │       │       ├── R → push_screen → LiveChatScreen
    │       │       └── Esc → pop_screen → SessionBrowserScreen
    │       │
    │       └── Enter (existing template) → push_screen → LiveChatScreen
    │
    └── LiveChatScreen
            │
            ├── E → push_screen → TranscriptModal → pop to Browser
            └── Esc / Q → confirm → pop_screen → SessionBrowserScreen
```

**Modals (overlay screens):**
- `FilePickerModal` — load external YAML template
- `AgentEditModal` — add/edit a single agent in the wizard
- `InjectModal` — out-of-turn HITL message injection
- `TranscriptModal` — "Session complete. View transcript? Save location: ..."
- `ConfirmModal` — generic "End session?" "Quit?" confirmation

---

## 10. EventBus Subscription Map

Summary of which widget subscribes to which event types in `LiveChatScreen`:

| Event type | Subscriber widget | Action |
|------------|------------------|--------|
| `CHANNEL_CREATED` | `ChannelTabs` | Add tab if not present |
| `TURN` | `TurnIndicator`, `MonologuePanel`, `AgentRoster` | Update status, clear monologue |
| `MESSAGE` | `ChannelTabs` | Append to correct channel log |
| `MONOLOGUE` | `MonologuePanel` | Stream to monologue log |
| `GAME_STATE` | `AgentRoster` (turn counter), `ChannelTabs` (public log) | Update turn/score display |
| `RULE_VIOLATION` | `ChannelTabs` (public log) | Append system message |
| `SESSION_END` | `LiveChatScreen` | Show `TranscriptModal` |

---

## 11. Violations to Prevent

| Violation | Prevention |
|-----------|-----------|
| Frozen UI | All `SessionEngine.run()` in `@work` — never on main thread |
| Monologue in chat | `ChannelTabs` only subscribes to `MESSAGE` — `MONOLOGUE` events go only to `MonologuePanel` |
| Stale monologue | `MonologuePanel` clears on every `TURN` event before streaming new agent |
| Channel bleed | `ChannelTabs.append_message()` routes by `event.channel_id` — never shows team/private on wrong tab |
| Observer blindness | Tab visibility determined by `config.hitl` + agent team membership at screen init |
| Silent private | Private messages rendered with `🔒` glyph + `[→ Recipient]` prefix + `$private-bg` |
| Mouse trap | Every action has a letter accelerator; `Enter` submits all forms |
| Hardcoded colors | All colors via CSS tokens and `agent_color(index)` function |
