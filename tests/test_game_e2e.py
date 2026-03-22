"""End-to-end game-play coverage through the real session runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.games import GameAction, GameRuntime, ModerationDecision, ScriptedModerationBackend
from src.providers import CompletionResult, MonologueSegment, TokenUsage
from src.session.config import AgentConfig, ChannelConfig, GameConfig, HITLConfig, OrchestratorConfig, SessionConfig, TranscriptConfig
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus


def _connect_four_llm_config(tmp_path: Path, *, max_turns: int = 4, monologue: bool = False) -> SessionConfig:
    return SessionConfig(
        title="Connect Four LLM Moderated E2E",
        description="Moderated Connect Four end to end",
        type="games",
        setting="game",
        topic="Play Connect Four.",
        agents=[
            AgentConfig(
                id="referee",
                name="The Referee",
                provider="anthropic",
                model="claude-sonnet-4-6",
                role="moderator",
            ),
            AgentConfig(
                id="player_red",
                name="Alex Mercer",
                provider="openai",
                model="gpt-4o",
                role="player",
                monologue=monologue,
                monologue_mode="prompt",
            ),
            AgentConfig(
                id="player_black",
                name="Sasha Kim",
                provider="google",
                model="gemini-2.5-flash",
                role="player",
            ),
        ],
        game=GameConfig(
            plugin="connect_four",
            name="Connect Four",
            moderation={
                "mode": "llm_moderated",
                "moderator_agent_id": "referee",
            },
        ),
        orchestrator=OrchestratorConfig(type="python", module="turn_based"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=True, format="both", path=tmp_path),
        max_turns=max_turns,
    )


def _build_moderated_runtime() -> GameRuntime:
    deterministic_config = SessionConfig(
        title="Connect Four Runtime Seed",
        description="Seed runtime",
        type="games",
        setting="game",
        topic="Play Connect Four.",
        agents=[
            AgentConfig(
                id="referee",
                name="The Referee",
                provider="anthropic",
                model="m",
                role="moderator",
            ),
            AgentConfig(
                id="player_red",
                name="Alex Mercer",
                provider="openai",
                model="m",
                role="player",
            ),
            AgentConfig(
                id="player_black",
                name="Sasha Kim",
                provider="google",
                model="m",
                role="player",
            ),
        ],
        game=GameConfig(plugin="connect_four", name="Connect Four"),
    )
    runtime = GameRuntime.from_session_config(deterministic_config)
    action_red = GameAction(action_type="drop_disc", payload={"column": 4})
    red_result = runtime.game.apply_action(runtime.state, "player_red", action_red)
    action_black = GameAction(action_type="drop_disc", payload={"column": 5})
    black_result = runtime.game.apply_action(red_result.next_state, "player_black", action_black)
    runtime.moderation_backend = ScriptedModerationBackend(
        decisions=[
            ModerationDecision(
                accepted=False,
                moderator_mode="llm_moderated",
                next_state=runtime.state,
                reason="Move format unclear. State the column explicitly.",
            ),
            ModerationDecision.from_apply_result(
                mode="llm_moderated",
                action=action_red,
                result=red_result,
            ),
            ModerationDecision.from_apply_result(
                mode="llm_moderated",
                action=action_black,
                result=black_result,
            ),
        ]
    )
    return runtime


def _battleship_config(tmp_path: Path, *, max_turns: int = 40) -> SessionConfig:
    return SessionConfig(
        title="Battleship E2E",
        description="Deterministic Battleship end to end",
        type="games",
        setting="game",
        topic="Play Battleship.",
        agents=[
            AgentConfig(
                id="captain_alpha",
                name="Commander Hayes",
                provider="openai",
                model="gpt-4o",
                role="player",
            ),
            AgentConfig(
                id="captain_beta",
                name="Captain Voss",
                provider="google",
                model="gemini-2.5-flash",
                role="player",
            ),
        ],
        game=GameConfig(plugin="battleship", name="Battleship"),
        orchestrator=OrchestratorConfig(type="python", module="turn_based"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=True, format="both", path=tmp_path),
        max_turns=max_turns,
    )


async def test_session_runner_e2e_moderated_connect_four_writes_transcripts_and_monologue(tmp_path):
    # max_turns=2: turn 0 (player_red rejected+retry, referee narration), turn 1 (player_black).
    # The session ends at max_turns before the second referee narration, so exactly 4 provider
    # calls are made and the session exits cleanly rather than exhausting the stub.
    config = _connect_four_llm_config(tmp_path, max_turns=2, monologue=True)
    bus = EventBus()
    runtime = _build_moderated_runtime()
    responses = [
        CompletionResult(
            text="<thinking>I should open in the center.</thinking>Column 4.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="test-model",
        ),
        CompletionResult(
            text="<thinking>Repeat the move clearly.</thinking>Column 4.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="test-model",
        ),
        CompletionResult(
            text="Red controls the center.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="test-model",
        ),
        CompletionResult(
            text="Column 5.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="test-model",
        ),
    ]

    with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(side_effect=responses)
            engine = SessionEngine(config, bus)
            state = await engine.run()

    assert state.end_reason == "max_turns", f"session ended dirty: {state.end_reason}"
    assert state.game_state.custom["authoritative_state"]["board"][5][3] == "R"
    assert state.game_state.custom["authoritative_state"]["board"][5][4] == "B"
    assert any(event.type == "MONOLOGUE" for event in state.events)
    assert any(event.type == "RULE_VIOLATION" for event in state.events)

    transcripts = sorted(tmp_path.glob("*.json"))
    assert transcripts, "expected transcript JSON output"
    payload = json.loads(transcripts[0].read_text(encoding="utf-8"))
    event_types = [event["type"] for event in payload["events"]]
    assert "MONOLOGUE" in event_types
    assert "RULE_VIOLATION" in event_types
    assert "GAME_STATE" in event_types
    markdown = sorted(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "[thinking]" in markdown
    assert "Rule violation" in markdown


async def test_session_runner_e2e_live_llm_moderated_connect_four_uses_provider_backed_moderator(tmp_path):
    config = _connect_four_llm_config(tmp_path, max_turns=1, monologue=False)
    bus = EventBus()

    async def complete_side_effect(**kwargs):
        messages = kwargs["messages"]
        if messages and messages[0]["role"] == "system":
            if "authoritative game moderator" in messages[0]["content"]:
                return CompletionResult(
                    text='{"accepted": true, "reason": "Accepted."}',
                    usage=TokenUsage(prompt_tokens=7, completion_tokens=3),
                    model="moderator-model",
                    metadata={
                        "moderation_decision": {
                            "accepted": True,
                            "reason": "Accepted.",
                        }
                    },
                )
        return CompletionResult(
            text="Column 4.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
        )

    with patch("src.session.engine.LiteLLMClient") as MockClient:
        MockClient.return_value.complete = AsyncMock(side_effect=complete_side_effect)
        engine = SessionEngine(config, bus)
        state = await engine.run()

    authoritative = state.game_state.custom["authoritative_state"]
    assert authoritative["board"][5][3] == "R"
    assert state.end_reason == "max_turns"
    transcripts = sorted(tmp_path.glob("*.json"))
    payload = json.loads(transcripts[0].read_text(encoding="utf-8"))
    assert any(
        event["type"] == "GAME_STATE" and "authoritative_delta" in event["updates"]
        for event in payload["events"]
    )


async def test_session_runner_e2e_provider_native_monologue_in_real_game_session(tmp_path):
    config = _connect_four_llm_config(tmp_path, max_turns=1, monologue=False)
    config.agents[1].monologue = True
    config.agents[1].monologue_mode = "native"
    bus = EventBus()

    async def complete_side_effect(**kwargs):
        messages = kwargs["messages"]
        if messages and messages[0]["role"] == "system":
            if "authoritative game moderator" in messages[0]["content"]:
                return CompletionResult(
                    text='{"accepted": true, "reason": "Accepted."}',
                    usage=TokenUsage(prompt_tokens=7, completion_tokens=3),
                    model="moderator-model",
                    metadata={
                        "moderation_decision": {
                            "accepted": True,
                            "reason": "Accepted.",
                        }
                    },
                )
        return CompletionResult(
            text="Column 4.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
            monologue=[
                MonologueSegment(
                    text="Center control is strongest here.",
                    source="provider_native",
                )
            ],
        )

    with patch("src.session.engine.LiteLLMClient") as MockClient:
        MockClient.return_value.complete = AsyncMock(side_effect=complete_side_effect)
        engine = SessionEngine(config, bus)
        state = await engine.run()

    assert any(
        event.type == "MONOLOGUE" and event.text == "Center control is strongest here."
        for event in state.events
    )
    transcripts = sorted(tmp_path.glob("*.json"))
    payload = json.loads(transcripts[0].read_text(encoding="utf-8"))
    assert any(
        event["type"] == "MONOLOGUE"
        and event["text"] == "Center control is strongest here."
        for event in payload["events"]
    )
    markdown = sorted(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "Center control is strongest here." in markdown


async def test_session_runner_e2e_battleship_reaches_terminal_state_without_hidden_state_leak(tmp_path):
    config = _battleship_config(tmp_path, max_turns=33)
    bus = EventBus()
    captured_messages: list[list[dict]] = []
    runtime = GameRuntime.from_session_config(config)
    beta_targets = [
        coordinate
        for _, coordinates in runtime.state.ship_positions["captain_beta"].items()
        for coordinate in coordinates
    ]
    alpha_occupied = {
        coordinate
        for _, coordinates in runtime.state.ship_positions["captain_alpha"].items()
        for coordinate in coordinates
    }
    beta_misses = [
        f"{column}{row}"
        for column in "ABCDEFGHIJ"
        for row in range(1, 11)
        if f"{column}{row}" not in alpha_occupied
    ]
    responses: list[str] = []
    for index, beta_target in enumerate(beta_targets):
        responses.append(beta_target)
        if index < len(beta_targets) - 1:
            responses.append(beta_misses[index])
    completions = [
        CompletionResult(
            text=text,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="test-model",
        )
        for text in responses
    ]

    async def capture_complete(**kwargs):
        captured_messages.append(kwargs["messages"])
        return completions.pop(0)

    with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(side_effect=capture_complete)
            engine = SessionEngine(config, bus)
            state = await engine.run()

    authoritative = state.game_state.custom["authoritative_state"]
    assert authoritative["winner"] == "captain_alpha"
    assert state.end_reason == "win_condition"
    assert captured_messages, "expected provider calls to be captured"
    alpha_context = "\n".join(message["content"] for message in captured_messages[0] if "content" in message)
    assert "A1" in alpha_context
    assert "B1" not in alpha_context
    transcripts = sorted(tmp_path.glob("*.json"))
    payload = json.loads(transcripts[0].read_text(encoding="utf-8"))
    assert any(event["type"] == "GAME_STATE" for event in payload["events"])


async def test_session_runner_e2e_deterministic_mafia_runs_night_day_and_reaches_terminal_win(tmp_path):
    config = SessionConfig(
        title="Mafia E2E",
        description="Deterministic Mafia end to end",
        type="games",
        setting="game",
        topic="Play Mafia.",
        agents=[
            AgentConfig(id="moderator", name="Narrator", provider="anthropic", model="m", role="moderator"),
            AgentConfig(id="mafia_don", name="Don Corvo", provider="openai", model="m", role="mafia", team="mafia"),
            AgentConfig(id="mafia_soldier", name="Sal Bricks", provider="google", model="m", role="mafia", team="mafia"),
            AgentConfig(id="mafia_consigliere", name="Luca Moretti", provider="anthropic", model="m", role="mafia", team="mafia"),
            AgentConfig(id="detective", name="Iris Sharp", provider="anthropic", model="m", role="detective"),
            AgentConfig(id="doctor", name="Dante Mend", provider="openai", model="m", role="doctor"),
            AgentConfig(id="villager_1", name="Rosa Fields", provider="google", model="m", role="villager"),
            AgentConfig(id="villager_2", name="Marco Stone", provider="anthropic", model="m", role="villager"),
            AgentConfig(id="villager_3", name="Cleo Vance", provider="openai", model="m", role="villager"),
            AgentConfig(id="villager_4", name="Reed Cole", provider="google", model="m", role="villager"),
        ],
        channels=[ChannelConfig(id="mafia", type="team", members=["mafia_don", "mafia_soldier", "mafia_consigliere"])],
        game=GameConfig(plugin="mafia", name="Mafia"),
        orchestrator=OrchestratorConfig(type="python", module="turn_based"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=True, format="both", path=tmp_path),
        max_turns=20,
    )
    bus = EventBus()

    runtime = GameRuntime.from_session_config(config)
    runtime.state.phase = "night_mafia_discussion"
    runtime.state.round_number = 3
    runtime.state.alive_players = ["mafia_don", "detective", "doctor", "villager_2", "villager_3"]
    runtime.state.eliminated = ["mafia_soldier", "mafia_consigliere", "villager_1", "villager_4"]
    runtime.state.revealed_roles = {
        "mafia_soldier": "mafia",
        "mafia_consigliere": "mafia",
        "villager_1": "villager",
        "villager_4": "villager",
    }
    runtime.state.discussion_order = ["mafia_don"]
    runtime.state.discussion_index = 0
    runtime.state.current_vote_order = ["mafia_don"]
    runtime.state.vote_index = 0
    runtime.state.doctor_history = ["villager_2"]

    responses = [
        CompletionResult(
            text="<team>I need detective gone.</team>",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
        ),
        CompletionResult(
            text='{"target": "detective"}',
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
            parsed_action={"target": "detective"},
        ),
        CompletionResult(
            text='{"investigate": "mafia_don"}',
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
            parsed_action={"investigate": "mafia_don"},
        ),
        CompletionResult(
            text='{"protect": "villager_3"}',
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
            parsed_action={"protect": "villager_3"},
        ),
        CompletionResult(
            text="Dawn breaks over Ravenhollow as the town learns who was lost in the dark.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="narrator-model",
        ),
        CompletionResult(
            text="I know enough now. Don Corvo is the last wolf.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
        ),
        CompletionResult(
            text="I agree. Don Corvo doesn't survive this day.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
        ),
        CompletionResult(
            text="The evidence points to Don Corvo.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
        ),
        CompletionResult(
            text="Don Corvo is Mafia. Vote him out.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
        ),
        CompletionResult(
            text='{"vote_for": "doctor"}',
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
            parsed_action={"vote_for": "doctor"},
        ),
        CompletionResult(
            text='{"vote_for": "mafia_don"}',
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
            parsed_action={"vote_for": "mafia_don"},
        ),
        CompletionResult(
            text='{"vote_for": "mafia_don"}',
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
            parsed_action={"vote_for": "mafia_don"},
        ),
        CompletionResult(
            text='{"vote_for": "mafia_don"}',
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="player-model",
            parsed_action={"vote_for": "mafia_don"},
        ),
        CompletionResult(
            text="The square erupts as Don Corvo is dragged into the light and the last wolf falls.",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
            model="narrator-model",
        ),
    ]

    with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
        with patch("src.session.engine.LiteLLMClient") as MockClient:
            MockClient.return_value.complete = AsyncMock(side_effect=responses)
            engine = SessionEngine(config, bus)
            state = await engine.run()

    assert state.end_reason == "win_condition"
    authoritative = state.game_state.custom["authoritative_state"]
    assert authoritative["winner"] == "town"
    assert "mafia_don" in authoritative["eliminated"]

    transcripts = sorted(tmp_path.glob("*.json"))
    assert transcripts
    payload = json.loads(transcripts[0].read_text(encoding="utf-8"))
    assert any(
        event["type"] == "MESSAGE"
        and event["agent_id"] == "game_engine"
        and event.get("recipient_id") == "detective"
        for event in payload["events"]
    )
    assert any(
        event["type"] == "MESSAGE"
        and event["agent_id"] == "game_engine"
        and "night 3 result" in event["text"].lower()
        for event in payload["events"]
    )
    assert any(event["type"] == "SESSION_END" and event["reason"] == "win_condition" for event in payload["events"])
