"""End-to-end game-play coverage through the real session runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.games import GameAction, GameRuntime, ModerationDecision, ScriptedModerationBackend
from src.providers import CompletionResult, MonologueSegment, TokenUsage
from src.session.config import AgentConfig, GameConfig, HITLConfig, OrchestratorConfig, SessionConfig, TranscriptConfig
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
    config = _connect_four_llm_config(tmp_path, max_turns=3, monologue=True)
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
    responses = [
        "B1", "A10",
        "B2", "A9",
        "B3", "A8",
        "B4", "A7",
        "B5", "A6",
        "D1", "J10",
        "D2", "J9",
        "D3", "J8",
        "D4", "J7",
        "F1", "J6",
        "F2", "I10",
        "F3", "I9",
        "H1", "I8",
        "H2", "I7",
        "H3", "I6",
        "J1", "H10",
        "J2",
    ]
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
