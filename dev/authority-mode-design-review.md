# Authority-Mode Design Review

## Problem

The repo previously inferred game authority from `game.plugin` alone. That created a mode-confusion failure:

- Plugin-backed games were treated as engine-authoritative by the runtime.
- Some templates still prompted an LLM moderator as if it were the referee of record.
- Asymmetric games like Battleship need different visibility depending on whether the engine or the LLM owns state progression.

This made it possible to mix two incompatible models in one session.

## Explicit Modes

### `engine_authoritative`

Use this when the engine/plugin owns:

- legal move validation
- hidden information
- state updates
- win/draw detection
- session termination

Implications:

- `game.plugin` is required.
- Players should use structured move output for deterministic actions.
- Moderators are presentation-only and may receive authoritative state for narration.
- The router should inject authoritative game state and suppress legacy `GAME_STATE` context duplication.

### `llm_authoritative`

Use this when the moderator/referee LLM owns:

- adjudication
- private role/state reasoning
- game progression
- winner declaration

Implications:

- No engine-owned game plugin is attached.
- Orchestrators and prompts remain the main control surface.
- The runtime should not silently apply an authoritative plugin state machine behind the moderator.

## Current Mapping

- Connect Four: `engine_authoritative`
- Battleship: `engine_authoritative`
- Mafia: `llm_authoritative`

## Battleship Review Outcome

Battleship had a mode-confusion bug:

- The template told the Admiral to validate shots and decide winners.
- The engine already treated plugin-backed games as engine-authoritative.
- The moderator was not receiving full authoritative state, so the Admiral could not actually narrate accurately in asymmetric play.

Fix:

- Keep Battleship engine-authoritative.
- Make the Admiral presentation-only.
- Give the moderator authoritative state for narration while preserving player asymmetry.
- Use structured player shot output.

## Mafia Review Outcome

Mafia is currently a prompt/orchestrator-driven game, not an authoritative engine plugin. That is valid for `llm_authoritative` capability testing, but the template should be understood as moderator-owned game logic rather than engine-enforced mechanics.

## Follow-On Guidance

- Do not combine `game.plugin` with `llm_authoritative` until there is a defined hybrid contract.
- If a future game needs both:
  one mode should remain canonical for state transitions, and the other should be explicitly advisory or shadow-only.
