# Game Engine Refactor TODO

Last updated: 2026-03-18

## Current Priority Order

- [x] Unify plugin-backed turn execution around moderation backends
  - Route all plugin-backed game turns through the shared moderation backend interface.
  - Remove the deterministic-only apply logic from `SessionEngine` turn execution.
  - Cover `deterministic`, `llm_moderated`, and `hybrid_audit` with engine-level TDD.

- [x] Add experiment-grade hybrid audit recording
  - Persist primary decision, shadow decision, divergence status, and enough turn context to compare runs.
  - Expose audit data through events/state/transcripts so experiments are inspectable after the run.

- [x] Define engine behavior for invalid or ambiguous moderated turns
  - [x] Convert malformed moderator payloads and invalid accepted actions into rule violations with state preserved.
  - [x] Implement bounded retry with clarification via `RULE_VIOLATION` context injection.
  - [x] Decide and implement skip and longer-horizon termination rules.
  - [x] Cover those remaining behaviors with TDD.

- [x] Wire a real provider-backed moderator adapter into the live session path
  - [x] Instantiate `LLMModerationBackend` from session config instead of test-only injected callables.
  - [x] Use the moderator prompt/response contract to call the moderator model during live engine runs.
  - [x] Replace the temporary thread-bridge implementation with a native async moderation path.

- [x] Add true end-to-end game-play coverage through the supported runtime surface
  - [x] Cover one deterministic game end to end through the real session runner and transcript artifacts.
  - [x] Cover one moderated game end to end through the real session runner.
  - [x] Cover one moderated game end to end through the TUI screen path.

- [x] Add a hidden-information authoritative game implementation and coverage
  - [x] Add Battleship as the first authoritative hidden-information plugin.
  - [x] Cover per-player `visible_state` differences and hidden-state correctness through unit/runtime/session tests.

- [x] Verify monologue capture in actual game sessions end to end
  - [x] Cover prompt-fallback monologue in a real moderated game session and transcript output.
  - [ ] Cover provider-native monologue in a real game session when a live/native-capable test seam is in place.

## In Progress Foundation

- [x] Add shared moderation backend contract with deterministic and hybrid audit shapes
- [x] Add moderation config modes: `deterministic`, `llm_moderated`, `hybrid_audit`
- [x] Add provider-agnostic `LLMModerationBackend`
- [x] Add provider-facing moderator prompt/response contract helpers

## Recently Completed

- [x] Add first-class structured provider monologue/communication support
- [x] Add authoritative game contracts and runtime scaffolding
- [x] Implement Connect Four as the first authoritative deterministic plugin
- [x] Integrate plugin-backed deterministic Connect Four into `SessionEngine`
- [x] Remove moderator dependency from plugin-backed deterministic Connect Four flow
- [x] Clean plugin-backed prompt context to use authoritative visible state and legal actions

## Notes

- The immediate goal is not more game-specific logic. It is to make the engine execute all plugin-backed games through one moderation contract.
- Hybrid audit should be treated as a first-class experimentation mode, not a debug-only sidecar.
- Continue using TDD for each engine-refactor slice.

## Current Assessment

- The application can play a deterministic game in the engine path today: Connect Four is covered by engine integration tests and reaches win/draw outcomes.
- The application can now also play a deterministic hidden-information game in the engine path today: Battleship is covered through unit/runtime/session-runner tests and reaches terminal outcome.
- The supported runtime surface now has real game-play E2E coverage through the session runner and TUI for moderated Connect Four.
- The current `tests/test_web_api_e2e.py` file still only exercises the optional web/API plumbing that exists in the repo, and is not the primary proof of product-critical game play.
- The LLM-moderated path is now wired to a real provider-backed moderator in the live session path and is covered by engine integration and session-runner E2E tests.
- Provider-native monologue is still not proven in a real game session.
