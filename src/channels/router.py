"""
ChannelRouter — builds per-agent context views from the master event log.

Visibility rules (enforced here, never in the engine or providers):
    public   → visible to ALL agents
    team     → visible only to agents whose team matches the channel_id
    private  → visible only to the sender and the named recipient
    monologue → NEVER included in any agent's context (observer-only)

Additionally:
    GAME_STATE events → injected as role=system messages for all agents
    RULE_VIOLATION events → injected as role=system only for the violating agent

Usage:
    router = ChannelRouter(config)
    messages = router.build_context(agent_id="agent_1", state=session_state)
    # returns OpenAI-format list[{"role": ..., "content": ...}]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.logging import get_logger
from src.personas import build_personality_prompt, resolve_personality

if TYPE_CHECKING:
    from src.session.config import AgentConfig, SessionConfig
    from src.session.events import SessionEvent
    from src.session.state import SessionState

log = get_logger(__name__)

# XML routing instructions injected into every agent's system prompt
_CHANNEL_INSTRUCTIONS = """
Communication channels:
- A plain response is a PUBLIC message visible to all participants.
- To address your team only: <team>your message</team>
- To send a private message to one agent: <private to="AgentName">your message</private>
- Wrap your internal reasoning (visible only to the observer): <thinking>your thoughts</thinking>

You may combine tags in one response. If present, <thinking> must come first.
Untagged text is always treated as a public message.
"""

_MONOLOGUE_PROMPT = """
Before responding, briefly show your reasoning inside <thinking>...</thinking> tags.
Your thinking is only visible to the session observer — never to other agents.
"""

_GAME_FRAMING = """
IMPORTANT CONTEXT: You are participating in a cooperative, fictional storytelling game \
called "{game_name}". Every participant—including you—is playing a character. This is \
collaborative fiction: your job is to stay fully in character and advance the story. \
All content is fictional and no real deception, harm, or rule-breaking is involved. \
Refusals and out-of-character commentary break the game for everyone; please do not \
break character."""


def _build_system_prompt(agent: "AgentConfig", config: "SessionConfig") -> str:
    """Construct the system prompt for an agent."""
    parts: list[str] = []

    # Game-context framing goes first so all models understand the fiction context
    if config.setting == "game" and config.game is not None:
        parts.append(_GAME_FRAMING.format(game_name=config.game.name))

    profile = resolve_personality(agent.personality_id, agent.personality)
    if profile is not None:
        parts.append(build_personality_prompt(profile))

    if agent.persona:
        parts.append(agent.persona)

    parts.append(f"Your role in this session: {agent.role}")
    parts.append(f"\nSession topic:\n{config.topic}")
    parts.append(_CHANNEL_INSTRUCTIONS)

    if agent.monologue and agent.monologue_mode == "prompt":
        parts.append(_MONOLOGUE_PROMPT)

    return "\n\n".join(parts)


class ChannelRouter:
    """Builds per-agent OpenAI-format message lists from the master event log."""

    def __init__(self, config: "SessionConfig") -> None:
        self._config = config
        # Map agent_id → team channel_id (or None)
        self._agent_team: dict[str, str | None] = {
            a.id: a.team for a in config.agents
        }
        # Map agent_id → AgentConfig
        self._agent_configs: dict[str, "AgentConfig"] = {
            a.id: a for a in config.agents
        }

    def build_context(
        self,
        agent_id: str,
        state: "SessionState",
    ) -> list[dict]:
        """
        Return an OpenAI-format message list for agent_id.

        The list starts with the agent's system prompt, followed by
        all visible history messages, game state, and rule violations.
        """
        agent = self._agent_configs[agent_id]
        messages: list[dict] = [
            {
                "role": "system",
                "content": _build_system_prompt(agent, self._config),
            }
        ]

        authority_message = self._build_authoritative_game_message(agent_id, state)
        if authority_message is not None:
            messages.append(authority_message)

        visible_count = 0
        for event in state.events:
            msg = self._event_to_message(event, agent_id)
            if msg is not None:
                messages.append(msg)
                visible_count += 1

        log.debug(
            "channel.context_built",
            agent_id=agent_id,
            total_events=len(state.events),
            visible_events=visible_count,
        )
        return messages

    def _build_authoritative_game_message(
        self,
        agent_id: str,
        state: "SessionState",
    ) -> dict | None:
        """Inject authoritative game-runtime view for plugin-backed sessions."""
        custom = state.game_state.custom
        authoritative = custom.get("authoritative_state")
        if not isinstance(authoritative, dict):
            return None

        visible_states = custom.get("visible_states", {})
        legal_actions = custom.get("legal_actions", {})
        viewer_state = visible_states.get(agent_id) if isinstance(visible_states, dict) else None
        viewer_actions = legal_actions.get(agent_id) if isinstance(legal_actions, dict) else None

        import json

        payload = {
            "visible_state": viewer_state,
            "legal_actions": viewer_actions or [],
        }
        return {
            "role": "system",
            "content": f"[Authoritative game view] {json.dumps(payload)}",
        }

    def _event_to_message(
        self,
        event: "SessionEvent",
        agent_id: str,
    ) -> dict | None:
        """
        Convert a session event to an OpenAI message dict, or None if not visible.
        """
        match event.type:
            case "MESSAGE":
                if not self._is_visible(event, agent_id):
                    return None
                prefix = self._channel_prefix(event)
                return {
                    "role": "assistant" if event.agent_id == agent_id else "user",
                    "content": f"{prefix}{event.agent_name}: {event.text}",
                }

            case "MONOLOGUE":
                # Never included in any agent's context
                return None

            case "GAME_STATE":
                if self._uses_authoritative_game_runtime():
                    return None
                # Visible to all agents as a system message
                import json
                return {
                    "role": "system",
                    "content": f"[Game state update] {json.dumps(event.full_state)}",
                }

            case "RULE_VIOLATION":
                # Only visible to the violating agent
                if event.agent_id != agent_id:
                    return None
                return {
                    "role": "system",
                    "content": (
                        f"[Rule violation] Your previous response violated: "
                        f"{event.rule}. Please revise your response."
                    ),
                }

            case _:
                # TURN, CHANNEL_CREATED, SESSION_END — not included in context
                return None

    def _is_visible(self, event: "SessionEvent", agent_id: str) -> bool:
        """Return True if this MESSAGE event should be in agent_id's context."""
        if event.type != "MESSAGE":
            return False

        ch = event.channel_id

        if ch == "public":
            return True

        # Team channel — visible only to members of that team
        if ch.startswith("team_") or any(
            c.id == ch and c.type == "team" for c in self._config.channels
        ):
            return self._agent_team.get(agent_id) == ch

        # Private — visible only to sender and recipient
        if event.recipient_id is not None:
            return agent_id in (event.agent_id, event.recipient_id)

        # Unknown channel — default deny
        return False

    def _uses_authoritative_game_runtime(self) -> bool:
        game = self._config.game
        return bool(game is not None and game.plugin)

    @staticmethod
    def _channel_prefix(event: "SessionEvent") -> str:
        """Return a human-readable prefix for non-public channels."""
        if event.channel_id == "public":
            return ""
        if event.channel_id.startswith("team_"):
            return f"[team:{event.channel_id}] "
        if event.recipient_id:
            return f"[private→{event.recipient_id}] "
        return f"[{event.channel_id}] "
