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
    from src.games.runtime import GameRuntime
    from src.session.config import AgentConfig, SessionConfig
    from src.session.events import SessionEvent
    from src.session.state import SessionState

log = get_logger(__name__)

# How many recent events to send to a moderator in engine-authoritative games.
# Players receive the game-move journal instead of event history.
_MODERATOR_HISTORY_WINDOW = 20


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

_PLUGIN_GAME_FRAMING = """
IMPORTANT CONTEXT: You are playing a deterministic rules-based game called "{game_name}".
The authoritative game view in the system messages is the source of truth for board state,
turn order, legal actions, and win conditions. Focus on making the best legal move from
that state. Do not invent state, rules, identities, or narration that conflicts with the
authoritative game view."""

_PLUGIN_GAME_ACTION_DISCIPLINE = """
For plugin-backed games:
- Treat the authoritative game view as canonical, even if prior chat conflicts with it.
- Ignore meta-confusion about names, roles, or story.
- Focus on gameplay only.
- If your game prompt gives you a structured response contract, follow it exactly."""

_LLM_AUTHORITY_GAME_FRAMING = """
IMPORTANT CONTEXT: You are participating in a moderator-run game called "{game_name}".
The moderator/referee is responsible for adjudication and state progression. Stay in role,
follow the game rules, and do not assume there is an engine-owned authoritative board unless
the moderator explicitly establishes one in the conversation."""


def _make_system_message(text: str, provider: str) -> dict:
    """Return an OpenAI-format system message with provider-appropriate caching markers.

    Anthropic: wraps content in a structured block with ``cache_control`` so the
    static system prompt is cached across turns by the Anthropic API, cutting
    per-call prompt costs on long games.

    Google/Gemini: uses a separate Context Cache API — inline ``cache_control``
    blocks are not accepted and would cause API errors.  Content stays a plain string.

    OpenAI and others: automatic prefix caching requires no explicit marker.
    """
    if provider == "anthropic":
        return {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    return {"role": "system", "content": text}


def _build_system_prompt(agent: "AgentConfig", config: "SessionConfig") -> str:
    """Construct the system prompt for an agent."""
    parts: list[str] = []

    # Game-context framing goes first so all models understand the fiction context
    if config.setting == "game" and config.game is not None:
        if config.game.authority_mode == "engine_authoritative":
            parts.append(_PLUGIN_GAME_FRAMING.format(game_name=config.game.name))
            parts.append(_PLUGIN_GAME_ACTION_DISCIPLINE)
        else:
            parts.append(_LLM_AUTHORITY_GAME_FRAMING.format(game_name=config.game.name))

    profile = resolve_personality(agent.personality_id, agent.personality)
    if profile is not None:
        parts.append(build_personality_prompt(profile))

    if agent.persona:
        parts.append(agent.persona)

    # Explicit identity — for games the model must know its own name and player id
    # so it never confuses itself with another participant.
    if config.setting == "game" and config.game is not None:
        parts.append(
            f"You are {agent.name}. "
            f'Your player identifier in this game is "{agent.id}". '
            f"Your role: {agent.role}."
        )
    else:
        parts.append(f"Your role in this session: {agent.role}")

    parts.append(f"\nSession topic:\n{config.topic}")

    # Game rules and how-to-play are static context (not per-turn game state).
    # Injecting them here means every turn the model has the authoritative ruleset,
    # and they are eligible for prompt caching on providers that support it.
    if config.setting == "game" and config.game is not None:
        if config.game.rules:
            rules_text = "\n".join(f"- {rule}" for rule in config.game.rules)
            parts.append(f"Game rules:\n{rules_text}")
        if config.game.how_to_play:
            parts.append(f"How to play:\n{config.game.how_to_play.strip()}")

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
        # Set by the engine after the game runtime is constructed.
        # None for non-authoritative (llm-authority) sessions.
        self.game_runtime: "GameRuntime | None" = None

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
            _make_system_message(
                _build_system_prompt(agent, self._config),
                agent.provider,
            )
        ]

        authority_message = self._build_authoritative_game_message(agent_id, state)
        if authority_message is not None:
            messages.append(authority_message)

        # For engine-authoritative games the injected game message already
        # contains complete, current state.  Players get the journal instead of
        # the full chat history; moderators get a recent-events window.
        is_engine_auth = (
            self._config.game is not None
            and self._config.game.authority_mode == "engine_authoritative"
        )
        if is_engine_auth and agent.role != "moderator":
            # Players: journal only — no chat history needed.
            log.debug(
                "channel.context_built",
                agent_id=agent_id,
                total_events=len(state.events),
                visible_events=0,
                mode="journal_only",
            )
            return messages

        events_to_scan = state.events
        if is_engine_auth and agent.role == "moderator":
            # Moderators: only the most recent window so they can narrate
            # without re-reading hundreds of old turns.
            events_to_scan = state.events[-_MODERATOR_HISTORY_WINDOW:]

        visible_count = 0
        for event in events_to_scan:
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
        """Inject authoritative game-runtime view for plugin-backed sessions.

        Delegates all game-specific rendering to the game plugin via
        GameRuntime.render_agent_context, then assembles the system message
        with shared boilerplate (response schema hint, "Return exactly one
        JSON object…").  Zero branching on game_type here.
        """
        if self.game_runtime is None:
            return None
        agent = self._agent_configs[agent_id]
        ctx = self.game_runtime.render_agent_context(
            viewer_id=agent_id,
            role=agent.role,
            game_config=self._config.game,
        )
        details = ["[Authoritative game view]"]
        details.extend(ctx.instructions)
        details.extend(ctx.state_lines)
        if ctx.response_schema is not None:
            details.append(f"response_schema={ctx.response_schema}")
            details.append(f"response_example={ctx.response_example}")
            details.append("Return exactly one JSON object and no surrounding prose.")
        return {"role": "system", "content": "\n".join(details)}

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
        return bool(
            game is not None
            and game.authority_mode == "engine_authoritative"
            and game.plugin
        )

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
