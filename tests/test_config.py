"""Tests for session config loading and cross-field validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.session.config import (
    AgentConfig,
    ChannelConfig,
    SessionConfig,
    load_session_config,
)

SESSION_TEMPLATES = Path("session-templates")


class TestLoadSessionTemplates:
    """All 5 bundled session templates must load without error."""

    @pytest.mark.parametrize("template_file", list(SESSION_TEMPLATES.glob("*.yaml")))
    def test_load_template(self, template_file: Path):
        config = load_session_config(template_file)
        assert config.title
        assert config.topic
        assert len(config.agents) >= 1
        assert config.type in {
            "games", "social", "task-completion", "research", "problem-solve"
        }

    def test_game_template_has_game_block(self):
        config = load_session_config(SESSION_TEMPLATES / "game-20-questions.yaml")
        assert config.type == "games"
        assert config.game is not None
        assert config.game.name
        assert len(config.game.rules) > 0

    def test_social_template_has_agents(self):
        config = load_session_config(SESSION_TEMPLATES / "social-ai-opinions.yaml")
        assert config.type == "social"
        assert len(config.agents) >= 2

    def test_research_template_has_orchestrator(self):
        config = load_session_config(SESSION_TEMPLATES / "research-climate-policy.yaml")
        assert config.type == "research"
        assert config.orchestrator is not None


class TestCrossFieldValidation:
    """Cross-field @model_validator constraints."""

    def _minimal_agent(self, agent_id: str = "a1") -> dict:
        return {
            "id": agent_id,
            "name": "Nova",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "role": "participant",
        }

    def _base_config(self, **overrides) -> dict:
        base = {
            "title": "Test",
            "description": "Test session",
            "type": "social",
            "setting": "social",
            "topic": "Test topic",
            "agents": [self._minimal_agent()],
        }
        base.update(overrides)
        return base

    def test_games_type_requires_game_block(self):
        cfg = self._base_config(type="games")
        with pytest.raises(ValidationError, match="requires a 'game' block"):
            SessionConfig.model_validate(cfg)

    def test_valid_config_passes(self):
        cfg = self._base_config()
        config = SessionConfig.model_validate(cfg)
        assert config.title == "Test"

    def test_channel_member_must_be_valid_agent(self):
        cfg = self._base_config(
            channels=[{"id": "team_red", "type": "team", "members": ["nonexistent_agent"]}]
        )
        with pytest.raises(ValidationError, match="nonexistent_agent"):
            SessionConfig.model_validate(cfg)

    def test_valid_team_channel(self):
        cfg = self._base_config(
            agents=[self._minimal_agent("a1"), self._minimal_agent("a2")],
            channels=[{"id": "team_red", "type": "team", "members": ["a1", "a2"]}],
        )
        config = SessionConfig.model_validate(cfg)
        assert config.channels[0].id == "team_red"

    def test_agent_team_must_match_channel(self):
        """Agent.team must reference a declared team channel."""
        cfg = self._base_config(
            agents=[{**self._minimal_agent(), "team": "team_blue"}],
            channels=[{"id": "team_red", "type": "team", "members": ["a1"]}],
        )
        with pytest.raises(ValidationError, match="team_blue"):
            SessionConfig.model_validate(cfg)

    def test_llm_orchestrator_requires_provider_and_model(self):
        cfg = self._base_config(orchestrator={"type": "llm"})
        with pytest.raises(ValidationError, match="requires 'provider' and 'model'"):
            SessionConfig.model_validate(cfg)

    def test_max_turns_optional(self):
        cfg = self._base_config(max_turns=10)
        config = SessionConfig.model_validate(cfg)
        assert config.max_turns == 10

    def test_completion_signal_optional(self):
        cfg = self._base_config(completion_signal="The README is complete")
        config = SessionConfig.model_validate(cfg)
        assert config.completion_signal == "The README is complete"


class TestAgentConfig:
    def test_defaults(self):
        a = AgentConfig(
            id="a1", name="Nova", provider="anthropic",
            model="claude-sonnet-4-6", role="participant"
        )
        assert not a.monologue
        assert a.monologue_mode == "prompt"
        assert a.team is None

    def test_monologue_native(self):
        a = AgentConfig(
            id="a1", name="Nova", provider="anthropic",
            model="claude-sonnet-4-6", role="participant",
            monologue=True, monologue_mode="native",
        )
        assert a.monologue
        assert a.monologue_mode == "native"
