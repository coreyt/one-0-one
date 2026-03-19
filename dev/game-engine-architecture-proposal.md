# one-0-one — Game Engine Architecture Proposal

**Status:** Proposed  
**Date:** 2026-03-18  
**Supersedes:** nothing yet; intended as the target architecture for refactoring the current prototype

## 1. Why This Exists

The current codebase has a usable session runner, but not a real game engine.
Game truth is split across:

- `SessionEngine`
- ad hoc orchestrators in `orchestrators/`
- prompt text embedded in templates

That split is the main reason deterministic games, social deduction games, hidden-information games, and monologue capture all feel inconsistent.

This document defines the replacement architecture.

## 2. Design Goals

The replacement engine must support these equally well:

1. Deterministic, stateful games
2. Social and hidden-information games
3. Mixed human + LLM participation
4. Public, team, private, and observer-only communication
5. Provider-native internal monologue capture, with prompt fallback only where necessary

Non-goals for the first refactor:

- distributed execution
- long-term memory beyond the session
- tool-calling generality outside game play

## 3. Core Position

The engine should stop treating games as prompt scripts.

A game must become a first-class code object with:

- authoritative state
- typed actions
- legal move validation
- turn and phase progression
- per-agent visibility rules
- win / loss / draw detection

Orchestrators should schedule and sequence play around game rules, not own the rules.

## 4. Target Architecture

### 4.1 Main Layers

1. `SessionEngine`
   - owns runtime loop, event emission, provider calls, transcript persistence
   - does not own game rules

2. `GameRuntime`
   - wraps one game implementation plus current authoritative state
   - answers whose turn it is, what each actor can see, and what actions are legal

3. `Game` implementations
   - `ConnectFourGame`, `BattleshipGame`, `MafiaGame`, `TelephoneGame`, etc.
   - each owns its own state model and action model

4. `TurnPolicy`
   - generic policy for sequencing speakers or simultaneous phases
   - examples: `RoundRobinPolicy`, `ModeratorDrivenPolicy`, `PhasePolicy`
   - this replaces most bespoke orchestrator routing code

5. `ProviderClient`
   - returns structured response content:
     - outward communication
     - internal monologue
     - provider metadata
     - optional structured action payload

6. `VisibilityPolicy`
   - computes what each player can see from game state and communication events
   - hidden information is enforced here and in `Game.visible_state()`

### 4.2 Runtime Loop

The engine loop should become:

1. Load config
2. Instantiate game plugin from template
3. Build initial `GameRuntimeState`
4. Emit initial game state snapshot and channels
5. Ask game runtime whose turn / phase is active
6. Build a per-actor view:
   - visible game state
   - visible communication history
   - legal actions
   - role instructions
7. Call provider
8. Parse provider result into:
   - `communication`
   - `monologue`
   - `proposed_action`
9. Validate the proposed action against game rules
10. If valid, apply it to authoritative state
11. Emit:
   - communication events
   - monologue events
   - action events
   - game state delta event
12. Advance phase / turn
13. Stop when game declares terminal state

This makes state transitions code-driven instead of narrator-prompt-driven.

## 5. First-Class Contracts

### 5.1 Game Contract

```python
from typing import Protocol, Sequence

class Game(Protocol):
    game_type: str

    def initial_state(self, config: "GameConfig", agents: Sequence["AgentConfig"]) -> "GameStateBase": ...
    def initial_channels(self, state: "GameStateBase") -> list["ChannelSpec"]: ...
    def visible_state(self, state: "GameStateBase", viewer_id: str) -> "VisibleGameState": ...
    def turn_context(self, state: "GameStateBase") -> "TurnContext": ...
    def legal_actions(self, state: "GameStateBase", actor_id: str) -> list["ActionSpec"]: ...
    def validate_action(self, state: "GameStateBase", actor_id: str, action: "GameAction") -> "ValidationResult": ...
    def apply_action(self, state: "GameStateBase", actor_id: str, action: "GameAction") -> "ApplyResult": ...
    def is_terminal(self, state: "GameStateBase") -> bool: ...
    def outcome(self, state: "GameStateBase") -> "GameOutcome | None": ...
```

### 5.2 State Contract

Replace the generic `GameState.custom: dict[str, Any]` pattern with typed game state.

```python
from pydantic import BaseModel

class GameStateBase(BaseModel):
    phase: str
    round_number: int = 0
    turn_index: int = 0
```

Each game extends this:

```python
class ConnectFourState(GameStateBase):
    board: list[list[str]]
    active_player: str
    winner: str | None = None
```

```python
class MafiaState(GameStateBase):
    day_number: int
    phase: Literal["night", "day_discussion", "day_vote", "resolution"]
    alive_players: list[str]
    roles_by_player: dict[str, str]
    known_alignments_by_player: dict[str, dict[str, str]]
    pending_votes: dict[str, str]
```

### 5.3 Action Contract

The engine needs typed actions, not free-form text guesses.

```python
class GameAction(BaseModel):
    action_type: str
    payload: dict[str, Any]
```

Examples:

- Connect Four: `{"action_type": "drop_disc", "payload": {"column": 4}}`
- Battleship: `{"action_type": "fire", "payload": {"cell": "B5"}}`
- Mafia: `{"action_type": "vote", "payload": {"target": "villager_2"}}`
- Mafia night chat: `{"action_type": "team_deliberation", "payload": {"target": "doctor"}}`

### 5.4 Apply Result Contract

```python
class ApplyResult(BaseModel):
    next_state: GameStateBase
    public_events: list["DomainEvent"] = []
    private_events: list["DomainEvent"] = []
    state_delta: dict[str, Any] = {}
    turn_advanced: bool = True
```

This keeps game transitions explicit and transcriptable.

## 6. Deterministic and Hidden-Information Games

### 6.1 Deterministic Games

Games like Connect Four and Battleship should be fully adjudicated in code.

The model should decide intent, not truth.

Example:

- player proposes `drop_disc(column=4)`
- game validates whether column 4 is legal
- game updates board
- engine emits board delta

The referee or moderator can still speak, but narration is now presentation, not authority.

### 6.2 Hidden-Information Games

Games like Mafia need two simultaneous truths:

1. authoritative full state
2. per-player visible state

That means `visible_state()` is mandatory, not optional.

The engine must never derive hidden-information visibility from chat history alone.

Examples:

- Mafia agents can know their own role; mafia can know teammate roles
- detective can know past investigation results
- villagers cannot see any of that unless revealed publicly
- observer can see all of it

This is the core abstraction missing today.

## 7. Communication Model

The current channel model is directionally right, but it needs to be tied to game state.

### 7.1 Message Kinds

Keep these kinds:

- `public`
- `team`
- `private`
- `monologue`

Add these engine-level distinctions:

- `narration`
- `system`
- `action`
- `state_delta`

### 7.2 Channel Ownership

Channels should come from game/runtime state, not only from static session config.

Examples:

- mafia team chat exists only while mafia members are alive
- private whisper channels can be created as part of a game phase
- observer-only monologue is not a channel tab; it is a separate event stream

## 8. First-Class Monologue Capture

The user requirement is clear: internal monologue should be first-class provider capability.

### 8.1 Provider Result Contract

Replace the current raw `CompletionResult.text` contract with a structured result:

```python
class MonologueSegment(BaseModel):
    text: str
    source: Literal["provider_native", "prompt_fallback"]
    redaction_status: Literal["raw", "filtered"]

class CommunicationSegment(BaseModel):
    visibility: Literal["public", "team", "private"]
    text: str
    recipient_id: str | None = None

class ProviderResponse(BaseModel):
    model: str
    usage: TokenUsage
    communication: list[CommunicationSegment]
    monologue: list[MonologueSegment] = []
    raw_text: str = ""
    parsed_action: dict[str, Any] | None = None
```

### 8.2 Provider Responsibilities

Provider integrations should:

- request native reasoning / thinking when the model supports it
- capture it in a structured field
- mark whether it is provider-native or prompt-derived
- never merge it back into public communication

### 8.3 Fallback Policy

If provider-native monologue is unavailable:

- ask for a structured private reasoning block in the prompt
- parse it as fallback
- label it `source="prompt_fallback"`

This satisfies the user requirement without blocking unsupported models.

## 9. Orchestrator Refactor

The current `OrchestratorOutput` is too weak because it mixes:

- turn scheduling
- game state mutation
- rule enforcement

In the target design:

- `Game` owns state mutation and legality
- `TurnPolicy` owns scheduling
- `SessionEngine` owns execution
- `Moderator/Narrator` becomes an ordinary actor when needed

This means many current orchestrators shrink dramatically:

- `turn_based.py` becomes a reusable `ModeratorDrivenAlternatingPolicy`
- `mafia.py` becomes a `MafiaGame` plus phase-specific policy
- `telephone.py` becomes a `TelephoneGame` state machine, not a pile of ad hoc `custom` dict updates

## 10. Config Changes

The config schema should move from metadata-oriented to engine-oriented.

### 10.1 Proposed Shape

```yaml
session:
  title: "Connect Four"
  type: games
  topic: "Play Connect Four"

game:
  plugin: connect_four
  settings:
    rows: 6
    columns: 7
    connect_n: 4

turn_policy:
  type: moderator_driven_alternating

observer:
  monologue_visibility: full

agents:
  ...
```

### 10.2 Principles

- game-specific settings belong under `game.settings`
- static template metadata should not be the only source of rules
- channel definitions can still be declared, but game plugins may add dynamic channels

## 11. Event Model Changes

Add game-aware events instead of overloading `MESSAGE` and `GAME_STATE`.

Recommended new event types:

- `ACTION_PROPOSED`
- `ACTION_REJECTED`
- `ACTION_APPLIED`
- `STATE_DELTA`
- `PHASE_CHANGED`
- `TURN_STARTED`
- `TURN_COMPLETED`

Keep `MESSAGE`, `MONOLOGUE`, `CHANNEL_CREATED`, `SESSION_END`, `INCIDENT`.

This will make the TUI and web UI much easier to reason about.

## 12. Migration Plan

### Phase 1: contracts

- add new `games/` package
- define `Game`, `GameStateBase`, `GameAction`, `ApplyResult`, `VisibleGameState`
- define structured provider response types

### Phase 2: engine compatibility layer

- update `SessionEngine` to consume structured provider results
- support both legacy parser-driven sessions and new game plugins temporarily

### Phase 3: first plugin conversions

- convert Connect Four as the reference deterministic game
- convert Mafia as the reference hidden-information game

### Phase 4: retire prompt-owned state

- remove board/state authority from moderator prompts
- keep moderator prompts for flavor and narration only

### Phase 5: remove legacy orchestrator assumptions

- shrink or delete bespoke `orchestrators/*.py` where replaced by game plugins + turn policies

## 13. Immediate Implementation Priorities

If work starts now, do it in this order:

1. make provider responses structured and monologue-aware
2. define the `Game` contract and typed state/action models
3. update engine event flow around action validation and state deltas
4. migrate one deterministic game
5. migrate one hidden-information game

## 14. Expected Benefits

- deterministic games become testable and reproducible
- hidden-information games gain a real visibility boundary
- monologue handling becomes explicit and provider-aware
- UIs can render state and action transitions without guessing from prose
- templates become configuration for the engine, not substitutes for engine logic

## 15. What Should Not Continue

The following patterns should be treated as legacy:

- game truth stored only in moderator persona text
- `game_state.custom` as the primary runtime model
- regex parsing as the main representation of game actions
- bespoke orchestrator files as the home of game rules
- round counting derived from total agent slots rather than game semantics
