# one-0-one — Project Vision

## Overview

**one-0-one** is a multi-agent conversation platform where multiple LLMs (and optionally a human) engage in structured dialogue around a shared topic or activity. The goal is to create a flexible, extensible framework for orchestrating AI-to-AI (and AI-to-human) conversations across different modes and use cases.

---

## Core Concepts

### Multi-Agent Conversation
- Multiple LLM agents participate in a shared conversation thread
- Each agent can have a distinct persona, role, or perspective
- Conversation history is shared and visible to all participants
- Turn order and agent behavior are governed by the active **setting**

### Human-in-the-Loop (HITL)
- A human participant can join as a first-class conversant
- HITL can take on a role (moderator, player, researcher, etc.)
- Human can observe only, or intervene at any point
- Controls to pause, redirect, inject context, or end conversations

### Topic / Subject Grounding
- Every session is anchored to a user-provided topic or prompt
- The topic is presented to all agents as shared context at session start
- Topics can be open-ended (discussion) or structured (game, task, research)

---

## Provider Support

The application connects to multiple LLM providers:

| Provider       | Notes                                           |
|----------------|-------------------------------------------------|
| Anthropic      | Claude models (Opus, Sonnet, Haiku)             |
| OpenAI         | GPT-4o, o1, etc.                                |
| Google         | Gemini models                                   |
| Mistral        | Mistral, Mixtral models                         |
| LiteLLM Router | Local proxy at `~/projects/airlock/`            |

Each agent in a session can be assigned a different provider and model, enabling cross-model conversations and comparison.

---

## Conversation Modes (Settings)

A **setting** is a named configuration bundle that defines the rules, roles, turn order, agent identity style, and enforcement behavior for a session. All settings are supported by the same underlying framework; settings simply activate the appropriate behavior.

### Setting Properties

| Property         | Description                                                            |
|------------------|------------------------------------------------------------------------|
| `turn_order`     | `round-robin`, `random`, `orchestrator`, `freeform`                    |
| `agent_identity` | `persona` (names + personalities), `model_id`, or `configurable`      |
| `concurrency`    | `sequential` or `parallel` (where the mode supports it)               |
| `rule_enforcement` | `none`, `advisory`, or `hard` (orchestrator validates each turn)    |
| `hitl_role`      | Optional role for the human participant                                |

### Built-in Settings

#### Social
- Free-form discussion on a topic
- Agents adopt conversational personas with names
- Turn order: round-robin or freeform
- Concurrency: sequential
- Rule enforcement: none
- Good for exploring opinions, brainstorming, or entertainment

#### Research
- Agents take on researcher roles (domain expert, skeptic, synthesizer, etc.)
- Turn order: orchestrator-directed (to ensure balanced coverage)
- Concurrency: parallel responses allowed for independent subtasks
- Agent identity: persona (role-based) or model ID
- Output can be summarized into a structured report

#### Algorithm Development
- Agents collaborate to design, critique, and refine an algorithm or system
- Roles: architect, implementer, tester, reviewer
- Turn order: orchestrator-directed
- Concurrency: parallel for independent work (e.g., multiple implementations)
- Code blocks and pseudocode supported

#### Game
- Structured interaction governed by defined rules
- Agent roles are determined by the game definition
- Turn order: game-specific (defined in game config)
- Rule enforcement: **hard** — an orchestrator agent validates moves/responses and rejects violations
- See **Games Menu** below

---

## Games Menu

Games are first-class config objects. Each game definition includes:

```yaml
game:
  name: "20 Questions"
  description: "..."
  rules:
    - "..."
  how_to_play: "..."
  turn_order: round-robin
  roles:
    - name: thinker
      count: 1
      description: "Picks the secret thing and answers yes/no"
    - name: guesser
      count: 1-N
      description: "Asks yes/no questions"
  win_condition: "..."
  hitl_compatible: true
  max_rounds: 20
```

### Example Games

| Game               | Description                                                       |
|--------------------|-------------------------------------------------------------------|
| 20 Questions       | One agent thinks of something; others ask yes/no questions        |
| Debate Club        | Two agents argue opposing sides; a judge agent scores rounds      |
| Story Builder      | Agents contribute sentences to build a collaborative story        |
| Code Golf          | Agents compete to solve a problem in fewest tokens/lines          |
| Trivia             | A host agent asks questions; players answer and are scored        |
| Murder Mystery     | Roleplay mystery with assigned characters and hidden information  |
| Prisoner's Dilemma | Classic game theory scenario played iteratively                   |

---

## Session Configuration

### Config File Format

Session configs are YAML files with a **title** and **description** displayed in the TUI:

```yaml
title: "Claude vs GPT Debate: AI Ethics"
description: "A structured debate between two models on the ethics of AI autonomy."

setting: debate
topic: "AI systems should be granted legal personhood."

agents:
  - id: agent_1
    name: "Advocate"
    provider: anthropic
    model: claude-sonnet-4-6
    role: proponent
  - id: agent_2
    name: "Opposition"
    provider: openai
    model: gpt-4o
    role: opponent
  - id: judge
    name: "Judge"
    provider: google
    model: gemini-2.0-flash
    role: judge

hitl:
  enabled: true
  role: moderator

transcript:
  auto_save: true
  format: markdown
  path: ./sessions/
```

### Setup Flow

1. User opens TUI → sees **session browser** (list of saved configs with title + description)
2. User can **select an existing config** to launch immediately, or
3. Launch the **setup wizard** which walks through setting, topic, agents, and HITL options
4. Wizard generates a `.yaml` config file (saved to a configurable directory)
5. Session launches from the config

---

## Transcript Persistence

- All sessions are **automatically saved** on completion (and optionally incrementally)
- Saved as markdown (human-readable) with optional JSON sidecar (machine-readable)
- Filename includes session title, setting, and timestamp
- Transcripts capture: agent ID, model, role, turn number, timestamp, message content

---

## High-Level Architecture (Draft)

```
┌─────────────────────────────────────────────────────┐
│              one-0-one TUI (Textual)                 │
│  Session Browser │ Setup Wizard │ Live Chat View     │
└────────────────┬────────────────────────────────────┘
                 │
     ┌───────────▼────────────────┐
     │     Session Orchestrator    │
     │  - Loads setting + config   │
     │  - Manages turn order       │
     │  - Enforces rules (games)   │
     │  - Handles parallel turns   │
     │  - Writes transcript        │
     └───┬───────────┬────────────┘
         │           │
  ┌──────▼──┐   ┌────▼──────────────────────────┐
  │  Human  │   │         Agent Pool              │
  │  (HITL) │   │  Agent 1: Claude (Sonnet)       │
  └─────────┘   │  Agent 2: GPT-4o                │
                │  Agent 3: Gemini Flash           │
                │  Agent N: Mistral / LiteLLM      │
                └────────────────────────────────-┘
                              │
          ┌───────────────────▼────────────────────┐
          │            Provider Layer               │
          │  Anthropic │ OpenAI │ Google │ Mistral  │
          │  LiteLLM Router (~/projects/airlock/)   │
          └────────────────────────────────────────┘
```

---

## Orchestrator Design

The orchestrator is the engine that drives turn order, validates rules, and directs agent behavior. It supports two interchangeable implementations:

### LLM Orchestrator
- A meta-agent (any supported provider/model) that receives full session state and decides what happens next
- Can make nuanced decisions about turn order, rule interpretation, and game state
- Configured in the session template like any other agent, with `role: orchestrator`

### Python Function Orchestrator
- A loadable Python function (`orchestrators/<name>.py`) that implements a defined interface
- Enables deterministic, reproducible, fast orchestration without LLM calls
- Can be swapped in per session via the session template config
- **Bundled fallback**: `orchestrators/basic.py` — a simple round-robin orchestrator with basic rule validation, used when no orchestrator is specified

### Interface Contract
Both orchestrator types must implement the same interface:
- Input: current session state (agents, history, rules, game state)
- Output: next action (who speaks, any state mutations, any rule violations)

```yaml
# In session template:
orchestrator:
  type: python          # "python" or "llm"
  module: basic         # for python: name in orchestrators/; for llm: omit
  # for llm type:
  provider: anthropic
  model: claude-sonnet-4-6
```

---

## Agent Memory

### Current Behavior: Ephemeral (within-session only)
- Agents receive the full conversation history as context on each turn
- No state persists after the session ends
- This is the default and only implemented behavior

### Future: Optional Persistent Memory
- A stub `memory.py` module exists with `save_memory()` and `load_memory()` no-ops
- Persistent memory is tracked as a planned feature (see GitHub issue)
- When implemented, memory will be opt-in per agent, configured in the session template

---

## Session Templates

Session templates are YAML files stored in the `session-templates/` directory at the project root. They are **committed to the repo** and serve as the bundled library of ready-to-run sessions.

### Template Schema

```yaml
title: "..."           # Displayed in TUI session browser
description: "..."     # Displayed in TUI session browser
type: games            # One of: games, social, task-completion, research, problem-solve

setting: ...
topic: "..."
orchestrator:
  type: python
  module: basic

agents:
  - id: agent_1
    name: "..."
    provider: anthropic
    model: claude-sonnet-4-6
    role: "..."

hitl:
  enabled: false
  role: null

transcript:
  auto_save: true
  format: markdown
  path: ./sessions/
```

### Template Types

| Type               | Description                                                        |
|--------------------|--------------------------------------------------------------------|
| `games`            | Structured games with rules, roles, and win conditions            |
| `social`           | Free-form discussion, personas, no strict rules                   |
| `task-completion`  | Agents collaborate to complete a defined task or deliverable      |
| `research`         | Multi-perspective research and analysis on a topic                |
| `problem-solve`    | Structured problem-solving, algorithm design, debugging           |

### Loading Templates in the TUI

- TUI **session browser** lists all templates in `session-templates/` with title, description, and type badge
- User can also **load an external template** from any directory via a file picker
- Templates are never modified by the TUI; running a session creates a copy with session-specific overrides

---

## Message Channel Model

Every message in a session belongs to one of four visibility scopes. This is a
first-class framework concept enforced by the orchestrator and the UI layer.

| Channel Type | Visible To | Example Use |
|---|---|---|
| **Public** | All agents + observer + HITL | Main conversation, game moves, debate turns |
| **Team** | Agents on the same team + observer | Strategy huddle in Murder Mystery or Debate |
| **Private (1:1)** | Sender + recipient + observer | An agent whispering to one other agent |
| **Monologue** | Observer only (never in any agent's context) | Agent's chain-of-thought / internal reasoning |

### Key rules
- The **observer** (human watching without a role) always sees all four channel types.
- A **HITL player** on a team sees Public + their team's channel + Private messages they
  are party to. They do NOT see other teams' channels.
- **Monologue** content is never injected into another agent's context window. It is
  display-only. This is enforced by the orchestrator.
- **Private messages** are always visually distinguished (lock icon, recipient label,
  distinct background) in both TUI and GUI.
- The framework emits typed events: `MESSAGE`, `MONOLOGUE`, `TURN`, `GAME_STATE`,
  `RULE_VIOLATION`, `CHANNEL_CREATED`.

### UI surface allocation

| Content | TUI | GUI |
|---|---|---|
| Public / Team / Private messages | Channel tabs in the chat window | Channel tabs in the chat column |
| Internal monologue | **MonologuePanel** — resizable bottom strip, `M` to toggle | **Monologue Drawer** — collapsible right panel, 🧠 to toggle |

Monologue is intentionally **not** a tab alongside public/team/private. It is a separate
cognitive stream — not a message sent to anyone — and is placed on a distinct surface so
the observer can watch the conversation and the reasoning simultaneously, side by side.

### Session template config

```yaml
channels:
  - id: public
    type: public
  - id: team_red
    type: team
    members: [agent_1, agent_2]
  - id: team_blue
    type: team
    members: [agent_3, agent_4]

agents:
  - id: agent_1
    team: team_red
    monologue: true       # enables chain-of-thought capture for this agent
    ...
```

---

## Open Questions / Future Decisions

- For parallel turns, how should conflicts or cross-talk be handled in the TUI display?
- What should the session browser look like — a list, a grid, or a filtered/typed table?
