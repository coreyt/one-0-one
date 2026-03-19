# Game-Playing Policy Questions

This document captures the moderation-failure policy surface that is now configurable in session templates.

## Current Decisions

- Actor move failures exhaust to `forfeit` by default.
- Moderator/backend failures exhaust to `session_error` by default.
- Social or hidden-information moderated games should generally override actor exhaustion to `skip_turn`.
- Retry limits are configured separately for actor failures and moderator/backend failures.

## Config Surface

Use these keys under `game.moderation.failure_policy`.

```yaml
game:
  moderation:
    failure_policy:
      # Number of retries for player-caused failures before exhaustion policy applies.
      # Available settings: any integer >= 0
      # Recommended default: 2
      actor_retry_limit: 2

      # What to do when a player's moderated failures are exhausted.
      # Available settings:
      #   - skip_turn: advance to the next actor without applying an action
      #   - forfeit: end the game and award the win to the other player
      #   - session_error: terminate the session as an engine error
      # Recommended default for deterministic games: forfeit
      # Recommended override for social / hidden-information moderated games: skip_turn
      actor_retry_exhaustion_action: forfeit

      # Number of retries for moderator/backend failures before exhaustion policy applies.
      # Available settings: any integer >= 0
      # Recommended default: 2
      moderator_retry_limit: 2

      # What to do when the moderator/backend fails repeatedly.
      # Available settings:
      #   - skip_turn: abandon the current actor's turn and continue
      #   - session_error: terminate the session as an engine/runtime failure
      # Recommended default: session_error
      moderator_retry_exhaustion_action: session_error
```

## Failure Categories

- Actor failures:
  - unparsable move text
  - illegal move rejected by code moderation
  - moderator rejection of the player's move

- Moderator/backend failures:
  - malformed moderator payload
  - moderator acceptance that cannot be applied without a valid `next_state`
  - provider/backend failures while obtaining the moderator decision

## Initial Template Values

- `session-templates/game-connect-four.yaml`
  - `actor_retry_exhaustion_action: forfeit`
  - `moderator_retry_exhaustion_action: session_error`

- `session-templates/game-battleship.yaml`
  - `actor_retry_exhaustion_action: forfeit`
  - `moderator_retry_exhaustion_action: session_error`

Future social-deduction or negotiation templates should set:

```yaml
game:
  moderation:
    failure_policy:
      actor_retry_exhaustion_action: skip_turn
```
