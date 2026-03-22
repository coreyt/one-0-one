"""Tests for ChannelRouter visibility rules."""

from datetime import UTC, datetime

import pytest

from src.channels.router import ChannelRouter
from src.games.contracts import AgentGameContext
from src.session.config import (
    AgentConfig,
    ChannelConfig,
    GameConfig,
    HITLConfig,
    OrchestratorConfig,
    SessionConfig,
    TranscriptConfig,
)


class _GameRuntimeStub:
    """Minimal duck-typed stub for GameRuntime used in router unit tests."""

    def __init__(self, contexts: dict[tuple, AgentGameContext]) -> None:
        self._contexts = contexts

    def render_agent_context(
        self, viewer_id: str, role: str, *, game_config=None
    ) -> AgentGameContext:
        return self._contexts.get((viewer_id, role), AgentGameContext())
from src.session.events import (
    GameStateEvent,
    MessageEvent,
    MonologueEvent,
    RuleViolationEvent,
    TurnEvent,
)
from src.session.state import AgentState, GameState, SessionState

NOW = datetime.now(UTC)


def _make_config(agents: list[dict], channels: list[dict] = None) -> SessionConfig:
    return SessionConfig.model_validate({
        "title": "Test",
        "description": "Test",
        "type": "social",
        "setting": "social",
        "topic": "Test topic",
        "agents": agents,
        "channels": channels or [],
    })


def _make_state(config: SessionConfig, events=None) -> SessionState:
    return SessionState(
        session_id="s1",
        turn_number=1,
        game_state=GameState(),
        events=events or [],
        agents={a.id: AgentState(config=a) for a in config.agents},
    )


def _msg(channel: str, agent_id: str = "a1", agent_name: str = "Nova",
         text: str = "hi", recipient_id: str = None) -> MessageEvent:
    return MessageEvent(
        timestamp=NOW, turn_number=1, session_id="s1",
        agent_id=agent_id, agent_name=agent_name,
        model="m", channel_id=channel, text=text,
        recipient_id=recipient_id,
    )


class TestPublicVisibility:
    def test_public_visible_to_all(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        event = _msg("public", agent_id="a1")
        state = _make_state(config, events=[event])

        ctx_a1 = router.build_context("a1", state)
        ctx_a2 = router.build_context("a2", state)

        # System prompt is always first; messages follow
        def has_message(ctx, text):
            return any(m.get("content", "").endswith(text) for m in ctx[1:])

        assert has_message(ctx_a1, "hi")
        assert has_message(ctx_a2, "hi")


class TestTeamVisibility:
    def _team_config(self):
        return _make_config(
            agents=[
                {"id": "a1", "name": "Nova", "provider": "p", "model": "m",
                 "role": "r", "team": "team_red"},
                {"id": "a2", "name": "Rex", "provider": "p", "model": "m",
                 "role": "r", "team": "team_red"},
                {"id": "a3", "name": "Sage", "provider": "p", "model": "m", "role": "r"},
            ],
            channels=[
                {"id": "team_red", "type": "team", "members": ["a1", "a2"]},
            ],
        )

    def test_team_message_visible_to_members(self):
        config = self._team_config()
        router = ChannelRouter(config)
        event = _msg("team_red", agent_id="a1", agent_name="Nova")
        state = _make_state(config, events=[event])

        ctx_a2 = router.build_context("a2", state)
        ctx_a3 = router.build_context("a3", state)

        def has_text(ctx, text):
            return any(text in m.get("content", "") for m in ctx[1:])

        assert has_text(ctx_a2, "hi")      # teammate
        assert not has_text(ctx_a3, "hi")  # not on team


class TestPrivateVisibility:
    def test_private_visible_to_sender_and_recipient(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
            {"id": "a3", "name": "Sage", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        event = _msg(
            "private_a1_a2", agent_id="a1", agent_name="Nova",
            text="secret", recipient_id="a2"
        )
        state = _make_state(config, events=[event])

        def has_text(ctx, text):
            return any(text in m.get("content", "") for m in ctx[1:])

        assert has_text(router.build_context("a1", state), "secret")  # sender
        assert has_text(router.build_context("a2", state), "secret")  # recipient
        assert not has_text(router.build_context("a3", state), "secret")  # third party


class TestMonologueExclusion:
    def test_monologue_never_in_context(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        mono = MonologueEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            agent_id="a1", agent_name="Nova", text="INTERNAL THOUGHT",
        )
        state = _make_state(config, events=[mono])

        def has_text(ctx, text):
            return any(text in m.get("content", "") for m in ctx)

        # Neither the speaker nor any other agent should see the monologue
        assert not has_text(router.build_context("a1", state), "INTERNAL THOUGHT")
        assert not has_text(router.build_context("a2", state), "INTERNAL THOUGHT")


class TestSystemMessages:
    def test_game_state_visible_to_all(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        gs = GameStateEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            updates={"score": 5}, full_state={"score": 5},
        )
        state = _make_state(config, events=[gs])

        ctx_a1 = router.build_context("a1", state)
        ctx_a2 = router.build_context("a2", state)

        def has_system(ctx, text):
            return any(
                m["role"] == "system" and text in m.get("content", "")
                for m in ctx[1:]
            )

        assert has_system(ctx_a1, "Game state update")
        assert has_system(ctx_a2, "Game state update")

    def test_rule_violation_only_for_violating_agent(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
            {"id": "a2", "name": "Rex", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        rv = RuleViolationEvent(
            timestamp=NOW, turn_number=1, session_id="s1",
            agent_id="a1", rule="Yes/No only", violation_text="It depends...",
        )
        state = _make_state(config, events=[rv])

        def has_violation(ctx):
            return any("Rule violation" in m.get("content", "") for m in ctx[1:])

        assert has_violation(router.build_context("a1", state))      # violator sees it
        assert not has_violation(router.build_context("a2", state))  # others don't


class TestSystemPrompt:
    def test_system_prompt_contains_topic(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "researcher"},
        ])
        router = ChannelRouter(config)
        state = _make_state(config)
        ctx = router.build_context("a1", state)

        system = ctx[0]
        assert system["role"] == "system"
        assert "Test topic" in system["content"]

    def test_system_prompt_contains_channel_instructions(self):
        config = _make_config([
            {"id": "a1", "name": "Nova", "provider": "p", "model": "m", "role": "r"},
        ])
        router = ChannelRouter(config)
        state = _make_state(config)
        ctx = router.build_context("a1", state)

        system = ctx[0]["content"]
        assert "<thinking>" in system
        assert "<private" in system
        assert "<team>" in system

    def test_plugin_game_system_prompt_prioritizes_authoritative_state(self):
        config = SessionConfig.model_validate({
            "title": "Connect Four",
            "description": "Test",
            "type": "games",
            "setting": "game",
            "topic": "Play Connect Four.",
            "agents": [
                {"id": "player_red", "name": "Red", "provider": "p", "model": "m", "role": "player"},
            ],
            "game": GameConfig(plugin="connect_four", name="Connect Four").model_dump(),
        })
        router = ChannelRouter(config)
        state = _make_state(config)

        system = router.build_context("player_red", state)[0]["content"]
        assert "authoritative game view" in system.lower()
        assert "focus on gameplay" in system.lower()
        assert "cooperative, fictional storytelling game" not in system.lower()

    def test_connect_four_authoritative_view_uses_plain_board_without_border(self):
        config = SessionConfig.model_validate({
            "title": "Connect Four",
            "description": "Test",
            "type": "games",
            "setting": "game",
            "topic": "Play Connect Four.",
            "agents": [
                {"id": "player_red", "name": "Red", "provider": "p", "model": "m", "role": "player"},
                {"id": "player_black", "name": "Black", "provider": "p", "model": "m", "role": "player"},
            ],
            "game": GameConfig(plugin="connect_four", name="Connect Four").model_dump(),
        })
        router = ChannelRouter(config)
        router.game_runtime = _GameRuntimeStub({
            ("player_red", "player"): AgentGameContext(
                state_lines=[
                    "active_player=player_red",
                    "board:",
                    "  1 2 3 4 5 6 7",
                    ". . . . . . .",
                ],
                response_schema='{"column": <integer 1-7>}',
                response_example='{"column": 4}',
            ),
        })
        state = _make_state(config)

        system = router.build_context("player_red", state)[1]["content"]
        assert "board:" in system
        assert ". . . . . . ." in system
        assert "┌" not in system
        assert "└" not in system

    def test_connect_four_player_context_includes_structured_move_contract(self):
        config = SessionConfig.model_validate({
            "title": "Connect Four",
            "description": "Test",
            "type": "games",
            "setting": "game",
            "topic": "Play Connect Four.",
            "agents": [
                {"id": "player_red", "name": "Red", "provider": "p", "model": "m", "role": "player"},
                {"id": "player_black", "name": "Black", "provider": "p", "model": "m", "role": "player"},
            ],
            "game": GameConfig(plugin="connect_four", name="Connect Four").model_dump(),
        })
        router = ChannelRouter(config)
        router.game_runtime = _GameRuntimeStub({
            ("player_red", "player"): AgentGameContext(
                state_lines=["active_player=player_red"],
                response_schema='{"column": <integer 1-7>}',
                response_example='{"column": 4}',
            ),
        })
        state = _make_state(config)

        system = router.build_context("player_red", state)[1]["content"]
        assert 'response_schema={"column": <integer 1-7>}' in system
        assert 'response_example={"column": 4}' in system

    def test_connect_four_referee_context_is_presentation_only(self):
        config = SessionConfig.model_validate({
            "title": "Connect Four",
            "description": "Test",
            "type": "games",
            "setting": "game",
            "topic": "Play Connect Four.",
            "agents": [
                {"id": "referee", "name": "Referee", "provider": "p", "model": "m", "role": "moderator"},
                {"id": "player_red", "name": "Red", "provider": "p", "model": "m", "role": "player"},
                {"id": "player_black", "name": "Black", "provider": "p", "model": "m", "role": "player"},
            ],
            "game": GameConfig(plugin="connect_four", name="Connect Four").model_dump(),
        })
        router = ChannelRouter(config)
        router.game_runtime = _GameRuntimeStub({
            ("referee", "moderator"): AgentGameContext(
                instructions=[
                    "role=presentation_referee",
                    "Do not choose moves, decide legality, or infer a winner from chat.",
                ],
                state_lines=["active_player=player_black"],
            ),
        })
        state = _make_state(config)

        system = router.build_context("referee", state)[1]["content"]
        assert "role=presentation_referee" in system
        assert "Do not choose moves" in system

    def test_battleship_player_context_gets_structured_shot_contract_without_hidden_enemy_state(self):
        config = SessionConfig.model_validate({
            "title": "Battleship",
            "description": "Test",
            "type": "games",
            "setting": "game",
            "topic": "Play Battleship.",
            "agents": [
                {"id": "admiral", "name": "Admiral", "provider": "p", "model": "m", "role": "moderator"},
                {"id": "captain_alpha", "name": "Alpha", "provider": "p", "model": "m", "role": "player"},
                {"id": "captain_beta", "name": "Beta", "provider": "p", "model": "m", "role": "player"},
            ],
            "game": GameConfig(plugin="battleship", name="Battleship").model_dump(),
        })
        router = ChannelRouter(config)
        router.game_runtime = _GameRuntimeStub({
            ("captain_alpha", "player"): AgentGameContext(
                state_lines=['<ship name="Carrier" size="5" status="intact"/>'],
                response_schema='{"coordinate": "B5"}',
                response_example='{"coordinate": "A10"}',
            ),
        })
        state = _make_state(config)

        system = router.build_context("captain_alpha", state)[1]["content"]
        assert 'response_schema={"coordinate": "B5"}' in system
        # Own fleet ships appear in the journal
        assert "Carrier" in system
        # Opponent ship coordinates must not be revealed
        assert "B1" not in system

    def test_battleship_moderator_context_includes_authoritative_state(self):
        config = SessionConfig.model_validate({
            "title": "Battleship",
            "description": "Test",
            "type": "games",
            "setting": "game",
            "topic": "Play Battleship.",
            "agents": [
                {"id": "admiral", "name": "Admiral", "provider": "p", "model": "m", "role": "moderator"},
                {"id": "captain_alpha", "name": "Alpha", "provider": "p", "model": "m", "role": "player"},
                {"id": "captain_beta", "name": "Beta", "provider": "p", "model": "m", "role": "player"},
            ],
            "game": GameConfig(plugin="battleship", name="Battleship").model_dump(),
        })
        router = ChannelRouter(config)
        router.game_runtime = _GameRuntimeStub({
            ("admiral", "moderator"): AgentGameContext(
                instructions=["role=presentation_referee"],
                state_lines=['authoritative_state={"attack_history": {"captain_alpha": {"B1": "hit"}}}'],
            ),
        })
        state = _make_state(config)

        system = router.build_context("admiral", state)[1]["content"]
        assert "role=presentation_referee" in system
        assert "authoritative_state=" in system
        assert '"B1"' in system

    def test_mafia_action_phase_includes_structured_contract(self):
        config = SessionConfig.model_validate({
            "title": "Mafia",
            "description": "Test",
            "type": "games",
            "setting": "game",
            "topic": "Play Mafia.",
            "agents": [
                {"id": "moderator", "name": "Narrator", "provider": "p", "model": "m", "role": "moderator"},
                {"id": "mafia_don", "name": "Don", "provider": "p", "model": "m", "role": "mafia", "team": "mafia"},
            ],
            "channels": [{"id": "mafia", "type": "team", "members": ["mafia_don"]}],
            "game": GameConfig(plugin="mafia", name="Mafia").model_dump(),
        })
        router = ChannelRouter(config)
        router.game_runtime = _GameRuntimeStub({
            ("mafia_don", "mafia"): AgentGameContext(
                instructions=["Only the authoritative game view matters."],
                state_lines=["phase=night_mafia_vote"],
                response_schema='{"target": "<agent_id>"}',
                response_example='{"target": "villager_1"}',
            ),
        })
        state = _make_state(config)

        system = router.build_context("mafia_don", state)[1]["content"]
        assert 'response_schema={"target": "<agent_id>"}' in system
        assert "return exactly one json object" in system.lower()

    def test_mafia_discussion_phase_uses_normal_dialogue(self):
        config = SessionConfig.model_validate({
            "title": "Mafia",
            "description": "Test",
            "type": "games",
            "setting": "game",
            "topic": "Play Mafia.",
            "agents": [
                {"id": "moderator", "name": "Narrator", "provider": "p", "model": "m", "role": "moderator"},
                {"id": "villager_1", "name": "Rosa", "provider": "p", "model": "m", "role": "villager"},
            ],
            "game": GameConfig(plugin="mafia", name="Mafia").model_dump(),
        })
        router = ChannelRouter(config)
        router.game_runtime = _GameRuntimeStub({
            ("villager_1", "villager"): AgentGameContext(
                instructions=[
                    "This is a discussion turn — respond with natural dialogue.",
                    "Do not return JSON.",
                    "Speak publicly.",
                ],
                state_lines=["phase=day_discussion"],
            ),
        })
        state = _make_state(config)

        system = router.build_context("villager_1", state)[1]["content"]
        assert "discussion turn" in system.lower()
        assert "do not return json" in system.lower()
