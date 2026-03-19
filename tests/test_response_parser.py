"""Tests for ResponseParser XML tag extraction."""

import pytest

from src.response_parser import ResponseParser


@pytest.fixture
def parser() -> ResponseParser:
    return ResponseParser()


class TestPublicFallback:
    def test_no_tags_goes_to_public(self, parser):
        result = parser.parse("Hello everyone, let's discuss AI.")
        assert result.public_message == "Hello everyone, let's discuss AI."
        assert result.thinking is None
        assert result.team_message is None
        assert result.private_to is None
        assert result.tags_found == []

    def test_empty_string(self, parser):
        result = parser.parse("")
        assert result.public_message == ""

    def test_whitespace_only(self, parser):
        result = parser.parse("   \n\t  ")
        assert result.public_message == ""


class TestThinkingTag:
    def test_thinking_extracted(self, parser):
        raw = "<thinking>I should pivot my argument.</thinking>\nHello everyone."
        result = parser.parse(raw)
        assert result.thinking == "I should pivot my argument."
        assert result.public_message == "Hello everyone."
        assert "thinking" in result.tags_found

    def test_thinking_multiline(self, parser):
        raw = "<thinking>\nLine 1\nLine 2\n</thinking>Public text."
        result = parser.parse(raw)
        assert "Line 1" in result.thinking
        assert result.public_message == "Public text."

    def test_thinking_case_insensitive(self, parser):
        raw = "<THINKING>internal</THINKING>public"
        result = parser.parse(raw)
        assert result.thinking == "internal"


class TestTeamTag:
    def test_team_message_extracted(self, parser):
        raw = "<team>Let's focus on economics.</team>I think the economy angle is strongest."
        result = parser.parse(raw)
        assert result.team_message == "Let's focus on economics."
        assert result.public_message == "I think the economy angle is strongest."
        assert "team" in result.tags_found

    def test_team_only_no_public(self, parser):
        raw = "<team>Strategy only.</team>"
        result = parser.parse(raw)
        assert result.team_message == "Strategy only."
        assert result.public_message == ""


class TestPrivateTag:
    def test_private_extracted(self, parser):
        raw = '<private to="Rex">Don\'t counter me yet.</private>Public statement here.'
        result = parser.parse(raw)
        assert result.private_to == "Rex"
        assert result.private_message == "Don't counter me yet."
        assert result.public_message == "Public statement here."
        assert "private" in result.tags_found

    def test_private_single_quotes(self, parser):
        raw = "<private to='Nova'>Whisper.</private>"
        result = parser.parse(raw)
        assert result.private_to == "Nova"
        assert result.private_message == "Whisper."

    def test_private_only_no_public(self, parser):
        raw = '<private to="Agent2">Secret.</private>'
        result = parser.parse(raw)
        assert result.private_message == "Secret."
        assert result.public_message == ""


class TestCombinedTags:
    def test_thinking_plus_public(self, parser):
        raw = "<thinking>Reasoning...</thinking>My public response."
        result = parser.parse(raw)
        assert result.thinking == "Reasoning..."
        assert result.public_message == "My public response."

    def test_all_tags_combined(self, parser):
        raw = (
            "<thinking>Internal thought.</thinking>"
            "<team>Team strategy.</team>"
            '<private to="Rex">Just between us.</private>'
            "Public message to all."
        )
        result = parser.parse(raw)
        assert result.thinking == "Internal thought."
        assert result.team_message == "Team strategy."
        assert result.private_to == "Rex"
        assert result.private_message == "Just between us."
        assert result.public_message == "Public message to all."
        assert set(result.tags_found) == {"thinking", "team", "private"}

    def test_thinking_and_private_no_public(self, parser):
        raw = "<thinking>Hmm.</thinking><private to=\"A\">Secret.</private>"
        result = parser.parse(raw)
        assert result.thinking == "Hmm."
        assert result.private_message == "Secret."
        assert result.public_message == ""


class TestEliminateTag:
    """REQ-PERF-002: <eliminate> tag extraction."""

    def test_single_eliminate_extracted(self, parser):
        raw = "<eliminate>mafia_don</eliminate>The vote has been cast."
        result = parser.parse(raw)
        assert result.eliminated_agents == ["mafia_don"]
        assert result.public_message == "The vote has been cast."
        assert "eliminate" in result.tags_found

    def test_multiple_eliminate_tags(self, parser):
        raw = "<eliminate>agent_1</eliminate><eliminate>agent_2</eliminate>Two players removed."
        result = parser.parse(raw)
        assert "agent_1" in result.eliminated_agents
        assert "agent_2" in result.eliminated_agents
        assert result.public_message == "Two players removed."

    def test_no_eliminate_tag_returns_empty_list(self, parser):
        result = parser.parse("No eliminations today.")
        assert result.eliminated_agents == []
        assert "eliminate" not in result.tags_found

    def test_eliminate_whitespace_stripped(self, parser):
        raw = "<eliminate>  mafia_soldier  </eliminate>Done."
        result = parser.parse(raw)
        assert result.eliminated_agents == ["mafia_soldier"]

    def test_eliminate_combined_with_other_tags(self, parser):
        raw = (
            "<thinking>The vote is done.</thinking>"
            "<eliminate>villager_1</eliminate>"
            "Rosa Fields has been eliminated."
        )
        result = parser.parse(raw)
        assert result.thinking == "The vote is done."
        assert result.eliminated_agents == ["villager_1"]
        assert result.public_message == "Rosa Fields has been eliminated."

    def test_eliminate_does_not_appear_in_public_message(self, parser):
        raw = "<eliminate>detective</eliminate>Iris Sharp is gone."
        result = parser.parse(raw)
        assert "eliminate" not in result.public_message
        assert "detective" not in result.public_message


class TestNamePrefixStripping:
    """Tests for agent_name prefix stripping."""

    def test_single_prefix_stripped(self, parser):
        result = parser.parse("Marco Stone: I've been watching.", agent_name="Marco Stone")
        assert result.public_message == "I've been watching."

    def test_repeated_prefix_stripped(self, parser):
        result = parser.parse(
            "Marco Stone: Marco Stone: I've been watching.",
            agent_name="Marco Stone",
        )
        assert result.public_message == "I've been watching."

    def test_no_prefix_unchanged(self, parser):
        result = parser.parse("I've been watching.", agent_name="Marco Stone")
        assert result.public_message == "I've been watching."

    def test_partial_name_not_stripped(self, parser):
        result = parser.parse("Marco: something.", agent_name="Marco Stone")
        assert result.public_message == "Marco: something."

    def test_case_insensitive_strip(self, parser):
        result = parser.parse("marco stone: Hello.", agent_name="Marco Stone")
        assert result.public_message == "Hello."

    def test_prefix_stripped_from_team_message(self, parser):
        raw = "<team>Marco Stone: coordinate tonight</team>"
        result = parser.parse(raw, agent_name="Marco Stone")
        assert result.team_message == "coordinate tonight"

    def test_prefix_stripped_from_private_message(self, parser):
        raw = '<private to="Iris">Marco Stone: I trust you.</private>'
        result = parser.parse(raw, agent_name="Marco Stone")
        assert result.private_message == "I trust you."

    def test_no_agent_name_no_strip(self, parser):
        result = parser.parse("Marco Stone: Hello.")
        assert result.public_message == "Marco Stone: Hello."


class TestEdgeCases:
    def test_nested_content_preserved(self, parser):
        """Tags inside tag content should not be double-parsed."""
        raw = "<thinking>I see <team> tags but that's fine.</thinking>Public."
        result = parser.parse(raw)
        assert "<team>" in result.thinking

    def test_tags_found_list_accuracy(self, parser):
        raw = "<thinking>t</thinking><team>m</team>"
        result = parser.parse(raw)
        assert "thinking" in result.tags_found
        assert "team" in result.tags_found
        assert "private" not in result.tags_found


class TestStructuredSegments:
    def test_build_communication_segments(self, parser):
        raw = (
            "<team>Team plan.</team>"
            '<private to="Rex">Whisper.</private>'
            "Public note."
        )
        parsed = parser.parse(raw)
        segments = parser.build_communication_segments(parsed)

        assert [segment.visibility for segment in segments] == [
            "team", "private", "public"
        ]
        assert segments[0].text == "Team plan."
        assert segments[1].recipient == "Rex"
        assert segments[2].text == "Public note."

    def test_build_monologue_segments(self, parser):
        parsed = parser.parse("<thinking>Internal only.</thinking>Public.")
        segments = parser.build_monologue_segments(parsed)

        assert len(segments) == 1
        assert segments[0].text == "Internal only."
        assert segments[0].source == "prompt_fallback"


class TestOrphanedClosingTag:
    """Model emits </thinking> without a matching opening tag (gpt-4o pattern)."""

    def test_orphaned_close_tag_captured_as_thinking(self, parser):
        raw = "Board analysis: col 4 wins!\n</thinking>\nColumn 4. Let's go."
        result = parser.parse(raw)
        assert result.thinking == "Board analysis: col 4 wins!"
        assert result.public_message == "Column 4. Let's go."
        assert "thinking" in result.tags_found

    def test_orphaned_close_tag_prevents_reasoning_leak(self, parser):
        raw = "If Red plays col 6, it wins!\n</thinking>\nColumn 3. Blocking."
        result = parser.parse(raw)
        assert "wins!" not in result.public_message
        assert result.thinking is not None
        assert "wins!" in result.thinking

    def test_normal_thinking_tag_takes_precedence(self, parser):
        """A well-formed <thinking>...</thinking> is not affected by the fallback."""
        raw = "<thinking>my plan</thinking> Column 5. Done."
        result = parser.parse(raw)
        assert result.thinking == "my plan"
        assert result.public_message == "Column 5. Done."
