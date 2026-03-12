"""Tests for the Telephone game orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

import orchestrators.telephone as tel_orch
from src.orchestrators import OrchestratorInput, OrchestratorOutput
from src.session.config import (
    AgentConfig,
    ChannelConfig,
    GameConfig,
    SessionConfig,
)
from src.session.events import MessageEvent, TurnEvent
from src.session.state import GameState, SessionState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tel_config(num_players: int = 6) -> SessionConfig:
    players = []
    names = ["Echo", "Ripple", "Murmur", "Drift", "Fade", "Ghost"]
    for i in range(num_players):
        players.append(
            AgentConfig(
                id=f"player_{i + 1}",
                name=names[i] if i < len(names) else f"Player{i + 1}",
                provider="anthropic",
                model="m",
                role="player",
            )
        )
    return SessionConfig(
        title="Telephone Test",
        description="Test",
        type="games",
        setting="game",
        topic="Telephone test",
        max_turns=50,
        game=GameConfig(
            name="Telephone",
            win_condition="No winner — the fun is in the drift.",
        ),
        completion_signal="GAME COMPLETE",
        agents=[
            AgentConfig(
                id="operator",
                name="The Operator",
                provider="anthropic",
                model="m",
                role="moderator",
            ),
            *players,
        ],
        channels=[ChannelConfig(id="public", type="public")],
    )


def _tel_state(
    events: list | None = None,
    turn: int = 0,
    phase: str = "waiting_for_hitl",
    current_player_idx: int = 0,
    next_player: str | None = None,
    discussion_idx: int = 0,
) -> SessionState:
    custom: dict = {"phase": phase, "current_player_idx": current_player_idx}
    if next_player is not None:
        custom["next_player"] = next_player
    if phase == "discussion":
        custom["discussion_idx"] = discussion_idx
    return SessionState(
        session_id="test",
        turn_number=turn,
        game_state=GameState(custom=custom),
        events=events or [],
    )


def _tel_input(
    events: list | None = None,
    turn: int = 0,
    phase: str = "waiting_for_hitl",
    current_player_idx: int = 0,
    next_player: str | None = None,
    num_players: int = 6,
    discussion_idx: int = 0,
) -> OrchestratorInput:
    return OrchestratorInput(
        config=_tel_config(num_players=num_players),
        state=_tel_state(
            events=events,
            turn=turn,
            phase=phase,
            current_player_idx=current_player_idx,
            next_player=next_player,
            discussion_idx=discussion_idx,
        ),
    )


def _msg(
    agent_id: str,
    channel_id: str = "public",
    text: str = "hello",
) -> MessageEvent:
    return MessageEvent(
        session_id="test",
        turn_number=0,
        timestamp=datetime.now(UTC),
        agent_id=agent_id,
        agent_name=agent_id,
        model="test/model",
        channel_id=channel_id,
        recipient_id=None,
        text=text,
        is_parallel=False,
    )


def _turn_event(agent_ids: list[str], turn: int = 0) -> TurnEvent:
    return TurnEvent(
        session_id="test",
        turn_number=turn,
        timestamp=datetime.now(UTC),
        agent_ids=agent_ids,
        is_parallel=False,
    )


# ---------------------------------------------------------------------------
# Tests — HITL waiting phase
# ---------------------------------------------------------------------------


class TestTelephoneHITLWaiting:
    def test_no_hitl_pauses_engine(self):
        """No HITL message → wait_for_hitl=True, no agents scheduled."""
        result = tel_orch.orchestrate(_tel_input())
        assert result.wait_for_hitl is True
        assert result.next_agents == []
        assert result.session_end is False
        assert result.game_state_updates.get("phase") == "waiting_for_hitl"

    def test_hitl_triggers_operator(self):
        """HITL message arrives → operator introduces + whispers to Player 1."""
        events = [
            _msg("hitl", text="The quick brown fox jumps over the lazy dog"),
        ]
        result = tel_orch.orchestrate(
            _tel_input(events=events, turn=0, phase="waiting_for_hitl")
        )
        assert result.wait_for_hitl is False
        assert result.next_agents == ["operator"]
        assert result.game_state_updates.get("phase") == "chain"
        assert result.game_state_updates.get("current_player_idx") == 0
        assert result.game_state_updates.get("next_player") == "Echo"


# ---------------------------------------------------------------------------
# Tests — Chain phase (player-to-player routing)
# ---------------------------------------------------------------------------


class TestTelephoneChain:
    def test_operator_handoff_routes_to_player_1(self):
        """After operator speaks in chain phase → route to player_1."""
        events = [
            _msg("hitl", text="Starting phrase"),
            _msg("operator", channel_id="private_operator_player_1",
                 text="The starting phrase"),
        ]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=1, phase="chain",
                current_player_idx=0, next_player="Echo",
            )
        )
        assert result.next_agents == ["player_1"]

    def test_player_1_routes_to_player_2(self):
        """Player 1 responds → routes directly to player 2."""
        events = [
            _msg("operator", text="Handoff"),
            _msg("player_1", channel_id="private_player_1_player_2",
                 text="What I heard"),
        ]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=2, phase="chain",
                current_player_idx=0,
            )
        )
        assert result.next_agents == ["player_2"]
        assert "operator" not in result.next_agents
        assert result.game_state_updates.get("current_player_idx") == 1
        assert result.game_state_updates.get("next_player") == "Ripple"

    def test_mid_chain_player_routes_to_next(self):
        """Player 3 responds → routes to player 4."""
        events = [
            _msg("operator", text="Handoff"),
            _msg("player_3", text="My version"),
        ]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=4, phase="chain",
                current_player_idx=2,
            )
        )
        assert result.next_agents == ["player_4"]
        assert result.game_state_updates.get("current_player_idx") == 3
        assert result.game_state_updates.get("next_player") == "Drift"

    def test_last_player_triggers_discussion(self):
        """Last player (player_6) speaks → enters discussion phase."""
        events = [
            _msg("operator", text="Handoff"),
            _msg("player_6", text="The stray cat arched its back..."),
        ]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=8, phase="chain",
                current_player_idx=5,
            )
        )
        assert result.next_agents == ["operator"]
        assert result.game_state_updates.get("phase") == "discussion"
        assert result.game_state_updates.get("discussion_idx") == 0

    def test_next_player_name_in_game_state(self):
        """game_state_updates includes next_player name for each transition."""
        events = [
            _msg("operator", text="Handoff"),
            _msg("player_2", text="Heard version 2"),
        ]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=3, phase="chain",
                current_player_idx=1,
            )
        )
        assert result.game_state_updates.get("next_player") == "Murmur"


# ---------------------------------------------------------------------------
# Tests — Discussion phase
# ---------------------------------------------------------------------------


class TestTelephoneDiscussion:
    def test_discussion_starts_with_player_1(self):
        """discussion_idx=0 → player_1 discusses first.

        The Operator already spoke when transitioning from chain → discussion
        (revealing the original phrase). So discussion_idx=0 routes to player_1.
        """
        events = [_msg("operator", text="The original was...")]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=9, phase="discussion",
                discussion_idx=0,
            )
        )
        assert result.next_agents == ["player_1"]
        assert result.game_state_updates.get("discussion_idx") == 1

    def test_discussion_routes_players_sequentially(self):
        """Players discuss in order: idx=1 → player_2."""
        events = [_msg("player_1", text="I heard X and said Y")]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=10, phase="discussion",
                discussion_idx=1,
            )
        )
        assert result.next_agents == ["player_2"]
        assert result.game_state_updates.get("discussion_idx") == 2

    def test_discussion_player_4(self):
        """discussion_idx=3 → player_4 discusses."""
        events = [_msg("player_3", text="I heard X and said Y")]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=12, phase="discussion",
                discussion_idx=3,
            )
        )
        assert result.next_agents == ["player_4"]
        assert result.game_state_updates.get("discussion_idx") == 4

    def test_discussion_complete_triggers_reveal(self):
        """After all 6 players discuss → operator final reveal."""
        events = [_msg("player_6", text="I heard this from Fade")]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=15, phase="discussion",
                discussion_idx=6,  # all 6 players done
            )
        )
        assert result.next_agents == ["operator"]
        assert result.game_state_updates.get("phase") == "reveal"


# ---------------------------------------------------------------------------
# Tests — Reveal and termination
# ---------------------------------------------------------------------------


class TestTelephoneRevealAndTermination:
    def test_reveal_phase_routes_to_operator(self):
        """In reveal phase, operator delivers final analysis."""
        events = [_msg("player_6", text="Discussion complete")]
        result = tel_orch.orchestrate(
            _tel_input(events=events, turn=16, phase="reveal")
        )
        assert result.next_agents == ["operator"]
        assert result.game_state_updates.get("phase") == "reveal"

    def test_completion_signal_ends_session(self):
        """'GAME COMPLETE' in a public message → session_end."""
        events = [
            _msg("operator", text="The full chain reveals... GAME COMPLETE"),
        ]
        result = tel_orch.orchestrate(
            _tel_input(events=events, turn=17, phase="reveal")
        )
        assert result.session_end is True
        assert result.end_reason == "completion_signal"

    def test_max_turns_ends_session(self):
        """turn >= max_turns → session_end."""
        result = tel_orch.orchestrate(_tel_input(turn=50))
        assert result.session_end is True
        assert result.end_reason == "max_turns"


# ---------------------------------------------------------------------------
# Tests — Timeout guards
# ---------------------------------------------------------------------------


class TestTelephoneTimeouts:
    def test_operator_timeout_skips_to_player_1(self):
        """Operator times out during handoff → skip to player_1."""
        events = [
            _msg("hitl", text="Starting phrase"),
            _turn_event(["operator"], turn=1),
        ]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=2, phase="chain",
                current_player_idx=0,
            )
        )
        assert result.next_agents == ["player_1"]
        assert "operator" not in result.next_agents

    def test_player_timeout_advances_chain(self):
        """Player 2 times out → skip to player 3."""
        events = [
            _msg("operator", text="Handoff"),
            _msg("player_1", text="My version"),
            _turn_event(["player_2"], turn=3),
        ]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=4, phase="chain",
                current_player_idx=1,
            )
        )
        assert result.next_agents == ["player_3"]

    def test_last_player_timeout_triggers_discussion(self):
        """Player 6 times out → enter discussion phase."""
        events = [
            _msg("operator", text="Handoff"),
            _msg("player_5", text="My version"),
            _turn_event(["player_6"], turn=8),
        ]
        result = tel_orch.orchestrate(
            _tel_input(
                events=events, turn=9, phase="chain",
                current_player_idx=5,
            )
        )
        assert result.next_agents == ["operator"]
        assert result.game_state_updates.get("phase") == "discussion"


# ---------------------------------------------------------------------------
# Tests — Error conditions
# ---------------------------------------------------------------------------


class TestTelephoneErrors:
    def test_no_moderator_ends_session(self):
        """No moderator agent → session ends with error."""
        config = SessionConfig(
            title="Bad Config",
            description="Test",
            type="games",
            setting="game",
            topic="Test",
            game=GameConfig(name="Telephone", win_condition="None"),
            agents=[
                AgentConfig(id="p1", name="P1", provider="a", model="m", role="player"),
                AgentConfig(id="p2", name="P2", provider="a", model="m", role="player"),
            ],
            channels=[ChannelConfig(id="public", type="public")],
        )
        state = _tel_state()
        result = tel_orch.orchestrate(OrchestratorInput(config=config, state=state))
        assert result.session_end is True
        assert result.end_reason == "error"

    def test_no_players_ends_session(self):
        """No player agents → session ends with error."""
        config = SessionConfig(
            title="Bad Config",
            description="Test",
            type="games",
            setting="game",
            topic="Test",
            game=GameConfig(name="Telephone", win_condition="None"),
            agents=[
                AgentConfig(id="op", name="Op", provider="a", model="m", role="moderator"),
            ],
            channels=[ChannelConfig(id="public", type="public")],
        )
        state = _tel_state()
        result = tel_orch.orchestrate(OrchestratorInput(config=config, state=state))
        assert result.session_end is True
        assert result.end_reason == "error"


# ---------------------------------------------------------------------------
# Tests — Full simulation
# ---------------------------------------------------------------------------


class TestTelephoneFullSimulation:
    def test_full_chain_simulation(self):
        """Simulate complete game: HITL → operator → chain → discussion → reveal."""
        config = _tel_config()
        events: list = []
        turn = 0
        phase = "waiting_for_hitl"
        current_idx = 0
        next_player = None
        discussion_idx = 0
        sequence: list[str] = []

        # First: inject HITL phrase (this happens before any agent speaks)
        events.append(_msg("hitl", text="The quick brown fox"))

        for step in range(40):  # safety limit
            custom: dict = {
                "phase": phase,
                "current_player_idx": current_idx,
            }
            if next_player:
                custom["next_player"] = next_player
            if phase == "discussion":
                custom["discussion_idx"] = discussion_idx

            state = SessionState(
                session_id="test",
                turn_number=turn,
                game_state=GameState(custom=custom),
                events=events,
            )
            inp = OrchestratorInput(config=config, state=state)
            result = tel_orch.orchestrate(inp)

            if result.session_end:
                break

            if result.wait_for_hitl:
                # Should not happen since we injected HITL above
                raise AssertionError("Unexpected wait_for_hitl")

            agent_id = result.next_agents[0]
            sequence.append(agent_id)

            # Simulate message
            if agent_id == "operator" and result.game_state_updates.get("phase") == "reveal":
                events.append(_msg(agent_id, text="Full analysis... GAME COMPLETE"))
            elif agent_id == "operator":
                events.append(_msg(agent_id, text=f"Operator turn {step}"))
            elif phase == "chain":
                events.append(_msg(agent_id, text=f"{agent_id} whispers"))
            else:
                events.append(_msg(agent_id, text=f"{agent_id} discusses"))

            # Apply game_state_updates
            phase = result.game_state_updates.get("phase", phase)
            current_idx = result.game_state_updates.get("current_player_idx", current_idx)
            next_player = result.game_state_updates.get("next_player", next_player)
            discussion_idx = result.game_state_updates.get("discussion_idx", discussion_idx)
            turn += 1

        # Expected sequence:
        # operator (intro+whisper) → p1 → p2 → p3 → p4 → p5 → p6 →
        # operator (reveals) → p1 → p2 → p3 → p4 → p5 → p6 →
        # operator (final MSDM)
        assert sequence[0] == "operator"  # intro + whisper

        # Verify chain: all 6 players whispered in order
        chain_players = sequence[1:7]
        assert chain_players == [
            "player_1", "player_2", "player_3",
            "player_4", "player_5", "player_6",
        ], f"Chain was: {chain_players}"

        # Verify discussion: operator (reveals) + 6 players + operator (final)
        discussion_seq = sequence[7:]
        assert discussion_seq[0] == "operator"  # reveals original
        assert discussion_seq[1:7] == [
            "player_1", "player_2", "player_3",
            "player_4", "player_5", "player_6",
        ]
        assert discussion_seq[7] == "operator"  # final reveal

        # Verify no operator between chain players
        chain_segment = sequence[1:7]
        assert "operator" not in chain_segment, (
            f"Operator should NOT appear between chain players: {chain_segment}"
        )

    def test_wait_for_hitl_then_proceed(self):
        """Without HITL → wait. With HITL → proceed to operator."""
        # Step 1: No HITL → wait
        result1 = tel_orch.orchestrate(_tel_input())
        assert result1.wait_for_hitl is True
        assert result1.next_agents == []

        # Step 2: HITL arrives → operator
        events = [_msg("hitl", text="Test phrase")]
        result2 = tel_orch.orchestrate(
            _tel_input(events=events, phase="waiting_for_hitl")
        )
        assert result2.wait_for_hitl is False
        assert result2.next_agents == ["operator"]
        assert result2.game_state_updates.get("phase") == "chain"
