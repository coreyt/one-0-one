"""Tests for orchestrators — REQ-PERF-002, REQ-PERF-003, REQ-PERF-004, mafia."""

from __future__ import annotations

from src.orchestrators import OrchestratorInput, OrchestratorOutput
from src.orchestrators.basic import orchestrate
import orchestrators.mafia as mafia_orch
import orchestrators.poker as poker_orch
import orchestrators.turn_based as tb_orch
from src.session.config import AgentConfig, OrchestratorConfig, SessionConfig
from src.session.events import GameStateEvent, MessageEvent, TurnEvent
from src.session.state import GameState, SessionState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent(agent_id: str, team: str | None = None, role: str = "villager") -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id.replace("_", " ").title(),
        provider="anthropic",
        model="claude-sonnet-4-6",
        role=role,
        team=team,
    )


def _config(*agents: AgentConfig, max_turns: int | None = 100) -> SessionConfig:
    from src.session.config import ChannelConfig
    # Auto-create team channels for any agent that references a team
    teams = {a.team for a in agents if a.team}
    channels = [ChannelConfig(id=t, type="team", members=[a.id for a in agents if a.team == t]) for t in teams]
    return SessionConfig(
        title="Test",
        description="Test session",
        type="social",
        setting="test",
        topic="Testing",
        max_turns=max_turns,
        agents=list(agents),
        channels=channels,
    )


def _state(turn: int = 0, eliminated: list[str] | None = None) -> SessionState:
    return SessionState(
        session_id="test-session",
        turn_number=turn,
        game_state=GameState(eliminated=eliminated or []),
        events=[],
        agents={},
    )


def _input(config: SessionConfig, turn: int = 0, eliminated: list[str] | None = None) -> OrchestratorInput:
    return OrchestratorInput(config=config, state=_state(turn, eliminated))


# ---------------------------------------------------------------------------
# REQ-PERF-002: Skip eliminated agents
# ---------------------------------------------------------------------------

class TestSkipEliminatedAgents:
    def test_all_agents_active_by_default(self):
        # 3 no-team agents — DM-batched together (REQ-PERF-004)
        cfg = _config(_agent("a"), _agent("b"), _agent("c"))
        result = orchestrate(_input(cfg, turn=0))
        assert result.session_end is False
        assert set(result.next_agents) == {"a", "b", "c"}

    def test_eliminated_agent_is_skipped(self):
        cfg = _config(_agent("a"), _agent("b"), _agent("c"))
        # "a" eliminated — active [b,c], both no-team → batch together
        result = orchestrate(_input(cfg, turn=0, eliminated=["a"]))
        assert "a" not in result.next_agents
        assert set(result.next_agents) == {"b", "c"}

    def test_multiple_eliminated_skipped(self):
        cfg = _config(_agent("a"), _agent("b"), _agent("c"), _agent("d"))
        # a and b eliminated — active [c,d], both no-team → batch
        result = orchestrate(_input(cfg, turn=0, eliminated=["a", "b"]))
        assert "a" not in result.next_agents
        assert "b" not in result.next_agents
        assert "c" in result.next_agents

    def test_round_robin_wraps_over_active_only(self):
        # Use a team agent to prevent batching so we can test round-robin
        cfg = _config(
            _agent("mod", role="moderator"),   # team=None, solo context
            _agent("b", team="red"),
            _agent("c", team="blue"),
        )
        # b eliminated; active = [mod, c]; turn 0 → mod (solo), turn 1 → c (solo, diff team)
        r0 = orchestrate(_input(cfg, turn=0, eliminated=["b"]))
        r1 = orchestrate(_input(cfg, turn=1, eliminated=["b"]))
        r2 = orchestrate(_input(cfg, turn=2, eliminated=["b"]))
        assert r0.next_agents == ["mod"]
        assert r1.next_agents == ["c"]
        assert r2.next_agents == ["mod"]

    def test_all_agents_eliminated_ends_session(self):
        cfg = _config(_agent("a"), _agent("b"))
        result = orchestrate(_input(cfg, turn=0, eliminated=["a", "b"]))
        assert result.session_end is True

    def test_eliminated_not_in_batch(self):
        """Team batching must not include eliminated agents."""
        cfg = _config(
            _agent("don", team="mafia"),
            _agent("sal", team="mafia"),
            _agent("luca", team="mafia"),
        )
        # don eliminated — batch should only contain sal and luca
        result = orchestrate(_input(cfg, turn=0, eliminated=["don"]))
        assert "don" not in result.next_agents


# ---------------------------------------------------------------------------
# REQ-PERF-003: Parallel team chat
# ---------------------------------------------------------------------------

class TestTeamParallelization:
    def test_team_agents_batched(self):
        cfg = _config(
            _agent("don", team="mafia"),
            _agent("sal", team="mafia"),
            _agent("luca", team="mafia"),
            _agent("iris"),
        )
        result = orchestrate(_input(cfg, turn=0))
        # Turn 0 hits "don" (team=mafia) — should batch all 3 mafia
        assert set(result.next_agents) == {"don", "sal", "luca"}

    def test_advance_turns_equals_batch_size(self):
        cfg = _config(
            _agent("don", team="mafia"),
            _agent("sal", team="mafia"),
            _agent("iris"),
        )
        result = orchestrate(_input(cfg, turn=0))
        assert result.advance_turns == 2  # 2 mafia members

    def test_solo_agent_advance_turns_is_one(self):
        # iris and dante are both no-team — they batch, advance=2
        # Use a team agent as barrier to isolate iris
        cfg = _config(_agent("iris"), _agent("x", team="red"))
        result = orchestrate(_input(cfg, turn=0))
        assert result.next_agents == ["iris"]
        assert result.advance_turns == 1

    def test_no_cross_team_batching(self):
        """Agents from different teams must not be batched together."""
        cfg = _config(
            _agent("don", team="mafia"),
            _agent("sal", team="mafia"),
            _agent("red", team="red_team"),
        )
        result = orchestrate(_input(cfg, turn=0))
        assert "red" not in result.next_agents

    def test_team_batch_skips_to_correct_next_turn(self):
        """After a 2-agent batch (advance_turns=2), turn+2 picks the right agent."""
        cfg = _config(
            _agent("don", team="mafia"),
            _agent("sal", team="mafia"),
            _agent("iris"),
            _agent("dante"),
        )
        # Turn 0: batch [don, sal], advance_turns=2
        r0 = orchestrate(_input(cfg, turn=0))
        assert set(r0.next_agents) == {"don", "sal"}
        assert r0.advance_turns == 2
        # Next actual call would be at turn 0+2=2 → iris+dante (no-team batch)
        r2 = orchestrate(_input(cfg, turn=2))
        assert set(r2.next_agents) == {"iris", "dante"}
        assert r2.advance_turns == 2


# ---------------------------------------------------------------------------
# REQ-PERF-004: Parallel DM (consecutive no-team agents)
# ---------------------------------------------------------------------------

class TestDMParallelization:
    def test_consecutive_solo_agents_batched(self):
        """Two consecutive no-team agents between team barriers are batched."""
        cfg = _config(
            _agent("blocker_a", team="alpha"),
            _agent("detective"),
            _agent("doctor"),
            _agent("blocker_b", team="beta"),
        )
        # Turn 1 hits detective (no team) — batches with doctor, stops at blocker_b
        result = orchestrate(_input(cfg, turn=1))
        assert set(result.next_agents) == {"detective", "doctor"}
        assert result.advance_turns == 2

    def test_single_solo_agent_not_batched(self):
        """One no-team agent surrounded by different contexts stays solo."""
        cfg = _config(
            _agent("don", team="mafia"),
            _agent("detective"),
            _agent("sal", team="mafia"),
        )
        # Turn 1 = detective, surrounded by mafia — not batched with mafia
        result = orchestrate(_input(cfg, turn=1))
        assert result.next_agents == ["detective"]
        assert result.advance_turns == 1

    def test_three_consecutive_solo_agents_batched(self):
        cfg = _config(
            _agent("a"),
            _agent("b"),
            _agent("c"),
            _agent("d", team="red"),
        )
        result = orchestrate(_input(cfg, turn=0))
        assert set(result.next_agents) == {"a", "b", "c"}
        assert result.advance_turns == 3


# ---------------------------------------------------------------------------
# OrchestratorOutput.advance_turns default
# ---------------------------------------------------------------------------

class TestAdvanceTurnsDefault:
    def test_default_advance_turns_is_one(self):
        out = OrchestratorOutput(next_agents=["x"])
        assert out.advance_turns == 1

    def test_session_end_advance_turns_is_one(self):
        out = OrchestratorOutput(session_end=True, end_reason="max_turns")
        assert out.advance_turns == 1


# ---------------------------------------------------------------------------
# Mafia orchestrator — narrator interleaving
# ---------------------------------------------------------------------------

def _msg_event(agent_id: str, channel_id: str = "public", text: str = "hello") -> MessageEvent:
    from datetime import UTC, datetime
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


def _mafia_config() -> SessionConfig:
    from src.session.config import ChannelConfig, GameConfig
    return SessionConfig(
        title="Mafia Test",
        description="Test",
        type="games",
        setting="game",
        topic="Mafia test",
        max_turns=200,
        game=GameConfig(name="Mafia", win_condition="Town wins."),
        completion_signal="WINS!",
        agents=[
            AgentConfig(id="narrator", name="Narrator", provider="anthropic",
                        model="m", role="moderator"),
            AgentConfig(id="don", name="Don", provider="openai",
                        model="m", role="mafia", team="mafia"),
            AgentConfig(id="sal", name="Sal", provider="gemini",
                        model="m", role="mafia", team="mafia"),
            AgentConfig(id="iris", name="Iris", provider="anthropic",
                        model="m", role="detective"),
            AgentConfig(id="rosa", name="Rosa", provider="gemini",
                        model="m", role="villager"),
            AgentConfig(id="marco", name="Marco", provider="anthropic",
                        model="m", role="villager"),
        ],
        channels=[
            ChannelConfig(id="public", type="public"),
            ChannelConfig(id="mafia", type="team", members=["don", "sal"]),
        ],
    )


def _mafia_state(events: list | None = None, turn: int = 0,
                 eliminated: list[str] | None = None) -> SessionState:
    return SessionState(
        session_id="test",
        turn_number=turn,
        game_state=GameState(eliminated=eliminated or []),
        events=events or [],
    )


def _mafia_input(events: list | None = None, turn: int = 0,
                 eliminated: list[str] | None = None) -> OrchestratorInput:
    return OrchestratorInput(
        config=_mafia_config(),
        state=_mafia_state(events=events, turn=turn, eliminated=eliminated),
    )


class TestMafiaOrchestrator:
    def test_narrator_goes_first_with_no_public_messages(self):
        result = mafia_orch.orchestrate(_mafia_input())
        assert result.next_agents == ["narrator"]
        assert result.advance_turns == 1

    def test_players_go_after_narrator_speaks(self):
        events = [_msg_event("narrator")]
        result = mafia_orch.orchestrate(_mafia_input(events=events, turn=1))
        assert "narrator" not in result.next_agents
        assert len(result.next_agents) >= 1

    def test_narrator_called_after_narrator_every_player_messages(self):
        N = mafia_orch.NARRATOR_EVERY
        # narrator spoke once, then N player messages → time for narrator again
        events = [_msg_event("narrator")] + [_msg_event("rosa") for _ in range(N)]
        result = mafia_orch.orchestrate(_mafia_input(events=events, turn=10))
        assert result.next_agents == ["narrator"]

    def test_narrator_not_called_before_threshold(self):
        # narrator spoke, then N-1 player messages → NOT narrator yet
        N = mafia_orch.NARRATOR_EVERY
        events = [_msg_event("narrator")] + [_msg_event("rosa") for _ in range(N - 1)]
        result = mafia_orch.orchestrate(_mafia_input(events=events, turn=10))
        assert "narrator" not in result.next_agents

    def test_narrator_excluded_from_player_pool(self):
        # narrator spoke, 1 player message so far → next is a player
        events = [_msg_event("narrator"), _msg_event("rosa")]
        result = mafia_orch.orchestrate(_mafia_input(events=events, turn=2))
        assert "narrator" not in result.next_agents

    def test_team_batching_preserved_for_players(self):
        # narrator spoke, then 0 player messages yet → narrator again (threshold)
        # narrator spoke, then 1 message → check if mafia team is batched
        events = [_msg_event("narrator"), _msg_event("iris")]
        result = mafia_orch.orchestrate(_mafia_input(events=events, turn=2))
        # If round-robin hits a mafia agent, both mafia should be batched
        if "don" in result.next_agents or "sal" in result.next_agents:
            assert set(result.next_agents) == {"don", "sal"}

    def test_eliminated_players_skipped(self):
        events = [_msg_event("narrator"), _msg_event("iris")]
        result = mafia_orch.orchestrate(
            _mafia_input(events=events, turn=2, eliminated=["don", "sal"])
        )
        assert "don" not in result.next_agents
        assert "sal" not in result.next_agents

    def test_narrator_eliminated_no_crash(self):
        # If narrator is eliminated the game should still route to players
        events = [_msg_event("iris")]
        result = mafia_orch.orchestrate(
            _mafia_input(events=events, turn=1, eliminated=["narrator"])
        )
        assert result.session_end is False
        assert "narrator" not in result.next_agents

    def test_narrator_not_retried_after_timeout(self):
        """If the last TURN was narrator-only but they produced no message,
        the next call should route to players, not retry the narrator."""
        from src.session.events import TurnEvent
        from datetime import UTC, datetime
        N = mafia_orch.NARRATOR_EVERY
        # Narrator spoke, then N player messages, then narrator TURN was emitted
        # but narrator timed out (no subsequent narrator MESSAGE)
        turn_event = TurnEvent(
            session_id="test",
            turn_number=10,
            timestamp=datetime.now(UTC),
            agent_ids=["narrator"],
            is_parallel=False,
        )
        events = (
            [_msg_event("narrator")]
            + [_msg_event("rosa") for _ in range(N)]
            + [turn_event]       # narrator attempted but no message followed
        )
        result = mafia_orch.orchestrate(_mafia_input(events=events, turn=11))
        # Should NOT retry narrator — should route to a player
        assert "narrator" not in result.next_agents

    def test_narrator_ratio_approximates_target(self):
        """Simulate 100 player messages and count narrator insertions."""
        cfg = _mafia_config()
        events: list = []
        turn = 0
        narrator_calls = 0
        player_calls = 0

        for _ in range(60):  # 60 orchestrator decisions
            inp = OrchestratorInput(
                config=cfg,
                state=_mafia_state(events=events, turn=turn),
            )
            result = mafia_orch.orchestrate(inp)
            if result.session_end:
                break

            # Simulate the agents speaking (add to events)
            for agent_id in result.next_agents:
                agent = next(a for a in cfg.agents if a.id == agent_id)
                channel = "mafia" if agent.team == "mafia" else "public"
                events.append(_msg_event(agent_id, channel_id=channel))
                if channel == "public":
                    if agent_id == "narrator":
                        narrator_calls += 1
                    else:
                        player_calls += 1

            turn += result.advance_turns

        total = narrator_calls + player_calls
        if total > 0:
            ratio = narrator_calls / total
            # Mafia private messages don't count toward the public threshold so
            # the observed public ratio is ~1/(NARRATOR_EVERY*k+1) where k>1.
            # Accept a generous range; the key property is narrator is regular,
            # non-zero, and not dominant.
            assert 0.08 <= ratio <= 0.35, f"narrator ratio {ratio:.2%} out of range"

    def test_max_turns_ends_session(self):
        cfg = _mafia_config()
        # Create a config with max_turns=5 and turn=5
        from src.session.config import ChannelConfig, GameConfig
        cfg2 = SessionConfig(
            title="T", description="T", type="games", setting="game",
            topic="T", max_turns=5, game=GameConfig(name="M", win_condition="x"),
            agents=cfg.agents, channels=cfg.channels,
        )
        inp = OrchestratorInput(config=cfg2, state=_mafia_state(turn=5))
        result = mafia_orch.orchestrate(inp)
        assert result.session_end is True
        assert result.end_reason == "max_turns"

    def test_completion_signal_ends_session(self):
        cfg = _mafia_config()
        # Inject a message containing the completion signal
        events = [_msg_event("narrator", text="The town WINS! The Mafia is gone.")]
        inp = OrchestratorInput(config=cfg, state=_mafia_state(events=events, turn=1))
        result = mafia_orch.orchestrate(inp)
        assert result.session_end is True
        assert result.end_reason == "completion_signal"

    def test_narrator_called_immediately_after_elimination(self):
        """After any elimination GAME_STATE event, narrator goes next regardless of counter."""
        from datetime import UTC, datetime
        cfg = _mafia_config()
        # Narrator spoke, then 1 player spoke, then an elimination happened
        # (only 1 player message, well below NARRATOR_EVERY=4)
        narrator_msg = _msg_event("narrator", channel_id="public")
        player_msg = _msg_event("iris", channel_id="public")
        elim_event = GameStateEvent(
            timestamp=datetime.now(UTC),
            turn_number=1,
            session_id="test",
            updates={"newly_eliminated": "sal"},
            full_state={"eliminated": ["sal"]},
        )
        events = [narrator_msg, player_msg, elim_event]
        inp = _mafia_input(events=events, turn=2, eliminated=["sal"])
        result = mafia_orch.orchestrate(inp)
        assert result.next_agents == ["narrator"]
        assert result.session_end is False

    def test_narrator_not_called_twice_for_same_elimination(self):
        """After narrator speaks post-elimination, the GAME_STATE event is behind
        the narrator's new message — no second immediate narrator call."""
        from datetime import UTC, datetime
        cfg = _mafia_config()
        # Older elimination event → narrator spoke to announce it → then 1 player
        elim_event = GameStateEvent(
            timestamp=datetime.now(UTC),
            turn_number=1,
            session_id="test",
            updates={"newly_eliminated": "sal"},
            full_state={"eliminated": ["sal"]},
        )
        narrator_announce = _msg_event("narrator", channel_id="public")
        one_player = _msg_event("iris", channel_id="public")
        events = [elim_event, narrator_announce, one_player]
        inp = _mafia_input(events=events, turn=3, eliminated=["sal"])
        result = mafia_orch.orchestrate(inp)
        # Only 1 player msg since narrator's announce → below NARRATOR_EVERY=4
        # Should route to players, not narrator again
        assert "narrator" not in result.next_agents


# ---------------------------------------------------------------------------
# Turn-based orchestrator
# ---------------------------------------------------------------------------

def _tb_config() -> SessionConfig:
    from src.session.config import ChannelConfig, GameConfig
    return SessionConfig(
        title="Turn-Based Test",
        description="Test",
        type="games",
        setting="game",
        topic="Connect Four test",
        max_turns=120,
        game=GameConfig(name="Connect Four", win_condition="Four in a row."),
        completion_signal="WINS!",
        agents=[
            AgentConfig(id="referee", name="Referee", provider="anthropic",
                        model="m", role="moderator"),
            AgentConfig(id="p1", name="Player One", provider="openai",
                        model="m", role="player"),
            AgentConfig(id="p2", name="Player Two", provider="gemini",
                        model="m", role="player"),
        ],
        channels=[
            ChannelConfig(id="public", type="public"),
        ],
    )


def _tb_state(events=None, turn=0, eliminated=None) -> SessionState:
    return SessionState(
        session_id="test",
        turn_number=turn,
        game_state=GameState(eliminated=eliminated or []),
        events=events or [],
    )


def _tb_input(events=None, turn=0, eliminated=None) -> OrchestratorInput:
    return OrchestratorInput(
        config=_tb_config(),
        state=_tb_state(events=events, turn=turn, eliminated=eliminated),
    )


class TestTurnBasedOrchestrator:

    def test_narrator_goes_first_with_no_messages(self):
        result = tb_orch.orchestrate(_tb_input())
        assert result.next_agents == ["referee"]
        assert result.session_end is False

    def test_narrator_fires_after_player_move(self):
        events = [_msg_event("p1", channel_id="public")]
        result = tb_orch.orchestrate(_tb_input(events=events, turn=1))
        assert result.next_agents == ["referee"]

    def test_p1_goes_after_narrator_opening(self):
        events = [_msg_event("referee", channel_id="public")]
        result = tb_orch.orchestrate(_tb_input(events=events, turn=1))
        assert result.next_agents == ["p1"]

    def test_strict_alternation_p1_p2(self):
        # referee → p1 → referee → p2 → referee → p1 ...
        from datetime import UTC, datetime
        events = []
        sequence = []
        turn = 0
        for _ in range(10):
            inp = OrchestratorInput(config=_tb_config(), state=_tb_state(events=events, turn=turn))
            result = tb_orch.orchestrate(inp)
            assert not result.session_end
            next_id = result.next_agents[0]
            sequence.append(next_id)
            events.append(_msg_event(next_id, channel_id="public"))
            turn += result.advance_turns

        # Expected: referee, p1, referee, p2, referee, p1, referee, p2, referee, p1
        assert sequence[0] == "referee"
        player_turns = [s for s in sequence if s != "referee"]
        assert player_turns == ["p1", "p2", "p1", "p2", "p1"]

    def test_narrator_timeout_guard_skips_to_player(self):
        """If narrator just timed out (TURN event present, no message), go to player."""
        from datetime import UTC, datetime
        # Referee last spoke, then a TURN event for referee only (timeout)
        events = [
            _msg_event("referee", channel_id="public"),   # referee opened
            _msg_event("p1", channel_id="public"),        # p1 played
            TurnEvent(
                timestamp=datetime.now(UTC), turn_number=2,
                session_id="test", agent_ids=["referee"], is_parallel=False,
            ),
        ]
        result = tb_orch.orchestrate(_tb_input(events=events, turn=2))
        assert "referee" not in result.next_agents
        assert result.next_agents[0] in ("p1", "p2")

    def test_eliminated_player_skipped(self):
        # p1 is eliminated — only p2 should play
        events = [_msg_event("referee", channel_id="public")]
        result = tb_orch.orchestrate(
            OrchestratorInput(
                config=_tb_config(),
                state=_tb_state(events=events, turn=1, eliminated=["p1"]),
            )
        )
        # With only one non-eliminated player, game ends
        assert result.session_end is True

    def test_both_players_eliminated_ends_session(self):
        result = tb_orch.orchestrate(
            OrchestratorInput(
                config=_tb_config(),
                state=_tb_state(eliminated=["p1", "p2"]),
            )
        )
        assert result.session_end is True
        assert result.end_reason == "win_condition"

    def test_max_turns_ends_session(self):
        inp = OrchestratorInput(
            config=_tb_config(),
            state=_tb_state(turn=120),
        )
        result = tb_orch.orchestrate(inp)
        assert result.session_end is True
        assert result.end_reason == "max_turns"

    def test_completion_signal_ends_session(self):
        events = [_msg_event("referee", text="Alex Mercer WINS! Four in a row!")]
        inp = OrchestratorInput(
            config=_tb_config(),
            state=_tb_state(events=events, turn=1),
        )
        result = tb_orch.orchestrate(inp)
        assert result.session_end is True
        assert result.end_reason == "completion_signal"

    def test_advance_turns_is_always_one(self):
        result = tb_orch.orchestrate(_tb_input())
        assert result.advance_turns == 1


# ---------------------------------------------------------------------------
# Poker orchestrator
# ---------------------------------------------------------------------------

def _poker_config() -> SessionConfig:
    from src.session.config import ChannelConfig, GameConfig
    return SessionConfig(
        title="Poker Test",
        description="Test",
        type="games",
        setting="game",
        topic="Texas Hold'em test",
        max_turns=300,
        game=GameConfig(name="Texas Hold'em", win_condition="Last player with chips wins."),
        completion_signal="WINS THE TOURNAMENT!",
        agents=[
            AgentConfig(id="dealer", name="The Dealer", provider="anthropic",
                        model="m", role="moderator"),
            AgentConfig(id="marcus", name="Marcus Webb", provider="openai",
                        model="m", role="player"),
            AgentConfig(id="lila", name="Lila Tran", provider="gemini",
                        model="m", role="player"),
            AgentConfig(id="rocky", name="Rocky Ortiz", provider="openai",
                        model="m", role="player"),
            AgentConfig(id="diana", name="Diana Chen", provider="gemini",
                        model="m", role="player"),
        ],
        channels=[
            ChannelConfig(id="public", type="public"),
        ],
    )


def _poker_state(events=None, turn=0, eliminated=None) -> SessionState:
    return SessionState(
        session_id="test",
        turn_number=turn,
        game_state=GameState(eliminated=eliminated or []),
        events=events or [],
    )


def _poker_input(events=None, turn=0, eliminated=None) -> OrchestratorInput:
    return OrchestratorInput(
        config=_poker_config(),
        state=_poker_state(events=events, turn=turn, eliminated=eliminated),
    )


class TestPokerOrchestrator:

    def test_dealer_opens_first_with_no_messages(self):
        result = poker_orch.orchestrate(_poker_input())
        assert result.next_agents == ["dealer"]
        assert result.session_end is False
        assert result.advance_turns == 1

    def test_dealer_fires_after_player_action(self):
        events = [_msg_event("marcus", channel_id="public", text="I raise to 150.")]
        result = poker_orch.orchestrate(_poker_input(events=events, turn=1))
        assert result.next_agents == ["dealer"]

    def test_dealer_fires_after_any_player(self):
        """Any of the four players speaking should trigger Dealer response."""
        for player_id in ("marcus", "lila", "rocky", "diana"):
            events = [_msg_event(player_id, channel_id="public")]
            result = poker_orch.orchestrate(_poker_input(events=events, turn=1))
            assert result.next_agents == ["dealer"], f"failed for {player_id}"

    def test_routes_to_addressed_player_by_name(self):
        """When Dealer ends message addressing a player, route to that player."""
        events = [
            _msg_event("marcus", channel_id="public", text="Call."),
            _msg_event("dealer", channel_id="public",
                       text="Marcus calls. Pot is 300. Your move, Lila."),
        ]
        result = poker_orch.orchestrate(_poker_input(events=events, turn=2))
        assert result.next_agents == ["lila"]

    def test_routes_to_second_player_by_name(self):
        events = [
            _msg_event("lila", channel_id="public", text="I fold."),
            _msg_event("dealer", channel_id="public",
                       text="Lila folds. Rocky, it's your bet."),
        ]
        result = poker_orch.orchestrate(_poker_input(events=events, turn=2))
        assert result.next_agents == ["rocky"]

    def test_name_match_is_case_insensitive(self):
        events = [
            _msg_event("diana", channel_id="public", text="Raise 200."),
            _msg_event("dealer", channel_id="public",
                       text="Diana raises. MARCUS, you're up."),
        ]
        result = poker_orch.orchestrate(_poker_input(events=events, turn=2))
        assert result.next_agents == ["marcus"]

    def test_falls_back_to_fewest_messages_when_no_name(self):
        """If Dealer message has no player name at end, use fewest-msgs fallback."""
        events = [
            _msg_event("dealer", channel_id="public",
                       text="Welcome to the table. Let the game begin."),
        ]
        # marcus has 0 messages — should be selected (first in config order, tiebreak)
        result = poker_orch.orchestrate(_poker_input(events=events, turn=1))
        assert result.next_agents[0] in ("marcus", "lila", "rocky", "diana")
        assert result.next_agents[0] == "marcus"  # first in config, tiebreak

    def test_eliminated_player_not_addressed(self):
        """Even if Dealer names an eliminated player, route to them is blocked
        — they won't appear in the active players list."""
        events = [
            _msg_event("marcus", channel_id="public", text="All in."),
            # Dealer tries to address rocky, but rocky is eliminated
            _msg_event("dealer", channel_id="public",
                       text="Marcus wins. Rocky, you're out. Diana, your move."),
        ]
        result = poker_orch.orchestrate(
            _poker_input(events=events, turn=2, eliminated=["rocky"])
        )
        # "diana" should be found since rocky is eliminated
        assert result.next_agents == ["diana"]

    def test_one_player_remaining_ends_game(self):
        # All but lila are eliminated
        result = poker_orch.orchestrate(
            _poker_input(eliminated=["marcus", "rocky", "diana"])
        )
        assert result.session_end is True
        assert result.end_reason == "win_condition"

    def test_all_players_eliminated_ends_game(self):
        result = poker_orch.orchestrate(
            _poker_input(eliminated=["marcus", "lila", "rocky", "diana"])
        )
        assert result.session_end is True
        assert result.end_reason == "win_condition"

    def test_max_turns_ends_session(self):
        inp = OrchestratorInput(
            config=_poker_config(),
            state=_poker_state(turn=300),
        )
        result = poker_orch.orchestrate(inp)
        assert result.session_end is True
        assert result.end_reason == "max_turns"

    def test_completion_signal_ends_session(self):
        events = [_msg_event("dealer", text="Marcus Webb WINS THE TOURNAMENT! Incredible!")]
        inp = OrchestratorInput(
            config=_poker_config(),
            state=_poker_state(events=events, turn=1),
        )
        result = poker_orch.orchestrate(inp)
        assert result.session_end is True
        assert result.end_reason == "completion_signal"

    def test_dealer_timeout_guard_skips_to_player(self):
        """If Dealer just timed out (TURN event present, no message), route to player."""
        from datetime import UTC, datetime
        events = [
            _msg_event("dealer", channel_id="public", text="Welcome everyone."),
            _msg_event("marcus", channel_id="public", text="Call."),
            TurnEvent(
                timestamp=datetime.now(UTC), turn_number=2,
                session_id="test", agent_ids=["dealer"], is_parallel=False,
            ),
        ]
        result = poker_orch.orchestrate(_poker_input(events=events, turn=2))
        assert "dealer" not in result.next_agents
        assert result.next_agents[0] in ("marcus", "lila", "rocky", "diana")

    def test_dealer_timeout_does_not_repeat_same_player(self):
        """When Dealer times out after Rocky speaks, Rocky should NOT be addressed again
        via the stale dealer_addressed path — fewest-messages fallback routes elsewhere."""
        from datetime import UTC, datetime
        # Dealer opened and addressed Rocky; Rocky spoke; Dealer timed out.
        dealer_open = _msg_event("dealer", channel_id="public",
                                 text="Welcome. Rocky, your move.")
        rocky_msg = _msg_event("rocky", channel_id="public", text="I raise 100.")
        # Dealer TURN fired but produced no message (timeout)
        dealer_turn = TurnEvent(
            timestamp=datetime.now(UTC), turn_number=2,
            session_id="test", agent_ids=["dealer"], is_parallel=False,
        )
        events = [dealer_open, rocky_msg, dealer_turn]
        result = poker_orch.orchestrate(_poker_input(events=events, turn=2))
        # dealer_just_attempted=True, last_was_player=True → skip "route to dealer"
        # dealer_msgs_after_player is empty (Dealer timed out) → fewest-msgs fallback
        # marcus has 0 messages and is first in config → selected
        assert result.next_agents == ["marcus"]
        assert result.next_agents[0] != "rocky"  # stale address not used

    def test_stale_dealer_address_not_used_after_timeout(self):
        """The stale 'Rocky, your move' from before timeout must not re-route to Rocky."""
        from datetime import UTC, datetime
        dealer_open = _msg_event("dealer", channel_id="public",
                                 text="Welcome. Marcus, you're first.")
        marcus_msg = _msg_event("marcus", channel_id="public", text="Raise 100.")
        dealer_ack = _msg_event("dealer", channel_id="public",
                                text="Marcus raises. Rocky, your turn.")
        rocky_msg = _msg_event("rocky", channel_id="public", text="Call.")
        # Now Dealer times out — last TURN was dealer, no dealer message after rocky
        dealer_turn = TurnEvent(
            timestamp=datetime.now(UTC), turn_number=4,
            session_id="test", agent_ids=["dealer"], is_parallel=False,
        )
        events = [dealer_open, marcus_msg, dealer_ack, rocky_msg, dealer_turn]
        result = poker_orch.orchestrate(_poker_input(events=events, turn=4))
        # dealer_just_attempted=True (last TURN=dealer), last_was_player=True (rocky)
        # dealer_msgs_after_player = [] (no dealer msg after rocky_msg) → fewest fallback
        # marcus has 1 msg, lila/diana have 0 → lila (first with 0, config order)
        assert result.next_agents[0] != "rocky"  # stale address must not win

    def test_advance_turns_is_always_one(self):
        result = poker_orch.orchestrate(_poker_input())
        assert result.advance_turns == 1
