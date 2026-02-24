# one-0-one — User Needs and Requirements

## 1. Purpose

This document captures the user needs and requirements for **one-0-one**, a multi-agent
conversation platform where multiple LLMs (and optionally a human) engage in structured
dialogue. Requirements are derived from the project vision (`dev/vision.md`) and the
design specifications in the agent files (`agents/`).

---

## 2. User Personas

Four personas drive the requirements. All personas may appear in the same session.

| ID | Persona | Core Need |
|----|---------|-----------|
| P-1 | **Conversationalist** | Launch a multi-agent conversation on a topic quickly and watch it unfold |
| P-2 | **Observer** | Watch agents interact with full visibility into all channels and agent reasoning |
| P-3 | **Game Player** | Participate in a structured game with rule enforcement, roles, and teams |
| P-4 | **Builder** | Configure custom sessions, save reusable templates, and review past transcripts |

---

## 3. User Needs

User needs are high-level statements of what each persona must be able to accomplish.
They are intentionally solution-neutral.

### 3.1 Conversationalist

| ID | Need |
|----|------|
| UN-001 | I need to provide a topic or subject that all agents receive as shared context at the start of the session so the conversation is grounded in what I care about. |
| UN-001a | I need to start a multi-agent conversation quickly without lengthy setup so I can explore ideas without friction. |
| UN-002 | I need to pick from a library of pre-built session templates so I don't have to configure agents from scratch every time. |
| UN-003 | I need to choose which AI models participate and what roles they play so I can compare perspectives across providers. |
| UN-004 | I need to join the conversation as a participant at any point so I can steer or contribute to the discussion. |
| UN-005 | I need to pause, inject context, or end the session at will so I remain in control of the interaction. |

### 3.2 Observer

| ID | Need |
|----|------|
| UN-006 | I need each agent's messages to be visually labeled and color-coded so I can instantly tell who is speaking. |
| UN-007 | I need to see an agent's internal reasoning separately from its spoken messages so I can understand its decision-making without cluttering the conversation view. |
| UN-008 | I need to see all communication channels (public, team, private) simultaneously so I have complete situational awareness of the session. |
| UN-009 | I need a clear visual indicator distinguishing private messages from public ones so I never mistake a private exchange for a public statement. |
| UN-010 | I need to know which agent is currently "thinking" so I can follow the pace and turn order of the session. |

### 3.3 Game Player

| ID | Need |
|----|------|
| UN-011 | I need the game rules and how-to-play instructions presented to me before the game starts so I know how to participate. |
| UN-012 | I need an orchestrator to enforce game rules and reject invalid moves so the game is fair and well-structured. |
| UN-013 | I need to see the current game state (score, turn count, round, win/loss) at all times so I know where the game stands. |
| UN-014 | I need a private team channel when playing on a side so my team can strategize without the other team seeing. |
| UN-015 | I need to send a message privately to a specific other agent or participant so I can communicate outside the main channel when the game calls for it. |

### 3.4 Builder

| ID | Need |
|----|------|
| UN-016 | I need a guided setup wizard so I can configure a session without editing YAML by hand. |
| UN-017 | I need to save a session configuration as a reusable template so I can repeat or share setups. |
| UN-018 | I need to load session templates from outside the built-in library so I can use configs stored elsewhere. |
| UN-019 | I need to choose between an LLM orchestrator and a Python function orchestrator so I can balance flexibility and determinism for each session. |
| UN-020 | I need to review saved session transcripts so I can analyze past conversations and refine agent personas and prompts. |
| UN-021 | I need to export transcripts in portable formats so I can share or post-process them outside the application. |

---

## 4. Functional Requirements

Requirements are organized by subsystem. Each requirement is labeled **SHALL** (mandatory)
or **SHOULD** (strongly desired but not blocking).

### 4.1 Provider Layer

| ID | Requirement |
|----|-------------|
| FR-001 | The system SHALL support Anthropic, OpenAI, Google, and Mistral as LLM providers. |
| FR-002 | The system SHALL support a local LiteLLM router (default: `~/projects/airlock/`) as an additional provider. The router path SHALL be configurable. |
| FR-003 | Each agent in a session SHALL be independently configurable with a provider and model, enabling cross-provider conversations. |
| FR-004 | Provider API keys SHALL be read from environment variables or a `.env` file. They SHALL NOT be committed to the repository, logged, or displayed in full in any UI. |

### 4.2 Session Templates

| ID | Requirement |
|----|-------------|
| FR-005 | Session templates SHALL be stored as YAML files. |
| FR-006 | The built-in template library SHALL be located at `session-templates/` in the project root and SHALL be committed to the repository (not gitignored). |
| FR-007 | Each session template SHALL include the required fields: `title`, `description`, `type`, and `topic`. Optional fields include: `max_turns`, `channels` (team definitions), and `completion_signal`. |
| FR-008 | Valid template types SHALL be: `games`, `social`, `task-completion`, `research`, `problem-solve`. |
| FR-009 | The session browser (TUI and GUI) SHALL display each template's `title`, `description`, and `type`. The browser SHALL support filtering templates by `type`. |
| FR-010 | Users SHALL be able to load external session templates from any directory outside `session-templates/`. |
| FR-011 | The setup wizard SHALL generate a valid YAML session template file as its output. |
| FR-011a | The `topic` field SHALL be provided to every agent as shared context in their system prompt at the start of the session. The orchestrator is responsible for injecting topic context before the first turn. |
| FR-011b | Session templates SHALL support an optional `max_turns` integer field that caps the total number of agent turns. When `max_turns` is reached the orchestrator SHALL conclude the session gracefully. Games use `max_rounds` inside the `game` block for the same purpose. |

### 4.3 Conversation Modes (Settings)

| ID | Requirement |
|----|-------------|
| FR-012 | The system SHALL support the following built-in settings: `social`, `research`, `algorithm_development`, `task_completion`, `game`. |
| FR-013 | Each setting SHALL define default values for: `turn_order`, `agent_identity`, `concurrency`, and `rule_enforcement`. |
| FR-013a | Built-in setting defaults SHALL be: `social` (round-robin, persona, sequential, none), `research` (orchestrator, persona/model_id, parallel, none), `algorithm_development` (orchestrator, model_id, parallel, none), `task_completion` (orchestrator, persona, sequential, none), `game` (round-robin, persona, sequential, hard). |
| FR-014 | Session templates SHALL be able to override any setting default — including `turn_order`, `agent_identity`, `concurrency`, and `rule_enforcement` — on a per-session basis. |
| FR-015 | Agent identity mode SHALL be configurable as `persona` (named character), `model_id` (raw model identifier), or `configurable` (user sets per session). |
| FR-015a | The setup wizard SHALL auto-populate setting-appropriate defaults when a setting type is selected. The user SHALL be able to change any pre-populated value before saving or running. |

### 4.4 Agent Configuration

| ID | Requirement |
|----|-------------|
| FR-016 | Each agent SHALL be configurable with: `id`, `name`, `provider`, `model`, `role`, `persona` (prompt text), `team` (optional), and `monologue` (enabled/disabled). |
| FR-017 | Agent display colors SHALL be assigned from a fixed palette of 6 colors, cycled by agent index. Colors SHALL be consistent across all views for the duration of a session. |
| FR-018 | The human HITL participant SHALL be treated as a first-class agent with a configurable role and consistent display identity. |

### 4.5 Turn Management

| ID | Requirement |
|----|-------------|
| FR-019 | The system SHALL support the following turn order modes: `round-robin`, `random`, `orchestrator-directed`, `freeform`. |
| FR-020 | The active turn order mode SHALL be specified in the session template and enforced by the orchestrator. |
| FR-021 | The system SHALL support parallel agent responses where the session mode allows (e.g., simultaneous research subtasks). |
| FR-022 | The UI SHALL display a turn indicator (agent name + animated status) showing which agent is currently generating a response. |

### 4.6 Orchestrator

| ID | Requirement |
|----|-------------|
| FR-023 | The system SHALL support two orchestrator types: `llm` (any configured provider/model acts as meta-agent) and `python` (a loadable Python module). An LLM orchestrator SHALL be configurable with a `provider`, `model`, and `persona` (system prompt describing its orchestration role). |
| FR-024 | Both orchestrator types SHALL implement the same interface: accept current session state as input; return a next-action object as output. |
| FR-025 | The system SHALL include a built-in basic Python orchestrator (`orchestrators/basic.py`) implementing round-robin turn order with basic rule validation. |
| FR-026 | `orchestrators/basic.py` SHALL be the default orchestrator when none is specified in a session template. |
| FR-027 | Additional Python orchestrators SHALL be loadable by placing a module in `orchestrators/` with no changes to core framework code. |
| FR-028 | The orchestrator SHALL emit the following typed session events: `MESSAGE`, `MONOLOGUE`, `TURN`, `GAME_STATE`, `RULE_VIOLATION`, `CHANNEL_CREATED`. |

### 4.7 Message Channel Model

| ID | Requirement |
|----|-------------|
| FR-029 | The system SHALL support four message visibility scopes: `public`, `team`, `private` (1:1), and `monologue`. |
| FR-029a | Session templates SHALL support an optional `channels` array defining team channels, their `id`, `type`, and `members` (list of agent IDs). Agents not assigned to a team SHALL participate only in public and private channels. |
| FR-030 | Public messages SHALL be visible to all agents and all human participants. |
| FR-031 | Team messages SHALL be visible only to agents assigned to that team and the observer. |
| FR-032 | Private (1:1) messages SHALL be visible only to the sender, the named recipient, and the observer. |
| FR-033 | Monologue content SHALL be visible to the observer only. It SHALL never be injected into any other agent's context window. This SHALL be enforced by the orchestrator. |
| FR-034 | The observer SHALL always have visibility into all four channel types, regardless of any assigned role. |
| FR-035 | A HITL player assigned to a team SHALL see: the public channel + their team channel + private messages they are party to. They SHALL NOT see other teams' channels. |
| FR-036 | Every message event SHALL carry a `channel_id` field used to route display to the correct UI surface. |
| FR-037 | Private messages SHALL always be visually distinguished from public messages in every UI view (lock icon, recipient label, distinct background). |

### 4.8 Human-in-the-Loop (HITL)

| ID | Requirement |
|----|-------------|
| FR-038 | HITL participation SHALL be optional and configured per session template (`hitl.enabled`). |
| FR-039 | The human participant SHALL be assignable to a named role (e.g., player, judge, moderator, stakeholder). |
| FR-040 | The human SHALL be able to take a turn in the normal turn rotation. |
| FR-041 | The human SHALL be able to inject a message out-of-turn without consuming a turn slot. |
| FR-042 | The human SHALL be able to pause and resume the session at any time. |
| FR-043 | The human SHALL be able to end the session at any time. |
| FR-044 | When the HITL player is on a team, the input interface SHALL include a channel selector (Public / Team / Private) before message submission. |

### 4.9 Games

| ID | Requirement |
|----|-------------|
| FR-045 | Game definitions SHALL be stored within session templates and SHALL include: `name`, `description`, `rules` (list), `how_to_play`, `turn_order`, `roles` (with counts), `win_condition`, `hitl_compatible`, and `max_rounds`. |
| FR-046 | The orchestrator SHALL hard-enforce game rules, reject invalid moves/responses, and emit a `RULE_VIOLATION` event for each rejection. |
| FR-046a | The orchestrator SHALL maintain and update game state across turns, including at minimum: current round, turn count, per-agent scores, and win/loss status. Game state SHALL be emitted via `GAME_STATE` events after each relevant turn. |
| FR-047 | The session UI SHALL display the current game state: turn count, round, score, and win/loss status. |
| FR-048 | The built-in game library SHALL include templates for: 20 Questions, Debate Club, Story Builder, Code Golf, Trivia, Murder Mystery, and Prisoner's Dilemma. |
| FR-049 | Games involving teams or sides SHALL support team channel partitioning via the message channel model. |

### 4.10 Transcript Persistence

| ID | Requirement |
|----|-------------|
| FR-050 | All sessions SHALL be automatically saved as transcripts upon completion. No user action shall be required to trigger a save. |
| FR-050a | The transcript save path SHALL be configurable per session template (`transcript.path`). The default path SHALL be `./sessions/`. |
| FR-050b | `task-completion` type sessions SHOULD support an optional `completion_signal` field (natural language description) that the orchestrator uses to determine when the session's task is complete and trigger an orderly conclusion. |
| FR-051 | Transcripts SHALL be saved in Markdown format. A JSON sidecar SHOULD also be saved. |
| FR-052 | Transcript filenames SHALL include the session title, setting type, and a timestamp. |
| FR-053 | Each transcript entry SHALL record: agent ID, agent name, model, role, channel, turn number, timestamp, and message content. |
| FR-054 | Monologue content SHALL be included in transcripts, clearly marked as observer-only (e.g., in a fenced block with a `monologue` label). |
| FR-055 | Private and team messages SHALL be included in transcripts, labeled with their channel scope. |
| FR-056 | A memory stub (`src/memory.py`) with `save_memory()` and `load_memory()` no-op functions SHALL exist as a placeholder for future persistent agent memory (tracked in GitHub issue #1). |

### 4.11 TUI

| ID | Requirement |
|----|-------------|
| FR-057 | The TUI SHALL be built with the Textual framework. |
| FR-058 | The TUI SHALL include three primary screens: `SessionBrowserScreen`, `SetupWizardScreen`, `LiveChatScreen`. |
| FR-059 | Every TUI workflow SHALL be fully operable without a mouse. |
| FR-060 | The TUI footer SHALL always display active key bindings for the current screen. |
| FR-061 | `LiveChatScreen` SHALL display channel tabs for Public, Team(s), and Private channels. Monologue SHALL NOT appear as a channel tab. |
| FR-062 | `LiveChatScreen` SHALL include a `MonologuePanel` — a resizable bottom strip, hidden by default, toggled with `M`. |
| FR-063 | `MonologuePanel` SHALL stream the active agent's monologue in real-time and SHALL clear on each new `TURN` event. |
| FR-064 | All LLM API calls and file I/O in the TUI SHALL use Textual's `@work` decorator (non-blocking). |
| FR-064a | The HITL human participant SHALL be labeled "You" (or their configured role name if set) in a visually distinct color (white / bright neutral) in the TUI chat log. |
| FR-064b | The observer role SHALL be visually distinguished in the TUI. When the human is in observer-only mode (no HITL role assigned), a status indicator SHALL make the observer-only status explicit. |
| FR-064c | Orchestrator and system messages (rule violations, game state updates) SHALL be displayed in a visually distinct style (dimmed, italic, full-width) separate from agent speech in the TUI chat log. |

### 4.12 Web GUI

| ID | Requirement |
|----|-------------|
| FR-065 | The web UI SHALL be built with React + TypeScript and Material UI v5+. |
| FR-066 | The web UI backend SHALL be FastAPI, serving a REST + SSE API. |
| FR-067 | The web UI SHALL include: Session Library, Setup Wizard, Live Chat, Transcript Browser, Transcript Reader, and Settings pages. |
| FR-068 | Live chat messages SHALL be streamed to the browser via Server-Sent Events (SSE). Partial messages SHALL be displayed as tokens arrive. |
| FR-069 | The Live Chat page SHALL display channel tabs (Public, Team(s), Private) in the chat column. Monologue SHALL NOT appear as a channel tab. |
| FR-070 | The Live Chat page SHALL include a Monologue Drawer — a collapsible right panel, hidden by default, toggled with the 🧠 toolbar button or `M` key. |
| FR-071 | When the Monologue Drawer is open, the agent roster SHALL collapse to icon-only width. |
| FR-072 | The Monologue Drawer SHALL stream the active agent's monologue in real-time and SHALL clear on each new `TURN` event. |
| FR-073 | The web UI SHALL support transcript export in Markdown and JSON formats. |
| FR-074 | All filter, pagination, and sort state in the web UI SHALL be encoded in URL query parameters (shareable links, browser back/forward support). |
| FR-074a | The HITL human participant SHALL be labeled "You" (or their configured role name) in a visually distinct color in the web UI chat view. |
| FR-074b | When the human is in observer-only mode, the web UI SHALL display a persistent "👁 Observing" badge in the Live Chat toolbar to make the observer-only status unambiguous. |
| FR-074c | Orchestrator and system messages SHALL be displayed full-width, dimmed, and italic — visually distinct from agent speech — in the web UI chat column. |
| FR-074d | The Transcript Browser SHALL support full-text search across transcript content. |
| FR-074e | The Transcript Browser SHALL support date-range filtering of transcripts. |

---

## 5. Non-Functional Requirements

### 5.1 Usability

| ID | Requirement |
|----|-------------|
| NFR-001 | Launching a saved session from the TUI browser SHALL require ≤ 3 keystrokes. |
| NFR-002 | Creating a new session from scratch in the TUI (excluding typing topic and persona text) SHALL require ≤ 20 keystrokes. |
| NFR-003 | Every TUI feature SHALL be accessible without a mouse. |
| NFR-004 | Agent colors SHALL be visually distinct and consistent across all views and sessions for the same agent index. |
| NFR-005 | The observer SHALL always be able to determine which agent is speaking, which channel a message belongs to, and whether a message is private, without ambiguity. |

### 5.2 Performance

| ID | Requirement |
|----|-------------|
| NFR-006 | The TUI main thread SHALL never block; all I/O SHALL be performed asynchronously. |
| NFR-007 | The web UI SHALL stream LLM tokens via SSE and render partial messages as they arrive; it SHALL NOT wait for a complete response before displaying. |
| NFR-008 | The web UI SHALL use skeleton loading states for page transitions, not full-page spinners. |
| NFR-009 | The web UI transcript browser SHALL use server-side pagination; no more than 50 rows SHALL be rendered client-side at a time. |

### 5.3 Security

| ID | Requirement |
|----|-------------|
| NFR-010 | Provider API keys SHALL be stored in environment variables or a `.env` file. They SHALL NOT be committed to the repository. |
| NFR-011 | API keys SHALL never appear in full in log output, TUI display, or web UI after initial entry. |
| NFR-012 | The web UI SHALL mask API keys to the last 4 characters after initial save. The full key value SHALL NOT be returned to the browser after initial entry. |

### 5.4 Extensibility

| ID | Requirement |
|----|-------------|
| NFR-013 | New LLM providers SHALL be addable by implementing the provider interface without modifying core orchestrator or framework code. |
| NFR-014 | New Python orchestrators SHALL be addable by placing a module in `orchestrators/` without modifying core framework code. |
| NFR-015 | New game definitions SHALL be addable by creating a session template YAML file without any code changes. |
| NFR-016 | New conversation settings (modes) SHOULD be addable with minimal changes confined to the settings layer. |

### 5.5 Reliability

| ID | Requirement |
|----|-------------|
| NFR-017 | If an LLM provider call fails, the orchestrator SHALL emit a recoverable error event and allow the session to pause or continue gracefully without crashing. |
| NFR-018 | Transcript auto-save SHALL be resilient to session crashes. The system SHOULD write incremental transcript checkpoints so that a crash does not result in a fully empty transcript. |

---

## 6. Constraints

| ID | Constraint |
|----|-----------|
| CON-001 | The TUI is the primary UI surface. The web GUI is supplementary. Core workflows SHALL be achievable from the TUI alone. |
| CON-002 | Agent memory is ephemeral (within-session only) in the current implementation. Cross-session memory is deferred (see GitHub issue #1). |
| CON-003 | Session templates in `session-templates/` SHALL be committed to the repository and SHALL NOT be gitignored. |
| CON-004 | The local LiteLLM router is expected at `~/projects/airlock/` by convention; the path SHALL be user-configurable. |
| CON-005 | The project is Python-based. The TUI (Textual) and web backend (FastAPI) SHALL be Python. The web frontend MAY use TypeScript/React. |

---

## 7. Requirements Traceability

| User Need | Functional Requirements | NFRs |
|-----------|------------------------|------|
| UN-001 | FR-007, FR-011a | — |
| UN-001a | FR-005–FR-011b, FR-057–FR-060 | NFR-001, NFR-002 |
| UN-002 | FR-005–FR-009 | NFR-001 |
| UN-003 | FR-001–FR-003, FR-016–FR-017 | — |
| UN-004 | FR-038–FR-044 | — |
| UN-005 | FR-041–FR-043 | — |
| UN-006 | FR-017, FR-029–FR-037, FR-064a, FR-074a | NFR-004, NFR-005 |
| UN-007 | FR-033, FR-062–FR-063, FR-070–FR-072 | NFR-005 |
| UN-008 | FR-029–FR-036, FR-064b, FR-074b | NFR-005 |
| UN-009 | FR-032, FR-037, FR-064c, FR-074c | NFR-005 |
| UN-010 | FR-022, FR-028 | — |
| UN-011 | FR-045–FR-046 | — |
| UN-012 | FR-046, FR-046a | — |
| UN-013 | FR-046a, FR-047 | — |
| UN-014 | FR-031, FR-035, FR-049, FR-029a | — |
| UN-015 | FR-029, FR-032, FR-037 | — |
| UN-016 | FR-011, FR-015a, FR-058 | NFR-002 |
| UN-017 | FR-005–FR-008, FR-011 | CON-003 |
| UN-018 | FR-010 | — |
| UN-019 | FR-023–FR-027 | NFR-014 |
| UN-020 | FR-050–FR-055, FR-067, FR-074d, FR-074e | — |
| UN-021 | FR-051, FR-073 | — |
