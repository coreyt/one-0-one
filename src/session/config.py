"""
Session template configuration models and YAML loader.

SessionConfig is the root model for a session template YAML file.
All fields are validated at load time via Pydantic v2.
Cross-field constraints are enforced by a @model_validator.

Usage:
    from src.session.config import load_session_config
    from pathlib import Path

    config = load_session_config(Path("session-templates/game-20-questions.yaml"))
    print(config.title, config.agents[0].name)
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from src.personas import PersonalityProfile


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    id: str
    name: str
    provider: str
    model: str
    role: str
    persona: str = ""
    team: str | None = None
    monologue: bool = False
    monologue_mode: Literal["prompt", "native"] = "prompt"
    personality_id: str | None = None
    """Reference a named profile in personas/roster.yaml."""
    personality: PersonalityProfile | None = None
    """Inline personality profile. Takes priority over personality_id if both are set."""


class OrchestratorConfig(BaseModel):
    type: Literal["python", "llm"] = "python"
    module: str = "basic"  # for type=python: module name in orchestrators/
    # for type=llm: provider + model + optional persona
    provider: str | None = None
    model: str | None = None
    persona: str = ""

    @model_validator(mode="after")
    def validate_llm_fields(self) -> "OrchestratorConfig":
        if self.type == "llm":
            if not self.provider or not self.model:
                raise ValueError(
                    "orchestrator type 'llm' requires 'provider' and 'model'"
                )
        return self


class ChannelConfig(BaseModel):
    id: str
    type: Literal["public", "team", "private"]
    members: list[str] = Field(default_factory=list)  # agent IDs; empty = all


class HITLConfig(BaseModel):
    enabled: bool = False
    role: str | None = None


class TranscriptConfig(BaseModel):
    auto_save: bool = True
    format: Literal["markdown", "json", "both"] = "both"
    path: Path = Path("./sessions")


class GameRole(BaseModel):
    name: str
    count: int | str  # int or "1-N"
    description: str


class GameModerationConfig(BaseModel):
    class GameModerationFailurePolicy(BaseModel):
        actor_retry_limit: int = 2
        actor_retry_exhaustion_action: Literal[
            "skip_turn", "forfeit", "session_error"
        ] = "forfeit"
        moderator_retry_limit: int = 2
        moderator_retry_exhaustion_action: Literal[
            "skip_turn", "session_error"
        ] = "session_error"

        @model_validator(mode="after")
        def validate_retry_limits(self) -> "GameModerationConfig.GameModerationFailurePolicy":
            if self.actor_retry_limit < 0:
                raise ValueError("actor_retry_limit must be >= 0")
            if self.moderator_retry_limit < 0:
                raise ValueError("moderator_retry_limit must be >= 0")
            return self

    mode: Literal["deterministic", "llm_moderated", "hybrid_audit"] = "deterministic"
    moderator_agent_id: str | None = None
    authority: Literal["hard", "advisory"] = "hard"
    shadow_mode: Literal["deterministic", "llm_moderated"] | None = None
    failure_policy: GameModerationFailurePolicy = Field(
        default_factory=GameModerationFailurePolicy
    )

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> "GameModerationConfig":
        if self.mode in {"llm_moderated", "hybrid_audit"} and not self.moderator_agent_id:
            raise ValueError(
                "game moderation mode requires moderator_agent_id for llm-moderated flows"
            )
        if self.mode == "hybrid_audit" and self.shadow_mode is None:
            self.shadow_mode = "deterministic"
        return self


class LLMDefaults(BaseModel):
    """Session-level LLM defaults. Per-agent overrides planned for later."""
    temperature: float = 0.7
    max_tokens: int | None = None
    thinking_budget: int = 8000
    timeout: int = 30


class GameConfig(BaseModel):
    plugin: str | None = None
    name: str
    description: str = ""
    rules: list[str] = Field(default_factory=list)
    how_to_play: str = ""
    turn_order: str = "round-robin"
    roles: list[GameRole] = Field(default_factory=list)
    win_condition: str = ""
    hitl_compatible: bool = True
    max_rounds: int | None = None
    moderation: GameModerationConfig = Field(default_factory=GameModerationConfig)


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class SessionConfig(BaseModel):
    title: str
    description: str
    type: Literal[
        "games", "social", "task-completion", "research", "problem-solve"
    ]
    setting: str
    topic: str
    orchestrator: OrchestratorConfig = Field(
        default_factory=OrchestratorConfig
    )
    agents: list[AgentConfig]
    channels: list[ChannelConfig] = Field(default_factory=list)
    hitl: HITLConfig = Field(default_factory=HITLConfig)
    transcript: TranscriptConfig = Field(default_factory=TranscriptConfig)
    llm_defaults: LLMDefaults = Field(default_factory=LLMDefaults)
    max_turns: int | None = None
    completion_signal: str | None = None
    game: GameConfig | None = None

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "SessionConfig":
        # Games must supply a game block
        if self.type == "games" and self.game is None:
            raise ValueError("template type 'games' requires a 'game' block")

        # Channel members must reference real agent IDs
        agent_ids = {a.id for a in self.agents}
        for ch in self.channels:
            for member in ch.members:
                if member not in agent_ids:
                    raise ValueError(
                        f"channel '{ch.id}' references member '{member}' "
                        f"who is not in the agents list"
                    )

        # Agent teams must reference a declared team channel
        team_channel_ids = {
            ch.id for ch in self.channels if ch.type == "team"
        }
        for agent in self.agents:
            if agent.team and agent.team not in team_channel_ids:
                raise ValueError(
                    f"agent '{agent.id}' team '{agent.team}' does not match "
                    f"any team channel"
                )

        return self


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_session_config(path: Path) -> SessionConfig:
    """Load and validate a session template YAML file.

    Raises:
        FileNotFoundError: if the path does not exist.
        pydantic.ValidationError: if the YAML fails schema validation.
        yaml.YAMLError: if the file is not valid YAML.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SessionConfig.model_validate(raw)
