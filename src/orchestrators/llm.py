"""
LLM meta-agent orchestrator.

An LLM model acts as the session orchestrator, receiving full session
state and returning a structured decision about what happens next.

The orchestrator's response is parsed as JSON. If parsing fails, we
fall back to the basic round-robin orchestrator for that turn and log
a warning.

Config (in session template YAML):
    orchestrator:
      type: llm
      provider: anthropic
      model: claude-sonnet-4-6
      persona: "You are a strict debate moderator..."
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from src.logging import get_logger
from src.orchestrators import OrchestratorInput, OrchestratorOutput, RuleViolation
from src.orchestrators.basic import orchestrate as basic_orchestrate

if TYPE_CHECKING:
    from src.session.config import OrchestratorConfig

log = get_logger(__name__)

_ORCHESTRATOR_SYSTEM = """
You are the session orchestrator for a multi-agent conversation platform.

You will receive the current session state (agents, turn number, game state,
and recent messages). Your job is to decide what happens next.

Respond ONLY with valid JSON in this exact format:
{
  "next_agents": ["agent_id_1"],
  "game_state_updates": {},
  "rule_violations": [],
  "session_end": false,
  "end_reason": null
}

Rules:
- "next_agents" must be a list of agent IDs from the session config.
  More than one agent means they speak in parallel.
- "game_state_updates" is a flat key-value dict of game state mutations.
- "rule_violations" is a list of {"agent_id": ..., "rule": ..., "violation_text": ...}.
- Set "session_end": true and "end_reason" when the session should conclude.
- Do not include any explanation outside the JSON object.
"""


class LLMOrchestrator:
    """LLM-backed orchestrator. Uses the provider layer to call any model."""

    def __init__(self, config: "OrchestratorConfig") -> None:
        self._config = config
        self._provider_client = None  # lazy init to avoid import cycles

    def _get_client(self):
        if self._provider_client is None:
            from src.providers.litellm_client import LiteLLMClient
            self._provider_client = LiteLLMClient()
        return self._provider_client

    async def orchestrate(self, input: OrchestratorInput) -> OrchestratorOutput:
        """
        Ask the LLM to decide the next session action.

        Falls back to basic round-robin if the LLM returns malformed output.
        """
        model = f"{self._config.provider}/{self._config.model}"
        system = self._config.persona or _ORCHESTRATOR_SYSTEM
        state_summary = self._serialize_state(input)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": state_summary},
        ]

        log.info("orchestrator.llm_decision", model=model, turn=input.state.turn_number)

        try:
            client = self._get_client()
            result = await client.complete(
                model=model,
                messages=messages,
                temperature=0.2,  # low temperature for deterministic decisions
            )
            return self._parse_response(result.text, input)
        except Exception as exc:
            log.warning(
                "orchestrator.llm_error",
                error=str(exc),
                fallback="basic_round_robin",
            )
            return basic_orchestrate(input)

    def _serialize_state(self, input: OrchestratorInput) -> str:
        """Build a concise state summary for the orchestrator prompt."""
        config = input.config
        state = input.state

        agent_list = [
            {"id": a.id, "name": a.name, "role": a.role}
            for a in config.agents
        ]
        recent_events = [
            {
                "type": e.type,
                "agent_id": getattr(e, "agent_id", None),
                "channel": getattr(e, "channel_id", None),
                "text": (getattr(e, "text", "") or "")[:200],  # truncate
            }
            for e in state.events[-20:]  # last 20 events
            if e.type in ("MESSAGE", "RULE_VIOLATION", "GAME_STATE")
        ]

        payload: dict[str, Any] = {
            "turn_number": state.turn_number,
            "agents": agent_list,
            "game_state": state.game_state.model_dump(),
            "recent_events": recent_events,
            "max_turns": config.max_turns,
            "completion_signal": config.completion_signal,
        }
        return json.dumps(payload, indent=2)

    def _parse_response(
        self, raw: str, input: OrchestratorInput
    ) -> OrchestratorOutput:
        """Parse JSON orchestrator response. Falls back to basic on error."""
        try:
            # Strip markdown code fences if present
            text = raw.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:])
            if text.endswith("```"):
                text = text[: text.rfind("```")]

            data = json.loads(text)
            violations = [
                RuleViolation(
                    agent_id=v["agent_id"],
                    rule=v["rule"],
                    violation_text=v.get("violation_text", ""),
                )
                for v in data.get("rule_violations", [])
            ]
            return OrchestratorOutput(
                next_agents=data.get("next_agents", []),
                game_state_updates=data.get("game_state_updates", {}),
                rule_violations=violations,
                session_end=data.get("session_end", False),
                end_reason=data.get("end_reason"),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning(
                "orchestrator.parse_error",
                error=str(exc),
                raw_snippet=raw[:200],
                fallback="basic_round_robin",
            )
            return basic_orchestrate(input)
