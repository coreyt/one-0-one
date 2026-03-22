"""Authoritative Mafia game implementation."""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from pydantic import Field

from src.games.contracts import (
    ActionSpec,
    AgentGameContext,
    ApplyResult,
    ChannelSpec,
    GameAction,
    GameOutcome,
    GameStateBase,
    TurnContext,
    ValidationResult,
    VisibleGameState,
)
from src.games.renderers import JournalEntry, render_journal_text, render_journal_xml

if TYPE_CHECKING:
    from src.session.config import AgentConfig, GameConfig


_ROLE_COUNTS = {
    "mafia": 3,
    "detective": 1,
    "doctor": 1,
    "villager": 4,
}

_DISCUSSION_PHASES = {"night_mafia_discussion", "day_discussion"}
_ACTION_SCHEMA_TARGET = {
    "night_mafia_vote": "target",
    "night_detective": "investigate",
    "night_doctor": "protect",
    "day_vote": "vote_for",
}


class MafiaState(GameStateBase):
    """Authoritative runtime state for classic 9-player Mafia."""

    phase: str = "night_mafia_discussion"
    players: list[str] = Field(default_factory=list)
    player_names: dict[str, str] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)
    alive_players: list[str] = Field(default_factory=list)
    eliminated: list[str] = Field(default_factory=list)
    revealed_roles: dict[str, str] = Field(default_factory=dict)
    mafia_order: list[str] = Field(default_factory=list)
    detective_id: str | None = None
    doctor_id: str | None = None
    winner: str | None = None
    winning_players: list[str] = Field(default_factory=list)
    discussion_order: list[str] = Field(default_factory=list)
    discussion_index: int = 0
    night_mafia_votes: dict[str, str] = Field(default_factory=dict)
    detective_results: list[dict[str, str]] = Field(default_factory=list)
    doctor_history: list[str] = Field(default_factory=list)
    night_kill_target: str | None = None
    day_votes: dict[str, str | None] = Field(default_factory=dict)
    current_vote_order: list[str] = Field(default_factory=list)
    vote_index: int = 0
    last_public_summary: dict[str, Any] | None = None
    round_log: list[dict[str, Any]] = Field(default_factory=list)


class MafiaGame:
    """Deterministic classic Mafia with hidden information and coded resolution."""

    game_type = "mafia"

    def initial_state(
        self,
        config: "GameConfig",
        agents: list["AgentConfig"],
    ) -> MafiaState:
        players = [agent for agent in agents if agent.role != "moderator"]
        role_counts = Counter(agent.role for agent in players)
        if len(players) != 9:
            raise ValueError("Deterministic Mafia requires exactly nine non-moderator players.")
        for role, expected in _ROLE_COUNTS.items():
            if role_counts.get(role, 0) != expected:
                raise ValueError(f"Deterministic Mafia requires exactly {expected} {role} role(s).")

        player_ids = [agent.id for agent in players]
        player_names = {agent.id: agent.name for agent in players}
        roles = {agent.id: agent.role for agent in players}
        mafia_order = [agent.id for agent in players if agent.role == "mafia"]
        detective = next(agent.id for agent in players if agent.role == "detective")
        doctor = next(agent.id for agent in players if agent.role == "doctor")

        return MafiaState(
            phase="night_mafia_discussion",
            round_number=1,
            players=player_ids,
            player_names=player_names,
            roles=roles,
            alive_players=list(player_ids),
            mafia_order=mafia_order,
            detective_id=detective,
            doctor_id=doctor,
            discussion_order=list(mafia_order),
            current_vote_order=list(mafia_order),
        )

    def initial_channels(self, state: MafiaState) -> list[ChannelSpec]:
        return [
            ChannelSpec(channel_id="public", channel_type="public"),
            ChannelSpec(
                channel_id="mafia",
                channel_type="team",
                members=list(state.mafia_order),
                description="Secret Mafia coordination channel.",
            ),
        ]

    def visible_state(self, state: MafiaState, viewer_id: str) -> VisibleGameState:
        payload: dict[str, Any] = {
            "phase": state.phase,
            "round_number": state.round_number,
            "alive_players": [
                {"id": player_id, "name": state.player_names[player_id]}
                for player_id in state.alive_players
            ],
            "eliminated_players": [
                {
                    "id": player_id,
                    "name": state.player_names[player_id],
                    "role": state.revealed_roles.get(player_id),
                }
                for player_id in state.eliminated
            ],
            "current_speaker": self._current_actor(state),
            "last_public_summary": state.last_public_summary,
            "self_role": state.roles.get(viewer_id),
            "winner": state.winner,
        }

        if state.roles.get(viewer_id) == "mafia":
            payload["mafia_teammates"] = [
                {"id": player_id, "name": state.player_names[player_id]}
                for player_id in state.mafia_order
                if player_id in state.alive_players and player_id != viewer_id
            ]
        if viewer_id == state.detective_id:
            payload["detective_results"] = list(state.detective_results)
        if viewer_id == state.doctor_id:
            payload["doctor_history"] = list(state.doctor_history)

        return VisibleGameState(viewer_id=viewer_id, payload=payload)

    def turn_context(self, state: MafiaState) -> TurnContext:
        if self.is_terminal(state):
            return TurnContext(active_actor_ids=[], phase=state.phase)

        actor_id = self._current_actor(state)
        prompts = {
            "night_mafia_discussion": "Privately discuss likely town targets on the mafia channel.",
            "night_mafia_vote": "Submit exactly one mafia kill vote as JSON.",
            "night_detective": "Submit exactly one investigation target as JSON.",
            "night_doctor": "Submit exactly one protection target as JSON.",
            "day_discussion": "Speak publicly to persuade, accuse, defend, or claim.",
            "day_vote": "Submit exactly one public vote as JSON.",
        }
        return TurnContext(
            active_actor_ids=[actor_id] if actor_id else [],
            phase=state.phase,
            allow_parallel=False,
            prompt=prompts.get(state.phase, ""),
        )

    def legal_actions(self, state: MafiaState, actor_id: str) -> list[ActionSpec]:
        if self.is_terminal(state) or actor_id != self._current_actor(state):
            return []

        field_name = _ACTION_SCHEMA_TARGET.get(state.phase)
        if field_name is None:
            return []

        targets = self._legal_targets_for_phase(state, actor_id)
        schema: dict[str, Any]
        if state.phase == "day_vote":
            schema = {
                "type": "object",
                "properties": {
                    field_name: {"enum": targets + [None]},
                },
                "required": [field_name],
                "additionalProperties": False,
            }
        else:
            schema = {
                "type": "object",
                "properties": {
                    field_name: {"enum": targets},
                },
                "required": [field_name],
                "additionalProperties": False,
            }

        return [
            ActionSpec(
                action_type=state.phase,
                description=f"Structured action for {state.phase}.",
                input_schema=schema,
            )
        ]

    def validate_action(
        self,
        state: MafiaState,
        actor_id: str,
        action: GameAction,
    ) -> ValidationResult:
        if self.is_terminal(state):
            return ValidationResult(is_valid=False, reason="Game is already over.")
        if actor_id != self._current_actor(state):
            return ValidationResult(is_valid=False, reason="It is not this player's turn.")
        if state.phase not in _ACTION_SCHEMA_TARGET:
            return ValidationResult(is_valid=False, reason="This phase expects normal discussion, not a structured action.")
        if action.action_type != state.phase:
            return ValidationResult(is_valid=False, reason="Unsupported action type for the current phase.")

        field_name = _ACTION_SCHEMA_TARGET[state.phase]
        target = action.payload.get(field_name)
        legal_targets = self._legal_targets_for_phase(state, actor_id)
        if state.phase == "day_vote" and target is None:
            return ValidationResult(
                is_valid=True,
                normalized_action=GameAction(action_type=state.phase, payload={field_name: None}),
            )
        if not isinstance(target, str):
            return ValidationResult(is_valid=False, reason=f"{field_name} must reference a living player.")
        if target not in legal_targets:
            return ValidationResult(is_valid=False, reason=f"{target} is not a legal target for this phase.")
        return ValidationResult(
            is_valid=True,
            normalized_action=GameAction(action_type=state.phase, payload={field_name: target}),
        )

    def apply_action(
        self,
        state: MafiaState,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult:
        validation = self.validate_action(state, actor_id, action)
        if not validation.is_valid or validation.normalized_action is None:
            raise ValueError(validation.reason or "Invalid action.")

        phase = state.phase
        if phase == "night_mafia_vote":
            return self._apply_mafia_vote(state, actor_id, validation.normalized_action)
        if phase == "night_detective":
            return self._apply_detective_action(state, actor_id, validation.normalized_action)
        if phase == "night_doctor":
            return self._apply_doctor_action(state, actor_id, validation.normalized_action)
        if phase == "day_vote":
            return self._apply_day_vote(state, actor_id, validation.normalized_action)
        raise ValueError("This phase does not accept structured actions.")

    def apply_message_turn(
        self,
        state: MafiaState,
        actor_id: str,
        public_message: str,
    ) -> ApplyResult:
        if state.phase not in _DISCUSSION_PHASES:
            raise ValueError("This phase does not accept message turns.")
        if actor_id != self._current_actor(state):
            raise ValueError("It is not this player's turn.")
        next_state = state.model_copy(deep=True)
        next_state.turn_index += 1
        next_state.discussion_index += 1
        next_state.last_public_summary = {
            "phase": state.phase,
            "speaker_id": actor_id,
            "speaker_name": state.player_names[actor_id],
            "message_excerpt": public_message[:120],
        }

        if next_state.discussion_index >= len(next_state.discussion_order):
            if state.phase == "night_mafia_discussion":
                next_state.phase = "night_mafia_vote"
                next_state.current_vote_order = self._living_mafia(next_state)
                next_state.vote_index = 0
            else:
                next_state.phase = "day_vote"
                next_state.current_vote_order = list(next_state.alive_players)
                next_state.vote_index = 0

        return ApplyResult(
            next_state=next_state,
            state_delta={
                "phase": next_state.phase,
                "discussion_index": next_state.discussion_index,
            },
            turn_advanced=True,
        )

    def render_agent_context(
        self,
        state: MafiaState,
        viewer_id: str,
        role: str,
        *,
        config: "GameConfig | None" = None,
    ) -> AgentGameContext:
        viewer = self.visible_state(state, viewer_id)
        payload = viewer.payload
        phase = payload.get("phase")

        if role == "moderator":
            return AgentGameContext(
                instructions=[
                    "role=presentation_referee",
                    "Narrate only the authoritative state shown here.",
                    "Do not decide votes, deaths, investigations, saves, or winners.",
                    "Use the public game-generated events as the factual basis for announcements.",
                ],
                state_lines=[
                    f"authoritative_state={json.dumps(state.model_dump())}",
                    f"visible_state={json.dumps(payload)}",
                ],
            )

        my_actions = self.legal_actions(state, viewer_id)
        journal_format = config.journal_format if config is not None else "xml"
        journal_entries = self._build_journal_entries(state, viewer_id)
        journal_fn = render_journal_text if journal_format == "text" else render_journal_xml
        common_state = [
            f"phase={phase}",
            f"round_number={payload.get('round_number')}",
            f"current_speaker={json.dumps(payload.get('current_speaker'))}",
            f"visible_state={json.dumps(payload)}",
            f"legal_actions={json.dumps([a.model_dump() for a in my_actions])}",
        ]
        if journal_entries:
            common_state.append(journal_fn(journal_entries))

        if my_actions:
            _action_schemas: dict[str, tuple[str, str]] = {
                "night_mafia_vote": ('{"target": "<agent_id>"}', '{"target": "villager_1"}'),
                "night_detective": ('{"investigate": "<agent_id>"}', '{"investigate": "mafia_don"}'),
                "night_doctor": ('{"protect": "<agent_id>"}', '{"protect": "detective"}'),
                "day_vote": ('{"vote_for": "<agent_id>|null"}', '{"vote_for": "mafia_don"}'),
            }
            schema, example = _action_schemas.get(
                phase, ('{"target": "<agent_id>"}', '{"target": "player_1"}')
            )
            return AgentGameContext(
                instructions=["Only the authoritative game view matters."],
                state_lines=common_state,
                response_schema=schema,
                response_example=example,
            )

        discussion_hint = (
            "Use the mafia channel for secret coordination."
            if phase == "night_mafia_discussion"
            else "Speak publicly to persuade, accuse, defend, or claim roles if useful."
        )
        return AgentGameContext(
            instructions=[
                "This is a discussion turn. Respond with normal in-character dialogue only.",
                "Do not return JSON unless the authoritative view says this is an action phase.",
                discussion_hint,
            ],
            state_lines=common_state,
        )

    def is_terminal(self, state: MafiaState) -> bool:
        return state.phase == "complete" or state.winner is not None

    def outcome(self, state: MafiaState) -> GameOutcome | None:
        if not self.is_terminal(state) or state.winner is None:
            return None
        losers = [player_id for player_id in state.players if player_id not in state.winning_players]
        return GameOutcome(
            status="win",
            winners=list(state.winning_players),
            losers=losers,
            summary=f"{state.winner.title()} wins Mafia.",
        )

    def parse_action_payload(self, payload: dict[str, Any]) -> GameAction | None:
        for phase, field_name in _ACTION_SCHEMA_TARGET.items():
            if field_name in payload:
                value = payload[field_name]
                if value is None and phase == "day_vote":
                    return GameAction(action_type=phase, payload={field_name: None})
                if isinstance(value, str):
                    return GameAction(action_type=phase, payload={field_name: value})
        return None

    def parse_action_text(self, text: str) -> GameAction | None:
        return None

    def handle_actor_forfeit(self, state: MafiaState, actor_id: str) -> ApplyResult | None:
        """Skip the actor's current turn gracefully without ending the session.

        Called by the engine when an actor's moderation retries are exhausted.
        For multiplayer Mafia, a single player's forfeit should not end the game —
        their turn is simply skipped.
        """
        if self.is_terminal(state) or actor_id != self._current_actor(state):
            return None

        phase = state.phase

        if phase == "night_mafia_discussion":
            next_state = state.model_copy(deep=True)
            next_state.turn_index += 1
            next_state.discussion_index += 1
            if next_state.discussion_index >= len(next_state.discussion_order):
                next_state.phase = "night_mafia_vote"
                next_state.current_vote_order = self._living_mafia(next_state)
                next_state.vote_index = 0
            return ApplyResult(next_state=next_state, state_delta={"phase": next_state.phase})

        if phase == "night_mafia_vote":
            next_state = state.model_copy(deep=True)
            next_state.turn_index += 1
            next_state.vote_index += 1
            if next_state.vote_index < len(next_state.current_vote_order):
                return ApplyResult(next_state=next_state, state_delta={"vote_index": next_state.vote_index})
            # All mafia voted or forfeited — resolve
            if next_state.night_mafia_votes:
                next_state.night_kill_target = self._resolve_mafia_target(next_state)
            if next_state.detective_id in next_state.alive_players:
                next_state.phase = "night_detective"
                next_state.current_vote_order = [next_state.detective_id]
                next_state.vote_index = 0
                return ApplyResult(next_state=next_state, state_delta={"phase": next_state.phase})
            if next_state.doctor_id in next_state.alive_players:
                next_state.phase = "night_doctor"
                next_state.current_vote_order = [next_state.doctor_id]
                next_state.vote_index = 0
                return ApplyResult(next_state=next_state, state_delta={"phase": next_state.phase})
            return self._resolve_night(next_state)

        if phase == "night_detective":
            next_state = state.model_copy(deep=True)
            next_state.turn_index += 1
            if next_state.doctor_id in next_state.alive_players:
                next_state.phase = "night_doctor"
                next_state.current_vote_order = [next_state.doctor_id]
                next_state.vote_index = 0
                return ApplyResult(next_state=next_state, state_delta={"phase": next_state.phase})
            return self._resolve_night(next_state)

        if phase == "night_doctor":
            next_state = state.model_copy(deep=True)
            next_state.turn_index += 1
            return self._resolve_night(next_state)

        if phase == "day_discussion":
            next_state = state.model_copy(deep=True)
            next_state.turn_index += 1
            next_state.discussion_index += 1
            if next_state.discussion_index >= len(next_state.discussion_order):
                next_state.phase = "day_vote"
                next_state.current_vote_order = list(next_state.alive_players)
                next_state.vote_index = 0
            return ApplyResult(next_state=next_state, state_delta={"phase": next_state.phase})

        if phase == "day_vote":
            # Delegate to normal day vote path with a null (abstain) vote
            return self._apply_day_vote(
                state, actor_id, GameAction(action_type="day_vote", payload={"vote_for": None})
            )

        return None

    def _apply_mafia_vote(
        self,
        state: MafiaState,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult:
        target = action.payload["target"]
        next_state = state.model_copy(deep=True)
        next_state.night_mafia_votes[actor_id] = target
        next_state.vote_index += 1
        next_state.turn_index += 1

        if next_state.vote_index < len(next_state.current_vote_order):
            return ApplyResult(
                next_state=next_state,
                state_delta={"night_mafia_votes": dict(next_state.night_mafia_votes)},
            )

        next_state.night_kill_target = self._resolve_mafia_target(next_state)
        if next_state.detective_id in next_state.alive_players:
            next_state.phase = "night_detective"
            next_state.current_vote_order = [next_state.detective_id]
            next_state.vote_index = 0
        elif next_state.doctor_id in next_state.alive_players:
            next_state.phase = "night_doctor"
            next_state.current_vote_order = [next_state.doctor_id]
            next_state.vote_index = 0
        else:
            return self._resolve_night(next_state)
        return ApplyResult(
            next_state=next_state,
            state_delta={
                "phase": next_state.phase,
                "night_kill_target": next_state.night_kill_target,
            },
        )

    def _apply_detective_action(
        self,
        state: MafiaState,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult:
        target = action.payload["investigate"]
        alignment = "mafia" if state.roles[target] == "mafia" else "town"
        next_state = state.model_copy(deep=True)
        next_state.turn_index += 1
        next_state.detective_results.append(
            {
                "round_number": str(state.round_number),
                "target_id": target,
                "target_name": state.player_names[target],
                "alignment": alignment,
            }
        )
        if next_state.doctor_id in next_state.alive_players:
            next_state.phase = "night_doctor"
            next_state.current_vote_order = [next_state.doctor_id]
            next_state.vote_index = 0
            return ApplyResult(
                next_state=next_state,
                state_delta={"phase": next_state.phase},
                private_events=[
                    {
                        "recipient_id": actor_id,
                        "text": f"Investigation result: {state.player_names[target]} is {alignment}.",
                    }
                ],
            )
        result = self._resolve_night(next_state)
        result.private_events.append(
            {
                "recipient_id": actor_id,
                "text": f"Investigation result: {state.player_names[target]} is {alignment}.",
            }
        )
        return result

    def _apply_doctor_action(
        self,
        state: MafiaState,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult:
        target = action.payload["protect"]
        next_state = state.model_copy(deep=True)
        next_state.turn_index += 1
        next_state.doctor_history.append(target)
        return self._resolve_night(next_state)

    def _apply_day_vote(
        self,
        state: MafiaState,
        actor_id: str,
        action: GameAction,
    ) -> ApplyResult:
        vote_for = action.payload["vote_for"]
        next_state = state.model_copy(deep=True)
        next_state.day_votes[actor_id] = vote_for
        next_state.vote_index += 1
        next_state.turn_index += 1

        if next_state.vote_index < len(next_state.current_vote_order):
            return ApplyResult(
                next_state=next_state,
                state_delta={"day_votes": dict(next_state.day_votes)},
            )

        public_events: list[dict[str, Any]] = []
        eliminated: str | None = None
        tally = Counter(vote for vote in next_state.day_votes.values() if vote is not None)
        majority = len(next_state.alive_players) // 2 + 1
        if tally:
            top_target, top_count = tally.most_common(1)[0]
            if top_count >= majority:
                eliminated = top_target

        if eliminated is not None:
            self._eliminate_player(next_state, eliminated)
            role = next_state.roles[eliminated]
            public_events.append(
                {
                    "text": f"Day {state.round_number} vote result: {next_state.player_names[eliminated]} was eliminated and revealed as {role}.",
                }
            )
            next_state.last_public_summary = {
                "phase": "day_vote",
                "eliminated_id": eliminated,
                "eliminated_name": next_state.player_names[eliminated],
                "role": role,
            }
        else:
            public_events.append(
                {"text": f"Day {state.round_number} vote result: no majority, no one was eliminated."}
            )
            next_state.last_public_summary = {
                "phase": "day_vote",
                "eliminated_id": None,
            }

        next_state.round_log.append({
            "round": state.round_number,
            "event_type": "day_result",
            "eliminated_id": eliminated,
            "eliminated_name": next_state.player_names[eliminated] if eliminated else None,
            "role": next_state.roles[eliminated] if eliminated else None,
            "no_majority": eliminated is None,
        })

        winner = self._winning_faction(next_state)
        if winner is not None:
            self._apply_winner(next_state, winner)
        else:
            next_state.round_number += 1
            next_state.phase = "night_mafia_discussion"
            next_state.discussion_order = self._living_mafia(next_state)
            next_state.discussion_index = 0
            next_state.current_vote_order = list(next_state.discussion_order)
            next_state.vote_index = 0
            next_state.night_mafia_votes = {}
            next_state.night_kill_target = None
            next_state.day_votes = {}

        return ApplyResult(
            next_state=next_state,
            public_events=public_events,
            state_delta={
                "phase": next_state.phase,
                "day_votes": dict(next_state.day_votes),
                "winner": next_state.winner,
            },
        )

    def _resolve_night(self, state: MafiaState) -> ApplyResult:
        next_state = state.model_copy(deep=True)
        protected = next_state.doctor_history[-1] if next_state.doctor_history else None
        killed = None
        if next_state.night_kill_target is not None and next_state.night_kill_target != protected:
            killed = next_state.night_kill_target
            self._eliminate_player(next_state, killed)

        public_text: str
        if killed is None and next_state.night_kill_target is not None and protected == next_state.night_kill_target:
            public_text = f"Night {state.round_number} result: no one died."
        elif killed is not None:
            role = next_state.roles[killed]
            public_text = f"Night {state.round_number} result: {next_state.player_names[killed]} died and was revealed as {role}."
        else:
            public_text = f"Night {state.round_number} result: no one died."

        next_state.last_public_summary = {
            "phase": "night",
            "round_number": state.round_number,
            "killed_id": killed,
            "killed_name": next_state.player_names[killed] if killed else None,
            "saved": killed is None and next_state.night_kill_target is not None and protected == next_state.night_kill_target,
        }
        next_state.round_log.append({
            "round": state.round_number,
            "event_type": "night_result",
            "killed_id": killed,
            "killed_name": next_state.player_names[killed] if killed else None,
            "role": next_state.roles[killed] if killed else None,
            "saved": next_state.last_public_summary["saved"],
        })
        next_state.night_mafia_votes = {}
        next_state.night_kill_target = None
        next_state.day_votes = {}
        next_state.vote_index = 0

        winner = self._winning_faction(next_state)
        if winner is not None:
            self._apply_winner(next_state, winner)
        else:
            next_state.phase = "day_discussion"
            next_state.discussion_order = list(next_state.alive_players)
            next_state.discussion_index = 0
            next_state.current_vote_order = list(next_state.alive_players)

        return ApplyResult(
            next_state=next_state,
            public_events=[{"text": public_text}],
            state_delta={
                "phase": next_state.phase,
                "killed": killed,
                "winner": next_state.winner,
            },
        )

    def _build_journal_entries(self, state: MafiaState, viewer_id: str) -> list[JournalEntry]:
        entries: list[JournalEntry] = []
        for entry in state.round_log:
            if entry["event_type"] == "night_result":
                details: dict[str, str] = {}
                if entry.get("killed_name"):
                    details["victim"] = entry["killed_name"]
                    if entry.get("role"):
                        details["role"] = entry["role"]
                if entry.get("saved"):
                    result = "saved"
                elif entry.get("killed_id"):
                    result = "killed"
                else:
                    result = "no_kill"
                entries.append(JournalEntry(
                    turn=entry["round"],
                    actor_id="night",
                    action_type="night_result",
                    details=details,
                    result=result,
                ))
            elif entry["event_type"] == "day_result":
                details = {}
                if entry.get("eliminated_name"):
                    details["eliminated"] = entry["eliminated_name"]
                    if entry.get("role"):
                        details["role"] = entry["role"]
                result = "no_majority" if entry.get("no_majority") else "eliminated"
                entries.append(JournalEntry(
                    turn=entry["round"],
                    actor_id="day",
                    action_type="day_vote",
                    details=details,
                    result=result,
                ))
        if viewer_id == state.detective_id:
            for dr in state.detective_results:
                entries.append(JournalEntry(
                    turn=int(dr["round_number"]),
                    actor_id=viewer_id,
                    action_type="investigation",
                    details={"target": dr["target_name"], "alignment": dr["alignment"]},
                ))
        return entries

    def _legal_targets_for_phase(self, state: MafiaState, actor_id: str) -> list[str]:
        living_others = [player_id for player_id in state.alive_players if player_id != actor_id]
        if state.phase == "night_mafia_vote":
            return [player_id for player_id in living_others if state.roles[player_id] != "mafia"]
        if state.phase == "night_detective":
            return list(living_others)
        if state.phase == "night_doctor":
            if state.doctor_history:
                last_target = state.doctor_history[-1]
                return [player_id for player_id in state.alive_players if player_id != last_target]
            return list(state.alive_players)
        if state.phase == "day_vote":
            return list(living_others)
        return []

    def _current_actor(self, state: MafiaState) -> str | None:
        if state.phase in _DISCUSSION_PHASES:
            if 0 <= state.discussion_index < len(state.discussion_order):
                return state.discussion_order[state.discussion_index]
            return None
        if state.phase in _ACTION_SCHEMA_TARGET:
            if 0 <= state.vote_index < len(state.current_vote_order):
                return state.current_vote_order[state.vote_index]
        return None

    def _living_mafia(self, state: MafiaState) -> list[str]:
        return [player_id for player_id in state.mafia_order if player_id in state.alive_players]

    def _resolve_mafia_target(self, state: MafiaState) -> str:
        tally = Counter(state.night_mafia_votes.values())
        top_count = max(tally.values())
        contenders = {target for target, count in tally.items() if count == top_count}
        don_id = state.mafia_order[0]
        if don_id in state.night_mafia_votes and state.night_mafia_votes[don_id] in contenders:
            return state.night_mafia_votes[don_id]
        for mafia_id in state.mafia_order:
            choice = state.night_mafia_votes.get(mafia_id)
            if choice in contenders:
                return choice
        return next(iter(contenders))

    def _eliminate_player(self, state: MafiaState, player_id: str) -> None:
        if player_id in state.alive_players:
            state.alive_players.remove(player_id)
        if player_id not in state.eliminated:
            state.eliminated.append(player_id)
        state.revealed_roles[player_id] = state.roles[player_id]

    def _winning_faction(self, state: MafiaState) -> str | None:
        living_mafia = len(self._living_mafia(state))
        living_town = len(state.alive_players) - living_mafia
        if living_mafia == 0:
            return "town"
        if living_mafia >= living_town:
            return "mafia"
        return None

    def _apply_winner(self, state: MafiaState, winner: str) -> None:
        state.phase = "complete"
        state.winner = winner
        if winner == "mafia":
            state.winning_players = self._living_mafia(state)
        else:
            state.winning_players = [
                player_id for player_id in state.alive_players if state.roles[player_id] != "mafia"
            ]
