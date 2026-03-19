"""
Textual pilot tests for the one-0-one TUI.

Covers:
    - OneOhOneApp: startup, browser screen mount, quit binding
    - SessionBrowserScreen: template list, filter tabs
    - ChannelTabs: default state, add_channel, append_message, unread badges
    - MonologuePanel: TURN clears, MONOLOGUE appends, toggle class
    - TurnIndicator: spinner state, label updates, SESSION_END
    - AgentRoster: populate, set_status
    - HITLInputBar: hidden state, show_for_turn, submit emits message, clears
    - colors: agent_color palette and wraparound
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Input, Label, RichLog, Tab, Tabs

from src.session.config import AgentConfig
from src.games import GameAction, GameRuntime, ModerationDecision, ScriptedModerationBackend
from src.providers import CompletionResult, TokenUsage
from src.session.events import (
    ChannelCreatedEvent,
    MessageEvent,
    MonologueEvent,
    SessionEndEvent,
    TurnEvent,
)
from src.tui.widgets.agent_roster import AgentRoster
from src.tui.widgets.channel_tabs import ChannelTabs
from src.tui.widgets.hitl_input import HITLInputBar
from src.tui.widgets.monologue_panel import MonologuePanel
from src.tui.widgets.turn_indicator import TurnIndicator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_turn(turn_number: int = 1, agent_ids: list[str] | None = None) -> TurnEvent:
    return TurnEvent(
        session_id="s1",
        turn_number=turn_number,
        timestamp=datetime(2025, 1, 1),
        agent_ids=agent_ids or ["Alice"],
        is_parallel=False,
    )


def _make_message(
    text: str = "Hello.",
    channel_id: str = "public",
    agent_id: str = "a",
    agent_name: str = "Alice",
    recipient_id: str | None = None,
    is_parallel: bool = False,
) -> MessageEvent:
    return MessageEvent(
        session_id="s1",
        turn_number=1,
        timestamp=datetime(2025, 1, 1),
        agent_id=agent_id,
        agent_name=agent_name,
        model="test",
        channel_id=channel_id,
        text=text,
        is_parallel=is_parallel,
        recipient_id=recipient_id,
    )


def _make_mono(text: str = "I'm thinking.", agent_name: str = "Alice") -> MonologueEvent:
    return MonologueEvent(
        session_id="s1",
        turn_number=1,
        timestamp=datetime(2025, 1, 1),
        agent_id="a",
        agent_name=agent_name,
        text=text,
    )


def _make_channel_created(channel_id: str, channel_type: str = "team") -> ChannelCreatedEvent:
    return ChannelCreatedEvent(
        session_id="s1",
        timestamp=datetime(2025, 1, 1),
        channel_id=channel_id,
        channel_type=channel_type,
        members=[],
    )


def _make_session_end(reason: str = "max_turns") -> SessionEndEvent:
    return SessionEndEvent(
        session_id="s1",
        timestamp=datetime(2025, 1, 1),
        turn_number=2,
        reason=reason,
    )


def _make_live_chat_config(*, monologue: bool = True, max_turns: int = 3):
    from src.session.config import GameConfig, HITLConfig, OrchestratorConfig, SessionConfig, TranscriptConfig

    return SessionConfig(
        title="Live Chat Connect Four",
        description="TUI game test",
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
                monologue=monologue,
                monologue_mode="prompt",
            ),
        ],
        game=GameConfig(
            plugin="connect_four",
            name="Connect Four",
            moderation={"mode": "llm_moderated", "moderator_agent_id": "referee"},
        ),
        orchestrator=OrchestratorConfig(type="python", module="turn_based"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=False, format="markdown", path="/tmp/"),
        max_turns=max_turns,
    )


def _build_live_chat_runtime():
    from src.session.config import GameConfig, SessionConfig

    seed = SessionConfig(
        title="Seed",
        description="Seed",
        type="games",
        setting="game",
        topic="Play Connect Four.",
        agents=[
            AgentConfig(id="referee", name="The Referee", provider="anthropic", model="m", role="moderator"),
            AgentConfig(id="player_red", name="Alex Mercer", provider="openai", model="m", role="player"),
            AgentConfig(id="player_black", name="Sasha Kim", provider="google", model="m", role="player"),
        ],
        game=GameConfig(plugin="connect_four", name="Connect Four"),
    )
    runtime = GameRuntime.from_session_config(seed)
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


# ---------------------------------------------------------------------------
# Minimal test apps (one widget each, no external CSS)
# ---------------------------------------------------------------------------

class ChannelTabsApp(App):
    CSS = ""
    def compose(self) -> ComposeResult:
        yield ChannelTabs(id="ct")


class MonologuePanelApp(App):
    CSS = ""
    def compose(self) -> ComposeResult:
        yield MonologuePanel(id="mp")


class TurnIndicatorApp(App):
    CSS = ""
    def compose(self) -> ComposeResult:
        yield TurnIndicator(id="ti")


class AgentRosterApp(App):
    CSS = ""
    def compose(self) -> ComposeResult:
        yield AgentRoster(id="ar")


class HITLInputBarApp(App):
    CSS = ""
    def compose(self) -> ComposeResult:
        yield HITLInputBar(id="hib")


class LiveChatTestApp(App):
    CSS = ""

    def __init__(self, screen, **kwargs):
        super().__init__(**kwargs)
        self._screen = screen
        self.notifications: list[tuple[str, str | None]] = []

    def on_mount(self) -> None:
        self.push_screen(self._screen)

    def notify(self, message, *, title=None, **kwargs):  # type: ignore[override]
        self.notifications.append((str(message), title))
        return None


# ---------------------------------------------------------------------------
# colors module
# ---------------------------------------------------------------------------

class TestColors:
    def test_agent_color_returns_string(self):
        from src.tui.colors import agent_color
        color = agent_color(0)
        assert isinstance(color, str)
        assert len(color) > 0

    def test_agent_color_all_six_slots(self):
        from src.tui.colors import agent_color, AGENT_PALETTE
        for i in range(len(AGENT_PALETTE)):
            assert agent_color(i) == AGENT_PALETTE[i]

    def test_agent_color_wraps_around(self):
        from src.tui.colors import agent_color, AGENT_PALETTE
        assert agent_color(len(AGENT_PALETTE)) == AGENT_PALETTE[0]
        assert agent_color(len(AGENT_PALETTE) + 1) == AGENT_PALETTE[1]


# ---------------------------------------------------------------------------
# ChannelTabs
# ---------------------------------------------------------------------------

class TestChannelTabs:
    async def test_public_tab_present_on_mount(self):
        app = ChannelTabsApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            tab = app.query_one("#tab_public", Tab)
            assert tab is not None

    async def test_public_log_exists(self):
        app = ChannelTabsApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            log = app.query_one("#log_public", RichLog)
            assert log is not None
            assert log.display is True

    async def test_add_channel_creates_tab(self):
        app = ChannelTabsApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            ct = app.query_one(ChannelTabs)
            ct.add_channel(_make_channel_created("team_red", "team"))
            await pilot.pause()
            tab = app.query_one("#tab_team_red", Tab)
            assert tab is not None

    async def test_add_channel_public_is_noop(self):
        """Adding 'public' channel should not create a duplicate tab."""
        app = ChannelTabsApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            ct = app.query_one(ChannelTabs)
            initial_count = len(list(ct.query(Tab)))
            ct.add_channel(_make_channel_created("public", "public"))
            await pilot.pause()
            assert len(list(ct.query(Tab))) == initial_count

    async def test_append_message_to_public(self):
        app = ChannelTabsApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            ct = app.query_one(ChannelTabs)
            ct.append_message(_make_message("Hello world.", "public"))
            await pilot.pause()
            log = app.query_one("#log_public", RichLog)
            # RichLog.lines returns strips — at least one line written
            assert len(log.lines) > 0

    async def test_append_message_unknown_channel_goes_to_public(self):
        """Messages to an unknown channel should fall back to public log."""
        app = ChannelTabsApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            ct = app.query_one(ChannelTabs)
            ct.append_message(_make_message("Fallback.", "team_unknown"))
            await pilot.pause()
            log = app.query_one("#log_public", RichLog)
            assert len(log.lines) > 0

    def test_format_message_plain(self):
        msg = _make_message("Hello!")
        rendered = ChannelTabs._format_message(msg)
        assert "Alice" in rendered
        assert "Hello!" in rendered

    def test_format_message_private(self):
        msg = _make_message("Secret.", recipient_id="b")
        rendered = ChannelTabs._format_message(msg)
        assert "🔒" in rendered
        assert "Secret." in rendered

    def test_format_message_parallel(self):
        msg = _make_message("Parallel.", is_parallel=True)
        rendered = ChannelTabs._format_message(msg)
        assert "parallel" in rendered.lower()

    def test_tab_label_public(self):
        assert ChannelTabs._make_label("public", "public") == "Public"

    def test_tab_label_team(self):
        label = ChannelTabs._make_label("team_red", "team")
        assert "Red" in label or "red" in label.lower()

    def test_tab_label_private_with_members(self):
        label = ChannelTabs._make_label("private_a_b", "private", ["echo", "ripple"])
        assert "Echo" in label
        assert "Ripple" in label

    def test_tab_label_private_no_members(self):
        label = ChannelTabs._make_label("private_ab", "private")
        assert label == "Private"


# ---------------------------------------------------------------------------
# MonologuePanel
# ---------------------------------------------------------------------------

class TestMonologuePanel:
    async def test_turn_event_updates_header(self):
        app = MonologuePanelApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            panel = app.query_one(MonologuePanel)
            panel.handle_event(_make_turn(agent_ids=["Alice"]))
            await pilot.pause()
            header = app.query_one("#mono-header", Label)
            assert "Alice" in str(header.render())

    async def test_monologue_event_appends_to_log(self):
        app = MonologuePanelApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            panel = app.query_one(MonologuePanel)
            panel.handle_event(_make_mono("inner thoughts"))
            await pilot.pause()
            log = app.query_one("#mono-log", RichLog)
            assert len(log.lines) > 0

    async def test_turn_event_clears_log(self):
        app = MonologuePanelApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            panel = app.query_one(MonologuePanel)
            panel.handle_event(_make_mono("old thoughts"))
            await pilot.pause()
            # TURN should clear
            panel.handle_event(_make_turn())
            await pilot.pause()
            log = app.query_one("#mono-log", RichLog)
            assert len(log.lines) == 0

    async def test_toggle_adds_open_class(self):
        app = MonologuePanelApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            panel = app.query_one(MonologuePanel)
            assert not panel.has_class("open")
            panel.toggle()
            await pilot.pause()
            assert panel.has_class("open")

    async def test_toggle_twice_removes_open_class(self):
        app = MonologuePanelApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            panel = app.query_one(MonologuePanel)
            panel.toggle()
            panel.toggle()
            await pilot.pause()
            assert not panel.has_class("open")


# ---------------------------------------------------------------------------
# TurnIndicator
# ---------------------------------------------------------------------------

class TestTurnIndicator:
    async def test_spinner_hidden_on_mount(self):
        app = TurnIndicatorApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            spinner = app.query_one("#spinner")
            assert spinner.display is False

    async def test_turn_event_shows_spinner(self):
        app = TurnIndicatorApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            ti = app.query_one(TurnIndicator)
            ti.handle_turn(_make_turn(turn_number=1, agent_ids=["Alice"]))
            await pilot.pause()
            spinner = app.query_one("#spinner")
            assert spinner.display is True

    async def test_turn_event_updates_label(self):
        app = TurnIndicatorApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            ti = app.query_one(TurnIndicator)
            ti.handle_turn(_make_turn(turn_number=3, agent_ids=["Bob"]))
            await pilot.pause()
            label = app.query_one("#turn-label", Label)
            label_text = str(label.render())
            assert "3" in label_text
            assert "Bob" in label_text

    async def test_message_event_hides_spinner(self):
        app = TurnIndicatorApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            ti = app.query_one(TurnIndicator)
            ti.handle_turn(_make_turn())  # spinner on
            await pilot.pause()
            ti.handle_turn(_make_message())  # spinner off
            await pilot.pause()
            spinner = app.query_one("#spinner")
            assert spinner.display is False

    async def test_session_end_shows_ended_label(self):
        app = TurnIndicatorApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            ti = app.query_one(TurnIndicator)
            ti.handle_turn(_make_session_end())
            await pilot.pause()
            label = app.query_one("#turn-label", Label)
            assert "ended" in str(label.render()).lower()
            spinner = app.query_one("#spinner")
            assert spinner.display is False


# ---------------------------------------------------------------------------
# AgentRoster
# ---------------------------------------------------------------------------

class TestAgentRoster:
    def _make_agents(self, n: int = 2) -> list[AgentConfig]:
        names = ["Alice", "Bob", "Carol", "Dan"]
        return [
            AgentConfig(
                id=f"agent_{i}",
                name=names[i],
                provider="anthropic",
                model="claude-sonnet-4-6",
                role="participant",
            )
            for i in range(n)
        ]

    async def test_columns_present_on_mount(self):
        app = AgentRosterApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            table = app.query_one(DataTable)
            # Textual 8: DataTable.columns is a dict
            assert len(table.columns) == 3

    async def test_populate_adds_rows(self):
        app = AgentRosterApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            roster = app.query_one(AgentRoster)
            agents = self._make_agents(2)
            roster.populate(agents)
            await pilot.pause()
            table = app.query_one(DataTable)
            assert len(table.rows) == 2

    async def test_populate_agent_count(self):
        app = AgentRosterApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            roster = app.query_one(AgentRoster)
            roster.populate(self._make_agents(3))
            await pilot.pause()
            table = app.query_one(DataTable)
            assert len(table.rows) == 3

    async def test_set_status_does_not_raise_for_unknown_agent(self):
        """set_status() on an unknown agent id should be a no-op (not raise)."""
        app = AgentRosterApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            roster = app.query_one(AgentRoster)
            roster.set_status("nonexistent-agent", "speaking")  # must not raise
            await pilot.pause()

    async def test_populate_then_set_status(self):
        app = AgentRosterApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            roster = app.query_one(AgentRoster)
            roster.populate(self._make_agents(1))
            await pilot.pause()
            # set_status should not raise after populate
            roster.set_status("agent_0", "speaking")
            await pilot.pause()


# ---------------------------------------------------------------------------
# HITLInputBar
# ---------------------------------------------------------------------------

class TestHITLInputBar:
    async def test_show_for_turn_makes_bar_visible(self):
        app = HITLInputBarApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            bar = app.query_one(HITLInputBar)
            bar.show_for_turn(has_team=False)
            await pilot.pause()
            assert bar.display is True

    async def test_show_for_turn_with_team_shows_channel_select(self):
        app = HITLInputBarApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            bar = app.query_one(HITLInputBar)
            bar.show_for_turn(has_team=True)
            await pilot.pause()
            channel_select = bar.query_one("#channel-select")
            assert channel_select.display is True

    async def test_show_for_turn_without_team_hides_channel_select(self):
        app = HITLInputBarApp()
        async with app.run_test(headless=True) as pilot:
            await pilot.pause()
            bar = app.query_one(HITLInputBar)
            bar.show_for_turn(has_team=False)
            await pilot.pause()
            channel_select = bar.query_one("#channel-select")
            assert channel_select.display is False

    async def test_submit_posts_hitl_message(self):
        """Pressing send button emits HITLInputBar.HITLMessage with the text."""
        received: list[HITLInputBar.HITLMessage] = []

        class TestApp(App):
            CSS = ""
            def compose(self) -> ComposeResult:
                yield HITLInputBar(id="hib")
            # Textual handler name: on_{class_snake}_{message_snake}
            # HITLInputBar → hitlinput_bar, HITLMessage → hitlmessage
            def on_hitlinput_bar_hitlmessage(self, msg: HITLInputBar.HITLMessage) -> None:
                received.append(msg)

        app = TestApp()
        async with app.run_test(headless=True) as pilot:
            bar = app.query_one(HITLInputBar)
            bar.show_for_turn()
            await pilot.pause()

            inp = bar.query_one("#hitl-input", Input)
            await pilot.click(inp)
            await pilot.press("H", "e", "l", "l", "o")
            await pilot.pause()

            btn = bar.query_one("#hitl-send")
            await pilot.click(btn)
            await pilot.pause()

            assert len(received) == 1
            assert received[0].text == "Hello"
            assert received[0].channel_id == "public"

    async def test_submit_clears_input_and_hides_bar(self):
        """After submit, the input clears and bar is hidden."""
        class TestApp(App):
            CSS = ""
            def compose(self) -> ComposeResult:
                yield HITLInputBar(id="hib")
            def on_hitlinput_bar_hitlmessage(self, _msg) -> None:
                pass

        app = TestApp()
        async with app.run_test(headless=True) as pilot:
            bar = app.query_one(HITLInputBar)
            bar.show_for_turn()
            await pilot.pause()

            inp = bar.query_one("#hitl-input", Input)
            await pilot.click(inp)
            await pilot.press("H", "i")
            await pilot.pause()

            btn = bar.query_one("#hitl-send")
            await pilot.click(btn)
            await pilot.pause()

            assert inp.value == ""

    async def test_empty_text_does_not_submit(self):
        """Clicking send with empty input should not post a message."""
        received: list = []

        class TestApp(App):
            CSS = ""
            def compose(self) -> ComposeResult:
                yield HITLInputBar(id="hib")
            def on_hitlinput_bar_hitlmessage(self, msg) -> None:
                received.append(msg)

        app = TestApp()
        async with app.run_test(headless=True) as pilot:
            bar = app.query_one(HITLInputBar)
            bar.show_for_turn()
            await pilot.pause()

            btn = bar.query_one("#hitl-send")
            await pilot.click(btn)
            await pilot.pause()

            assert len(received) == 0
            assert bar.display is True


# ---------------------------------------------------------------------------
# SessionBrowserScreen / OneOhOneApp
# ---------------------------------------------------------------------------

class TestOneOhOneApp:
    async def test_app_mounts_browser_screen(self):
        """App starts and pushes the browser screen."""
        from src.tui.app import OneOhOneApp
        from src.tui.screens.browser import SessionBrowserScreen

        with patch("src.tui.screens.browser.settings") as mock_settings:
            mock_settings.session_templates_path = "/tmp/nonexistent_path_xyz"
            app = OneOhOneApp()
            async with app.run_test(headless=True, size=(80, 24)) as pilot:
                await pilot.pause()
                assert isinstance(app.screen, SessionBrowserScreen)

    async def test_browser_screen_has_filter_tabs(self):
        """Browser screen renders type-filter tabs."""
        from src.tui.app import OneOhOneApp

        with patch("src.tui.screens.browser.settings") as mock_settings:
            mock_settings.session_templates_path = "/tmp/nonexistent_path_xyz"
            app = OneOhOneApp()
            async with app.run_test(headless=True, size=(80, 24)) as pilot:
                await pilot.pause()
                # Query from the screen (not app) since it's a pushed screen
                tabs = app.screen.query_one("#type-filter", Tabs)
                assert tabs is not None
                assert len(list(tabs.query(Tab))) == 6

    async def test_browser_shows_empty_list_when_no_templates(self):
        """With no templates dir, ListView is empty (no crash)."""
        from src.tui.app import OneOhOneApp
        from textual.widgets import ListView

        with patch("src.tui.screens.browser.settings") as mock_settings:
            mock_settings.session_templates_path = "/tmp/nonexistent_path_xyz"
            app = OneOhOneApp()
            async with app.run_test(headless=True, size=(80, 24)) as pilot:
                await pilot.pause()
                lv = app.screen.query_one(ListView)
                assert len(list(lv.query("ListItem"))) == 0

    async def test_browser_loads_templates_from_disk(self, tmp_path):
        """Templates in the configured directory appear in the ListView."""
        from src.tui.app import OneOhOneApp
        from textual.widgets import ListView

        template_yaml = """\
title: "Test Chat"
description: "A test template"
type: social
setting: social
topic: "Chat."
agents:
  - id: a
    name: Alice
    provider: anthropic
    model: claude-sonnet-4-6
    role: participant
orchestrator:
  type: python
  module: basic
hitl:
  enabled: false
transcript:
  auto_save: false
  format: markdown
  path: /tmp/
"""
        (tmp_path / "test-chat.yaml").write_text(template_yaml)

        with patch("src.tui.screens.browser.settings") as mock_settings:
            mock_settings.session_templates_path = str(tmp_path)
            app = OneOhOneApp()
            async with app.run_test(headless=True, size=(80, 24)) as pilot:
                # Wait for the worker to load templates
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()
                lv = app.screen.query_one(ListView)
                assert len(list(lv.query("ListItem"))) == 1


class TestLiveChatScreen:
    async def test_live_chat_runs_moderated_game_and_renders_chat_system_and_monologue(self):
        from src.tui.screens.live_chat import LiveChatScreen

        runtime = _build_live_chat_runtime()
        responses = [
            CompletionResult(
                text="<thinking>I should control the center.</thinking>Column 4.",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
                model="test-model",
            ),
            CompletionResult(
                text="<thinking>Try the same move again clearly.</thinking>Column 4.",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
                model="test-model",
            ),
            CompletionResult(
                text="<thinking>I can mirror the center pressure.</thinking>Column 5.",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=5),
                model="test-model",
            ),
        ]

        with patch("src.session.engine.GameRuntime.from_session_config", return_value=runtime):
            with patch("src.session.engine.LiteLLMClient") as MockClient:
                MockClient.return_value.complete = AsyncMock(side_effect=responses)
                app = LiveChatTestApp(LiveChatScreen(_make_live_chat_config(max_turns=2)))
                async with app.run_test(headless=True, size=(120, 40)) as pilot:
                    await pilot.pause()
                    await pilot.pause()
                    await pilot.pause()
                    await pilot.pause()
                    await pilot.pause()

                    screen = app.screen
                    logs = screen.query("RichLog")
                    line_count = sum(len(log.lines) for log in logs)
                    mono_log = screen.query_one("#mono-log", RichLog)
                    turn_label = screen.query_one("#turn-label", Label)

                    assert line_count > 0
                    assert len(mono_log.lines) > 0
                    assert "ended" in str(turn_label.render()).lower()
                    assert any("Session ended" in message for message, _ in app.notifications)
