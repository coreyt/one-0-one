"""
Orchestrator framework — protocol, input/output models, and loader.

Both Python-function and LLM orchestrators implement the same interface:
    orchestrate(input: OrchestratorInput) -> OrchestratorOutput

Loading a Python orchestrator:
    config = OrchestratorConfig(type="python", module="basic")
    fn = load_orchestrator(config)
    output = fn(OrchestratorInput(config=session_config, state=session_state))

Loading an LLM orchestrator:
    config = OrchestratorConfig(type="llm", provider="anthropic",
                                model="claude-sonnet-4-6")
    fn = load_orchestrator(config)
    output = await fn(OrchestratorInput(...))   # LLM orchestrator is async
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from src.logging import get_logger

if TYPE_CHECKING:
    from src.session.config import OrchestratorConfig, SessionConfig
    from src.session.state import SessionState

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


@dataclass
class RuleViolation:
    agent_id: str
    rule: str
    violation_text: str


@dataclass
class OrchestratorInput:
    config: "SessionConfig"
    state: "SessionState"


@dataclass
class OrchestratorOutput:
    next_agents: list[str] = field(default_factory=list)
    """Agent IDs to speak next. More than one means parallel turn."""

    game_state_updates: dict = field(default_factory=dict)
    """Key-value mutations to apply to game state."""

    rule_violations: list[RuleViolation] = field(default_factory=list)
    """Violations detected in the most recent turn."""

    session_end: bool = False
    """True when the orchestrator determines the session should end."""

    end_reason: str | None = None
    """One of: 'max_turns', 'win_condition', 'completion_signal', 'error'"""

    advance_turns: int = 1
    """How many turn slots this output consumes. Equals len(next_agents) for batched turns."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class OrchestratorProtocol(Protocol):
    """Both sync and async orchestrators satisfy this protocol."""

    def __call__(self, input: OrchestratorInput) -> OrchestratorOutput:
        ...


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_orchestrator(config: "OrchestratorConfig") -> Callable:
    """
    Return a callable orchestrator from the given config.

    For type="python": imports orchestrators.<module> and returns its
    `orchestrate` function. The module must be on sys.path (project root).

    For type="llm": returns a coroutine function wrapping LLMOrchestrator.

    Raises:
        ImportError: if the python module cannot be found
        AttributeError: if the module has no `orchestrate` function
        ValueError: if config.type is unsupported
    """
    if config.type == "python":
        module_name = f"orchestrators.{config.module}"
        log.info("orchestrator.load", type="python", module=module_name)
        module = importlib.import_module(module_name)
        fn = getattr(module, "orchestrate")
        return fn

    if config.type == "llm":
        from src.orchestrators.llm import LLMOrchestrator
        log.info(
            "orchestrator.load",
            type="llm",
            provider=config.provider,
            model=config.model,
        )
        return LLMOrchestrator(config).orchestrate

    raise ValueError(f"Unknown orchestrator type: {config.type!r}")
