# one-0-one — Preliminary Solution Design (PSD)

**Status:** Approved — all decisions resolved 2026-02-22
**References:** `dev/vision.md`, `dev/requirements.md`, `agents/tui-architect.md`, `agents/gui-architect.md`

---

## 1. Problem Summary

Users want to orchestrate conversations between multiple LLM agents — from different providers — around a shared topic, in different modes (social, research, games, task-completion, problem-solving). They want to watch those conversations with full visibility into all communication channels including private exchanges and agent reasoning. Sometimes they want to participate. The platform must be extensible (new providers, orchestrators, and games without code changes) and configurable via reusable YAML templates.

The core technical challenge is that this involves **heterogeneous, asynchronous, concurrent actors** (LLM API calls are slow and variable) communicating through a **partitioned channel model** (not every agent sees every message), driven by a **pluggable orchestrator** that must enforce rules and maintain game state, all surfaced through two different UIs that need live event streaming.

---

## 2. Solution Overview

The proposed solution is a **Python library (`src/`) that implements the session engine**, with two consumer UIs that embed it:

- The **TUI** (Textual) runs the session engine in-process and subscribes to its event bus directly.
- The **Web UI** (FastAPI + React) also runs the session engine in-process, wrapping its event bus in Server-Sent Events (SSE) for the browser.

This makes the session engine the stable, tested core. Neither UI owns session logic; they are display and control surfaces.

```
┌──────────────────────────────────────────────────────────────────────┐
│                        one-0-one                                      │
│                                                                        │
│  ┌─────────────────────┐         ┌──────────────────────────────────┐ │
│  │   TUI (Textual)      │         │  Web UI (FastAPI + React)        │ │
│  │  SessionBrowser      │         │  Session Library                 │ │
│  │  SetupWizard         │         │  Setup Wizard                    │ │
│  │  LiveChatScreen      │         │  Live Chat (SSE → browser)       │ │
│  │  MonologuePanel      │         │  Monologue Drawer                │ │
│  └────────┬────────────┘         └──────────┬───────────────────────┘ │
│           │ in-process                       │ in-process              │
│           └──────────────┬──────────────────┘                         │
│                          ▼                                             │
│           ┌──────────────────────────────────┐                        │
│           │       Session Engine (library)    │                        │
│           │  SessionEngine · ChannelRouter    │                        │
│           │  EventBus · OrchestratorLoader    │                        │
│           │  TranscriptWriter · MemoryStub    │                        │
│           └────────────────┬─────────────────┘                        │
│                            │                                           │
│           ┌────────────────▼─────────────────┐                        │
│           │       Provider Layer (LiteLLM)    │                        │
│           │  Anthropic · OpenAI · Google      │                        │
│           │  Mistral · LiteLLM Router         │                        │
│           └──────────────────────────────────┘                        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Project Structure

```
one-0-one/
├── src/
│   ├── session/
│   │   ├── __init__.py
│   │   ├── config.py          # SessionConfig — Pydantic model, YAML loader + validator
│   │   ├── engine.py          # SessionEngine — main async session loop
│   │   ├── state.py           # SessionState, AgentState, GameState
│   │   └── events.py          # Event types + EventBus (asyncio.Queue wrapper)
│   ├── channels/
│   │   └── router.py          # ChannelRouter — builds per-agent context views
│   ├── orchestrators/
│   │   ├── __init__.py        # OrchestratorProtocol + load_orchestrator()
│   │   ├── basic.py           # Built-in round-robin, no rule enforcement
│   │   └── llm.py             # LLM orchestrator wrapper (prompt → OrchestratorOutput)
│   ├── providers/
│   │   ├── __init__.py        # ProviderClient protocol
│   │   └── litellm_client.py  # LiteLLM-backed unified provider client
│   ├── transcript/
│   │   └── writer.py          # TranscriptWriter — markdown + JSON sidecar
│   ├── memory.py              # Stub: save_memory(), load_memory() (no-ops)
│   ├── tui/
│   │   ├── app.py             # Textual App entry point
│   │   ├── screens/
│   │   │   ├── browser.py     # SessionBrowserScreen
│   │   │   ├── wizard.py      # SetupWizardScreen
│   │   │   └── live_chat.py   # LiveChatScreen + MonologuePanel
│   │   ├── widgets/           # AgentRoster, TurnIndicator, ChannelTabs, etc.
│   │   └── styles/            # Textual CSS
│   └── web/
│       ├── api.py             # FastAPI router — REST + SSE endpoints
│       └── frontend/          # React + TypeScript + MUI
├── orchestrators/             # User-loadable Python orchestrators (project root)
│   └── basic.py               # Copy of src/orchestrators/basic.py (default)
├── session-templates/         # Built-in YAML session templates (committed to repo)
├── sessions/                  # Auto-saved transcripts (gitignored, default output path)
├── agents/                    # Claude Code agent definition files
├── dev/                       # Design documents
├── tests/
├── pyproject.toml
└── .env.example
```

---

## 4. Component Designs

### 4.1 Session Engine

`SessionEngine` is the central coordinator. It is instantiated with a `SessionConfig` (loaded from a template YAML) and runs the session loop asynchronously.

**Session loop:**

```
initialize:
  - load config, validate
  - instantiate ProviderClient for each agent
  - initialize ChannelRouter with channel definitions
  - initialize EventBus
  - attach TranscriptWriter to EventBus
  - inject topic + role into each agent's system prompt
  - emit CHANNEL_CREATED events

loop (while not done):
  1. Snapshot current SessionState
  2. Call orchestrator.orchestrate(state) → OrchestratorOutput
  3. If output.session_end → break
  4. If output.rule_violations → emit RULE_VIOLATION events, re-prompt violating agent
  5. Update game_state from output.game_state_updates → emit GAME_STATE event
  6. For each agent_id in output.next_agents (sequential or parallel):
       a. Build agent context: ChannelRouter.build_context(agent_id, state)
       b. Call ProviderClient.complete(model, context) → raw_response
       c. Parse raw_response → (monologue_text, message_text, routing_hints)
       d. Emit MONOLOGUE event (observer-only; NOT added to any agent's context)
       e. Emit MESSAGE event with channel_id, sender_id, recipient_id (if private)
       f. Append message to master event log
  7. Emit TURN event

finalize:
  - TranscriptWriter.flush() → write markdown + JSON
```

**Why this loop works for both sequential and parallel:** step 6 passes `output.next_agents` as a list. If it contains one agent ID it's sequential. If it contains multiple, the engine fires them with `asyncio.gather()`.

---

### 4.2 Provider Abstraction via LiteLLM

All LLM calls go through a single `ProviderClient` backed by LiteLLM.

**Rationale for LiteLLM:** It already exists in `~/projects/airlock/`, provides a unified OpenAI-compatible interface for Anthropic, OpenAI, Google, Mistral, and local routers, and reduces this project to writing one provider client instead of four. It handles API key routing, model aliases, and fallbacks.

**Model string convention** (LiteLLM format):

| Provider | Example model string |
|---|---|
| Anthropic | `anthropic/claude-sonnet-4-6` |
| OpenAI | `openai/gpt-4o` |
| Google | `google/gemini-2.0-flash` |
| Mistral | `mistral/mistral-large-latest` |
| LiteLLM router | `litellm/router-model-name` or direct URL |

```python
class ProviderClient(Protocol):
    async def complete(
        self,
        model: str,
        messages: list[dict],   # OpenAI-format [{role, content}, ...]
        temperature: float = 0.7,
        **kwargs,
    ) -> CompletionResult: ...

@dataclass
class CompletionResult:
    text: str           # full raw response text (may contain XML tags)
    usage: TokenUsage   # prompt_tokens, completion_tokens
    model: str          # actual model used (may differ from requested on fallback)
```

---

### 4.3 Agent Context Construction

Each agent has a **filtered view** of the conversation history, not a shared one. The `ChannelRouter` constructs this view fresh on each turn.

**Context construction for agent A:**

```
context = [system_prompt(A)]  # role, persona, topic, channel instructions

for event in master_event_log:
    if event.type == MESSAGE:
        if event.channel == PUBLIC:           → include
        elif event.channel == TEAM(A's team): → include
        elif event.channel == PRIVATE
             and A is sender or recipient:    → include
        else:                                 → exclude
    elif event.type == MONOLOGUE:             → always exclude
    elif event.type == GAME_STATE:            → include as system message
    elif event.type == RULE_VIOLATION
         and A is the violating agent:        → include as system message
```

**Trade-off TD-004:** Rebuilding context from the full event log on every turn is O(n) in history length. For typical sessions (< 200 turns, < 50 agent messages each) this is negligible. For very long sessions (research marathons), a rolling summary or sliding window would be needed. This is deferred — a `max_turns` cap provides a practical upper bound for now.

---

### 4.4 Agent Communication Protocol

Agents need to be able to route their messages to specific channels (team, private) and optionally emit internal reasoning (monologue). Rather than tool use (which requires all providers to support it consistently), the solution uses **structured XML tags** in the agent's response.

**System prompt instructs each agent:**

```
Communication rules:
- A plain response is a PUBLIC message visible to all.
- To address your team only, wrap your message: <team>your message</team>
- To send a private message to one agent, wrap it: <private to="AgentName">your message</private>
- To show your internal reasoning (visible only to the observer), wrap it: <thinking>your thoughts</thinking>

You may combine them in one response. The thinking block, if present, must come first.
Example:
<thinking>I should challenge the premise here.</thinking>
<team>Let's agree to focus on economics, not ethics, for the next two rounds.</team>
I believe the economic argument is the strongest angle here.
```

The `ResponseParser` extracts:
- `<thinking>...</thinking>` → MONOLOGUE event (observer-only)
- `<team>...</team>` → MESSAGE event on the agent's team channel
- `<private to="Name">...</private>` → MESSAGE event on a private channel
- Remaining text → MESSAGE event on the public channel

**Trade-off TD-003:** XML tag parsing can fail if a model outputs malformed tags or ignores the instruction. Mitigations: robust regex-based parser (not a full XML parser) that fails gracefully to plain text; clear system prompt instruction; retry on parse failure. Tool use (Option B) would be more reliable but adds provider-level complexity and not all models support it equivalently. This can be upgraded in a later version.

---

### 4.5 Orchestrator Framework

**Protocol** (both Python and LLM orchestrators implement this):

```python
@dataclass
class OrchestratorInput:
    config: SessionConfig
    state: SessionState        # turn_number, game_state, channel event log

@dataclass
class OrchestratorOutput:
    next_agents: list[str]           # IDs to speak next; >1 means parallel
    game_state_updates: dict         # key-value mutations to apply
    rule_violations: list[RuleViolation]  # violations detected in last turn
    session_end: bool
    end_reason: str | None           # "max_turns", "win_condition", "completion_signal", etc.

# Protocol
def orchestrate(input: OrchestratorInput) -> OrchestratorOutput: ...
```

**Python orchestrator** (`orchestrators/basic.py`):
- Implements round-robin `next_agents` selection
- Checks `max_turns` and `max_rounds` limits
- Reads `completion_signal` from config and checks it heuristically (string match in latest messages) — sufficient for v1
- No game rule enforcement (game-specific orchestrators extend this)

**LLM orchestrator** (`src/orchestrators/llm.py`):
- Serializes `OrchestratorInput` to a structured prompt
- Calls the configured provider/model
- Parses the response back into `OrchestratorOutput` (structured output / JSON mode)
- The orchestrator's `persona` field becomes its system prompt

**Loading a Python orchestrator:**

```python
def load_orchestrator(config: OrchestratorConfig) -> Callable:
    if config.type == "python":
        module = importlib.import_module(f"orchestrators.{config.module}")
        return module.orchestrate
    elif config.type == "llm":
        return LLMOrchestrator(config).orchestrate
```

---

### 4.6 Event Bus

The EventBus follows a **fluent, asyncio-native Observable pattern** — inspired by ReactiveX (Rx) but implemented without RxPY, staying entirely within the asyncio world.

#### Why not RxPY directly?

`RxPY` has its own scheduler model that doesn't compose cleanly with `asyncio`. Bridging them (via `AsyncIOScheduler`) introduces two concurrent concurrency models in the same process — a source of subtle bugs in a Textual app that is entirely asyncio-native. The operator model is the valuable part of Rx; the scheduler model is the friction.

#### Design: asyncio-native Subject with fluent pipe

```python
class EventBus:
    """Emitter side — the session engine calls emit()."""
    def emit(self, event: SessionEvent) -> None: ...
    def stream(self) -> AsyncStream: ...          # returns a subscribable observable

class AsyncStream:
    """Observable side — consumers chain operators and subscribe."""
    def filter(self, predicate: Callable[[SessionEvent], bool]) -> AsyncStream: ...
    def map(self, transform: Callable[[SessionEvent], Any]) -> AsyncStream: ...
    def subscribe(self, handler: Callable) -> AsyncSubscription: ...
    def __aiter__(self): ...   # also usable as async-for in @work tasks
```

Each `AsyncStream` wraps its own `asyncio.Queue`. Calling `.filter()` or `.map()` returns a new `AsyncStream` with a lightweight forwarding coroutine — no shared mutable state between streams.

#### How consumers use it — fluent and self-documenting

```python
# MonologuePanel: react to MONOLOGUE tokens; clear on every TURN
bus.stream()
   .filter(lambda e: e.type in ("MONOLOGUE", "TURN"))
   .subscribe(monologue_panel.handle)

# Public channel chat log
bus.stream()
   .filter(lambda e: e.type == "MESSAGE" and e.channel_id == "public")
   .map(format_chat_message)
   .subscribe(public_log.append)

# TranscriptWriter: everything
bus.stream().subscribe(transcript_writer.record)

# Web SSE: serialize all events to JSON for the browser
bus.stream()
   .map(lambda e: e.model_dump_json())
   .subscribe(sse_queue.put_nowait)
```

This removes large `if/elif` filtering blocks from subscribers. Each surface declares exactly what it cares about at the point of subscription. The MonologuePanel's "clear on TURN" reactive relationship is expressed directly rather than buried in event-handling logic.

#### Fan-out and lifecycle

- Each call to `bus.stream()` returns an independent stream with its own queue — true fan-out.
- Subscribing returns an `AsyncSubscription` with a `.cancel()` method for cleanup.
- No message broker, no Redis, no inter-process communication.

**Trade-off TD-007 (updated):** The EventBus is single-process asyncio. TUI and Web UI cannot share a live session simultaneously — each runs its own process. For v1 this is acceptable (TUI is primary). A distributed future would replace `EventBus` with a broker-backed `Subject` (e.g., Redis pub/sub) behind the same interface, requiring no changes to consumers.

---

### 4.7 Monologue — Prompt Engineering vs. Model-Native Thinking

Two approaches exist for capturing an agent's internal reasoning:

| Approach | How | Pros | Cons |
|---|---|---|---|
| **Prompt-based** (recommended v1) | Instruct agent to wrap thoughts in `<thinking>` tags | Works with every model, portable, predictable cost | Model may not always comply; not true internal state |
| **Model-native** | Claude extended thinking (`"thinking": {"type": "enabled"}`), o1 reasoning tokens | True internal reasoning, not "performed" thinking | Provider-specific, not universally available, variable cost |

**Recommendation:** Implement prompt-based `<thinking>` tags for v1. This works across all providers and requires no special API calls. Add a per-agent `monologue_mode` config option (`prompt` | `native`) so model-native thinking can be activated for Claude when available, without changing the event model. The `MONOLOGUE` event looks the same to the UI regardless of which approach was used.

---

### 4.8 Parallel Turns

When `OrchestratorOutput.next_agents` contains more than one agent ID, the engine fires them with `asyncio.gather()`.

**Display of parallel responses:**

Since multiple agents are generating simultaneously, their responses arrive in completion order (fastest first). In both the TUI and GUI, parallel responses are displayed as they arrive — not held until all are done.

To visually signal parallel activity:
- The turn indicator shows: `Turn 7 — Nova, Rex generating in parallel...`
- Each response appears in the chat log as it completes, labeled with the agent's name and color
- A subtle `[parallel]` badge appears on messages that were generated simultaneously

**Trade-off TD-006:** Displaying in completion order means the conversation log may not reflect the "logical" order if one agent's response was informed by another's (since parallel agents don't see each other's concurrent responses). This is architecturally correct — parallel agents genuinely don't have each other's output — but may look disjointed. Mitigations: the orchestrator only enables parallel when the setting explicitly allows it (e.g., research parallel subtasks), not in social or game modes.

---

### 4.9 Transcript Engine

`TranscriptWriter` subscribes to the EventBus and accumulates all events. It writes two files on session end:

**Markdown transcript** (`sessions/<title>_<setting>_<timestamp>.md`):
```markdown
# Session: Claude vs GPT Debate: AI Ethics
**Setting:** game | **Date:** 2026-02-22T14:31:00 | **Turns:** 18

## Agents
| Name | Model | Role | Team |
|------|-------|------|------|
| Advocate | anthropic/claude-sonnet-4-6 | proponent | — |
...

---

### Turn 1 — Advocate [public]
I believe AI systems deserve legal recognition because...

### Turn 1 — Advocate [thinking] *(observer only)*
> I should open with the strongest philosophical argument first...

### Turn 2 — Opposition [public]
The very premise conflates legal personhood with moral standing...
```

**JSON sidecar** (`sessions/<title>_<setting>_<timestamp>.json`):
```json
{
  "session_id": "...",
  "title": "...",
  "events": [
    {"type": "MESSAGE", "turn": 1, "agent_id": "advocate", "channel": "public",
     "model": "anthropic/claude-sonnet-4-6", "text": "...", "timestamp": "..."},
    {"type": "MONOLOGUE", "turn": 1, "agent_id": "advocate", "text": "...", ...}
  ]
}
```

**Crash resilience:** The writer flushes a checkpoint file every 10 events (configurable) so a crash mid-session doesn't lose everything.

---

### 4.10 Memory Stub

`src/memory.py` contains two no-op functions matching the interface that a real implementation would use:

```python
def save_memory(agent_id: str, session_id: str, data: dict) -> None:
    """Stub. See GitHub issue #1 for persistent memory implementation."""
    pass

def load_memory(agent_id: str) -> dict:
    """Stub. See GitHub issue #1 for persistent memory implementation."""
    return {}
```

The session engine calls these at session end (`save_memory`) and before system prompt construction (`load_memory`). With the stubs in place, wiring up a real backend later only requires implementing these two functions.

---

## 5. Key Data Models

All models use **Pydantic v2** throughout — for session config, runtime state, events, and environment/API key management. `pydantic-settings` replaces `python-dotenv` entirely, giving typed, validated access to all external configuration.

#### Application settings (replaces python-dotenv)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    mistral_api_key: str = ""
    litellm_router_url: str = "http://localhost:4000"

    sessions_path: Path = Path("./sessions")
    session_templates_path: Path = Path("./session-templates")
    transcript_checkpoint_interval: int = 10

settings = Settings()   # singleton; loaded once at startup
```

Keys are accessed as `settings.anthropic_api_key` — typed, IDE-autocompleted, validated — not via `os.environ.get()` scattered through the codebase.

#### Session configuration (loaded from YAML template)

Cross-field validation runs at load time; misconfiguration surfaces before the session starts:

```python
# Session configuration (loaded from YAML template)
class SessionConfig(BaseModel):
    title: str
    description: str
    type: Literal["games", "social", "task-completion", "research", "problem-solve"]
    setting: str
    topic: str
    orchestrator: OrchestratorConfig
    agents: list[AgentConfig]
    channels: list[ChannelConfig] = []
    hitl: HITLConfig = HITLConfig(enabled=False)
    transcript: TranscriptConfig = TranscriptConfig()
    max_turns: int | None = None
    completion_signal: str | None = None
    game: GameConfig | None = None

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "SessionConfig":
        if self.type == "games" and self.game is None:
            raise ValueError("template type 'games' requires a 'game' block")
        agent_ids = {a.id for a in self.agents}
        for ch in self.channels:
            for member in ch.members:
                if member not in agent_ids:
                    raise ValueError(f"channel member '{member}' is not in the agents list")
        return self

class AgentConfig(BaseModel):
    id: str
    name: str
    provider: str
    model: str
    role: str
    persona: str = ""
    team: str | None = None
    monologue: bool = False
    monologue_mode: Literal["prompt", "native"] = "prompt"

class ChannelConfig(BaseModel):
    id: str
    type: Literal["public", "team", "private"]
    members: list[str] = []   # agent IDs; empty means all

# Runtime state
class SessionState(BaseModel):
    session_id: str
    turn_number: int
    game_state: dict
    events: list[SessionEvent]   # master event log (all channels)
    agents: dict[str, AgentState]

class AgentState(BaseModel):
    config: AgentConfig
    status: Literal["idle", "thinking", "speaking", "done"]

# Events
class SessionEvent(BaseModel):
    type: Literal["MESSAGE","MONOLOGUE","TURN","GAME_STATE","RULE_VIOLATION","CHANNEL_CREATED","SESSION_END"]
    timestamp: datetime
    turn_number: int
    agent_id: str | None = None
    channel_id: str | None = None
    recipient_id: str | None = None
    text: str | None = None
    data: dict = {}
```

---

## 6. Technology Stack

| Layer | Technology | Version | Rationale |
|---|---|---|---|
| Python runtime | CPython | 3.11+ | asyncio improvements, `tomllib` stdlib, match statements |
| Async framework | asyncio (stdlib) | — | Sufficient; no need for Trio or anyio |
| Provider abstraction | **LiteLLM** | latest | Unified interface, already in airlock, 100+ providers |
| Config models | **Pydantic v2** | 2.x | Type-safe YAML loading, cross-field validation, JSON schema generation |
| App settings / env | **pydantic-settings** | 2.x | Typed `.env` + env var loading; replaces python-dotenv |
| YAML parsing | PyYAML | 6.x | Standard |
| TUI | **Textual** | 0.70+ | As specified |
| Web backend | **FastAPI** | 0.110+ | Async-native, SSE via `StreamingResponse`, OpenAPI docs free |
| Web frontend | **React + TypeScript** | 18.x / 5.x | As specified |
| UI components | **Material UI** | 5.x | As specified |
| Package management | **uv** + `pyproject.toml` | latest | Fast, modern, replaces pip+venv |
| Testing | pytest + pytest-asyncio | latest | Async test support |

---

## 7. Trade-offs Worked Through

The following trade-offs were evaluated and resolved. They are presented here for awareness — all have a recommended resolution, but any of them can be revisited.

---

### TD-001 — Provider abstraction: LiteLLM vs. native SDKs

**Options:**
- **A) LiteLLM** — one unified call surface for all providers.
- **B) Native SDKs** — `anthropic`, `openai`, `google-generativeai`, `mistralai` — one per provider.

**Analysis:** Native SDKs give full access to provider-specific features (extended thinking parameters, tool use formats, etc.). LiteLLM abstracts these away but covers ~95% of common use cases. The project already uses LiteLLM in `airlock/`. Writing four separate provider clients is significant ongoing maintenance burden.

**Recommendation: LiteLLM (Option A).** A thin `ProviderClient` protocol means a native SDK can always be plugged in for a specific model if a feature is needed that LiteLLM doesn't expose.

---

### TD-002 — Runtime model: session engine as library vs. standalone service

**Options:**
- **A) Library** — both TUI and Web UI import and embed the engine in-process.
- **B) Standalone service** — engine runs as a separate process; UIs connect over HTTP/WebSocket.

**Analysis:** Option B enables both UIs to observe the same live session simultaneously and supports distributed use. Option A is simpler to build, debug, and deploy with no IPC overhead. For v1, both UIs don't need to share a live session.

**Recommendation: Library (Option A) for v1.** The EventBus interface is designed so it can be replaced with a broker-backed implementation if Option B becomes desirable. No architectural rework needed.

---

### TD-003 — Agent channel routing: XML tags vs. tool use

**Options:**
- **A) XML tags** — `<thinking>`, `<team>`, `<private to="Name">` parsed from response text.
- **B) Tool/function calling** — agent invokes a `send_message(channel, recipient, text)` tool.
- **C) Structured JSON output** — agent returns `{channel, recipient, text}` JSON.

**Analysis:** Tool use (B) is the most reliable mechanism but requires consistent support across all providers and adds a tool definition to every agent's context. JSON output (C) disrupts the conversational feel. XML tags (A) work with any model capable of following system prompt instructions, which is universal.

**Recommendation: XML tags (Option A) for v1.** The `ResponseParser` is forgiving: if tags are absent or malformed, the full response is treated as a public message. Option B can be added later for providers that benefit from it.

---

### TD-004 — Agent context: rebuild from log vs. per-agent accumulated buffer

**Options:**
- **A) Rebuild from master event log** — filter on every turn.
- **B) Per-agent accumulated list** — append filtered messages as they arrive.

**Analysis:** Option B is O(1) per turn but requires maintaining N separate lists and careful invalidation if channel membership changes. Option A is O(n) in log size but simple and always correct.

**Recommendation: Rebuild from log (Option A).** Bounded by `max_turns`, typical session logs are small. If profiling reveals a bottleneck in long research sessions, a cache can be layered in without changing the interface.

---

### TD-005 — Monologue: prompt-based `<thinking>` tags vs. model-native reasoning

**Options:**
- **A) Prompt-based** — instruct agent to output `<thinking>` before responding.
- **B) Model-native** — Claude extended thinking, o1-style reasoning tokens.
- **C) Both** — prompt-based as default, native as opt-in per agent.

**Analysis:** Model-native reasoning (B) is more authentic — it reflects actual internal model state rather than "performed" reasoning — but is provider-specific and not available on all models. Prompt-based (A) works everywhere but the agent is constructing a post-hoc explanation, not true internal state.

**Recommendation: Option C** — prompt-based as the portable default, model-native activated by `monologue_mode: native` per agent for models that support it (currently Claude 3.5+). The `MONOLOGUE` event is emitted the same way either path; the UI doesn't need to know which mode was used.

---

### TD-006 — Parallel turns: display order and cross-talk

**Options:**
- **A) Display in completion order** — show responses as they arrive from the API.
- **B) Hold until all parallel agents complete, then display in a defined order.**

**Analysis:** Option B gives a cleaner "reveal" but adds latency (slowest agent gates display). Option A is more responsive but may produce a disordered-looking transcript.

**Recommendation: Option A (display in completion order).** The visual grouping of parallel turn messages (a `[parallel turn N]` header) makes it clear they were generated simultaneously. Parallel turns are only enabled in modes where the "order" is semantically less important (research subtasks, not debates).

---

### TD-007 — Event bus: asyncio-native fluent Observable vs. RxPY vs. bare asyncio.Queue

**Options:**
- **A) Bare asyncio.Queue with fan-out** — simple but every subscriber writes its own filter/dispatch logic.
- **B) RxPY** — full Rx operator set, but requires scheduler bridging to integrate with asyncio; two concurrency models in one process.
- **C) Asyncio-native fluent Observable (Subject + AsyncStream)** — Rx-inspired operator chaining (`.filter()`, `.map()`, `.subscribe()`) implemented natively over asyncio.Queue; no RxPY dependency.

**Analysis:** Option B brings real power but the asyncio/Rx scheduler impedance mismatch is a maintenance risk in a Textual app. Option A is simplest but pushes filtering logic into consumers, making the MonologuePanel's "clear on TURN" reactive relationship implicit. Option C gets the fluent/composable benefits of Rx while staying in asyncio.

**Recommendation: Option C — asyncio-native fluent Observable.** The `AsyncStream` is a thin layer (< 100 lines) over `asyncio.Queue` with `.filter()`, `.map()`, and `.subscribe()` methods. In the future, if a distributed session engine is needed, the `EventBus` can be replaced with a broker-backed `Subject` behind the same interface with no changes to consumers.

---

### TD-008 — Config and settings: Pydantic v2 + pydantic-settings throughout

**Options:**
- **A) Pydantic v2 for models, python-dotenv for env vars** — split approach.
- **B) Pydantic v2 + pydantic-settings everywhere** — unified, all configuration is typed Pydantic models.
- **C) marshmallow or manual validation** — more verbose, less FastAPI-native.

**Analysis:** `pydantic-settings` gives the same `.env` loading as `python-dotenv` but surfaces API keys and paths as typed model fields with IDE autocomplete and validation. FastAPI is built on Pydantic; using it throughout means one mental model for all config/data shapes. Cross-field validation via `@model_validator` catches template misconfiguration at load time.

**Recommendation: Option B — Pydantic v2 + pydantic-settings throughout.** `python-dotenv` is dropped from the stack.

---

## 8. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| R-001 | LLM models don't reliably follow XML tag routing instructions | Medium | Medium | Robust fallback parser; system prompt testing across all providers |
| R-002 | Long sessions exceed provider context window limits | Medium | High | `max_turns` cap; add rolling-summary stub to `ChannelRouter` for future |
| R-003 | LiteLLM version incompatibilities with individual provider APIs | Low-Medium | Medium | Pin LiteLLM version; integration tests per provider |
| R-004 | asyncio.gather() for parallel agents creates race conditions in event log | Low | High | Acquire a per-turn lock before appending to master event log |
| R-005 | Game rule enforcement via LLM orchestrator is non-deterministic | Medium | Medium | Provide a Python game orchestrator option for rules that must be precise |
| R-006 | TUI and web UI state diverge if engine is run twice | Low | Low | Document: one process, one UI surface at a time for v1 |
| R-007 | `completion_signal` matching is too naive (string-based) for complex tasks | Medium | Low | Works for v1; LLM-based completion detection can replace it later |

---

## 9. Resolved Decisions

All decisions below were resolved in a planning session on 2026-02-22.

| # | Decision | Resolution | Notes |
|---|---|---|---|
| D-001 | Provider abstraction library | **LiteLLM via airlock** | All provider calls route through the local LiteLLM router at `~/projects/airlock/`. Auth, model routing, and rate limiting handled by airlock. `litellm_router_url` in `pydantic-settings` is the single config point. |
| D-002 | Runtime model (library vs. service) | **In-process library (v1)** | Session Engine lives in `src/engine/` and is imported directly by TUI and web UI. Clean module boundaries make a future service extraction tractable. |
| D-003 | Agent channel routing mechanism | **XML response tags** | Agents use `<thinking>`, `<private to="Name">`, `<team>` tags. `ResponseParser` strips and routes. Untagged output defaults to public — safe fallback. Tool calling ruled out: not universally supported across providers via airlock. |
| D-004 | Monologue capture | **Prompt-based baseline + provider-native where supported** | `monologue: true` on `AgentConfig` is a capability declaration. All agents get a system prompt addition instructing `<thinking>` tag use. Provider layer switches to native thinking tokens (Claude extended thinking, o1 reasoning) where the model supports it. Implementation detail owned entirely by the provider layer — engine and config schema are unaffected. |
| D-005 | Parallel turn display | **Completion order with `[parallel]` badge** | Messages appear in the log as each agent finishes. Each carries a `[parallel]` badge so the observer understands simultaneous turns. Transcripts capture wall-clock timestamps. |
| D-006 | Python version | **3.11+** | No features require 3.12; 3.11 has the broadest compatibility across the stack. |
| D-007 | Package manager | **uv** | Standard `pyproject.toml`. Fast installs and lockfile resolution. |
| D-008 | Session browser layout | **Filtered list (type tabs + ListView)** | Keyboard-dominant, ≤3 keystrokes to launch. Card grid deferred — see issue [#2](https://github.com/coreyt/one-0-one/issues/2). |
| D-009 | API key configuration | **`.env` file loaded by `pydantic-settings`** | `python-dotenv` dropped. All env/secrets surface as typed fields on `Settings`. |
| D-010 | Game-specific orchestrators | **`orchestrators/` directory, named by game** | `orchestrators/basic.py`, `orchestrators/twenty_questions.py`, etc. Session templates reference by module name. Migrate to `games/` subdirectories if a game grows beyond a single file — see issue [#3](https://github.com/coreyt/one-0-one/issues/3). |

---

*All open decisions resolved. PSD is approved for implementation.*
