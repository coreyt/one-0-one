"""
ResponseParser — extracts XML routing tags from raw LLM response text.

Agents are instructed to wrap content in tags to route it to the correct
channel. This parser extracts those tags and returns a ParsedResponse.

Supported tags:
    <thinking>...</thinking>       → internal monologue (observer-only)
    <team>...</team>               → team channel message
    <private to="Name">...</private>  → private 1:1 message
    (remaining text)               → public channel message

Rules:
    - Tags are stripped from the text before the remainder is set as public_message.
    - If no tags are present, the full text becomes public_message (safe fallback).
    - Multiple <private> tags are supported; only the first is captured here.
      (Multi-private is a future extension if needed.)
    - Whitespace is stripped from all extracted values.

Usage:
    parser = ResponseParser()
    result = parser.parse(raw_text)
    print(result.public_message)   # text visible to all
    print(result.thinking)         # None or internal reasoning text
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.logging import get_logger

log = get_logger(__name__)

# Compiled patterns for each tag type
_RE_THINKING = re.compile(
    r"<thinking>(.*?)</thinking>", re.DOTALL | re.IGNORECASE
)
_RE_TEAM = re.compile(
    r"<team>(.*?)</team>", re.DOTALL | re.IGNORECASE
)
_RE_PRIVATE = re.compile(
    r'<private\s+to=["\']([^"\']+)["\']>(.*?)</private>',
    re.DOTALL | re.IGNORECASE,
)
_RE_ELIMINATE = re.compile(
    r"<eliminate>(.*?)</eliminate>", re.DOTALL | re.IGNORECASE
)


@dataclass
class ParsedResponse:
    """Structured result of parsing a raw LLM response."""

    thinking: str | None = None
    """Agent internal reasoning. Observer-only — never sent to other agents."""

    team_message: str | None = None
    """Message addressed to the agent's team channel only."""

    private_to: str | None = None
    """Recipient name for a private (1:1) message."""

    private_message: str | None = None
    """Content of the private message."""

    public_message: str = ""
    """Remaining text — visible to all participants on the public channel."""

    eliminated_agents: list[str] = field(default_factory=list)
    """Agent IDs extracted from <eliminate>agent_id</eliminate> tags."""

    tags_found: list[str] = field(default_factory=list)
    """Tag types that were extracted (for logging / debugging)."""


def _strip_name_prefix(text: str, agent_name: str) -> str:
    """
    Strip leading 'AgentName: ' prefix(es) from text.

    Some models echo the speaker's name at the start of their response
    (e.g. "Marco Stone: Marco Stone: I've been watching...").
    This strips all such leading occurrences, case-insensitively.
    """
    prefix = re.compile(r"^" + re.escape(agent_name) + r"\s*:\s*", re.IGNORECASE)
    while True:
        stripped = prefix.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return text


class ResponseParser:
    """Stateless XML tag extractor."""

    def parse(self, raw_text: str, agent_name: str | None = None) -> ParsedResponse:
        """
        Parse a raw LLM response into its channel-routed components.

        Any text not captured by a tag becomes the public message.
        If the model produces no tags at all, the full text is public.
        """
        result = ParsedResponse()
        remainder = raw_text

        # Extract <thinking> block
        m = _RE_THINKING.search(remainder)
        if m:
            result.thinking = m.group(1).strip()
            result.tags_found.append("thinking")
            remainder = _RE_THINKING.sub("", remainder, count=1)
        else:
            # Handle orphaned </thinking> (model emitted closing tag without opening tag).
            # Treat everything before the closing tag as thinking content.
            close_tag = remainder.lower().find("</thinking>")
            if close_tag != -1:
                result.thinking = remainder[:close_tag].strip()
                result.tags_found.append("thinking")
                remainder = remainder[close_tag + len("</thinking>"):]

        # Extract <team> block
        m = _RE_TEAM.search(remainder)
        if m:
            result.team_message = m.group(1).strip()
            result.tags_found.append("team")
            remainder = _RE_TEAM.sub("", remainder, count=1)

        # Extract first <private to="..."> block
        m = _RE_PRIVATE.search(remainder)
        if m:
            result.private_to = m.group(1).strip()
            result.private_message = m.group(2).strip()
            result.tags_found.append("private")
            remainder = _RE_PRIVATE.sub("", remainder, count=1)

        # Extract all <eliminate> tags
        for m in _RE_ELIMINATE.finditer(remainder):
            agent_id = m.group(1).strip()
            if agent_id:
                result.eliminated_agents.append(agent_id)
                if "eliminate" not in result.tags_found:
                    result.tags_found.append("eliminate")
        remainder = _RE_ELIMINATE.sub("", remainder)

        public = remainder.strip()
        if agent_name:
            public = _strip_name_prefix(public, agent_name)
            if result.team_message:
                result.team_message = _strip_name_prefix(result.team_message, agent_name)
            if result.private_message:
                result.private_message = _strip_name_prefix(result.private_message, agent_name)
        result.public_message = public

        if result.tags_found:
            log.debug(
                "parser.tags_found",
                tags=result.tags_found,
                public_len=len(result.public_message),
            )
        else:
            log.debug(
                "parser.fallback",
                reason="no tags found, full text → public",
                text_len=len(raw_text),
            )

        return result
