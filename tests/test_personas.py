"""Tests for the personas module: models, roster loading, prompt building."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.personas import (
    Big5,
    Big5Trait,
    PersonalityProfile,
    PersonalityRoster,
    assign_random_personalities,
    build_personality_prompt,
    load_roster,
    resolve_personality,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile_dict(profile_id: str = "p1", **overrides) -> dict:
    base = {
        "id": profile_id,
        "name": "Test Person",
        "age": 30,
        "gender": "female",
        "big5": {k: {"score": 5, "note": "neutral"} for k in
                 ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism")},
    }
    base.update(overrides)
    return base


def _make_profile(**kwargs) -> PersonalityProfile:
    return PersonalityProfile.model_validate(_profile_dict(**kwargs))


# ---------------------------------------------------------------------------
# Big5Trait
# ---------------------------------------------------------------------------

class TestBig5Trait:
    def test_valid_score(self):
        t = Big5Trait(score=7, note="some note")
        assert t.score == 7
        assert t.note == "some note"

    def test_score_zero_valid(self):
        assert Big5Trait(score=0).score == 0

    def test_score_ten_valid(self):
        assert Big5Trait(score=10).score == 10

    def test_score_above_ten_raises(self):
        with pytest.raises(ValidationError):
            Big5Trait(score=11, note="")

    def test_score_negative_raises(self):
        with pytest.raises(ValidationError):
            Big5Trait(score=-1, note="")

    def test_note_defaults_to_empty(self):
        assert Big5Trait(score=5).note == ""


# ---------------------------------------------------------------------------
# PersonalityProfile
# ---------------------------------------------------------------------------

class TestPersonalityProfile:
    def test_valid_profile(self):
        p = _make_profile()
        assert p.name == "Test Person"
        assert p.big5.openness.score == 5

    def test_age_zero_raises(self):
        with pytest.raises(ValidationError):
            PersonalityProfile.model_validate(_profile_dict(age=0))

    def test_age_above_120_raises(self):
        with pytest.raises(ValidationError):
            PersonalityProfile.model_validate(_profile_dict(age=121))

    def test_id_defaults_to_empty(self):
        d = _profile_dict()
        del d["id"]
        p = PersonalityProfile.model_validate(d)
        assert p.id == ""

    def test_all_big5_traits_accessible(self):
        p = _make_profile()
        for attr in ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"):
            assert getattr(p.big5, attr).score == 5


# ---------------------------------------------------------------------------
# build_personality_prompt
# ---------------------------------------------------------------------------

class TestBuildPersonalityPrompt:
    def _anxious(self) -> PersonalityProfile:
        return PersonalityProfile.model_validate({
            "id": "p", "name": "Jordan Lee", "age": 34, "gender": "non-binary",
            "big5": {
                "openness":          {"score": 7, "note": "curious mind"},
                "conscientiousness": {"score": 5, "note": "moderate"},
                "extraversion":      {"score": 3, "note": "introverted"},
                "agreeableness":     {"score": 9, "note": "very agreeable"},
                "neuroticism":       {"score": 8, "note": "anxious tendencies"},
            },
        })

    def test_contains_name(self):
        assert "Jordan Lee" in build_personality_prompt(self._anxious())

    def test_contains_age(self):
        assert "34" in build_personality_prompt(self._anxious())

    def test_contains_gender(self):
        assert "non-binary" in build_personality_prompt(self._anxious())

    def test_contains_all_trait_labels(self):
        p = build_personality_prompt(self._anxious())
        for label in ("Openness", "Conscientiousness", "Extraversion", "Agreeableness", "Neuroticism"):
            assert label in p

    def test_contains_trait_scores(self):
        p = build_personality_prompt(self._anxious())
        assert "7/10" in p
        assert "8/10" in p

    def test_contains_trait_notes(self):
        p = build_personality_prompt(self._anxious())
        assert "curious mind" in p
        assert "anxious tendencies" in p

    def test_contains_priority_disclaimer(self):
        p = build_personality_prompt(self._anxious())
        assert "game role" in p.lower() or "take priority" in p.lower()

    def test_has_section_delimiters(self):
        p = build_personality_prompt(self._anxious())
        assert "CHARACTER BACKGROUND" in p
        assert "END CHARACTER BACKGROUND" in p

    def test_returns_string(self):
        assert isinstance(build_personality_prompt(self._anxious()), str)


# ---------------------------------------------------------------------------
# resolve_personality
# ---------------------------------------------------------------------------

class TestResolvePersonality:
    def _roster_yaml(self, tmp_path: Path, profiles: list[dict]) -> Path:
        path = tmp_path / "roster.yaml"
        path.write_text(yaml.dump({"profiles": profiles}))
        load_roster.cache_clear()
        return path

    def test_none_when_both_absent(self):
        assert resolve_personality(None, None) is None

    def test_inline_takes_priority_over_id(self, tmp_path):
        roster = self._roster_yaml(tmp_path, [_profile_dict("roster_id")])
        inline = _make_profile(profile_id="inline_id")
        result = resolve_personality("roster_id", inline, roster_path=roster)
        assert result.id == "inline_id"

    def test_inline_without_id(self):
        inline = _make_profile()
        result = resolve_personality(None, inline)
        assert result is inline

    def test_roster_lookup_by_id(self, tmp_path):
        roster = self._roster_yaml(tmp_path, [_profile_dict("my_id")])
        result = resolve_personality("my_id", None, roster_path=roster)
        assert result is not None
        assert result.id == "my_id"

    def test_unknown_id_raises(self, tmp_path):
        roster = self._roster_yaml(tmp_path, [])
        with pytest.raises(ValueError, match="not found in roster"):
            resolve_personality("nonexistent", None, roster_path=roster)

    def test_empty_string_id_treated_as_none(self):
        # Empty string should not trigger a roster lookup
        result = resolve_personality("", None)
        assert result is None


# ---------------------------------------------------------------------------
# load_roster
# ---------------------------------------------------------------------------

class TestLoadRoster:
    def test_loads_real_roster(self):
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        assert len(roster.profiles) == 42

    def test_all_profiles_have_ids(self):
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        for p in roster.profiles:
            assert p.id, f"Profile {p.name!r} has no id"

    def test_all_scores_in_range(self):
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        for p in roster.profiles:
            for attr in ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"):
                score = getattr(p.big5, attr).score
                assert 0 <= score <= 10, f"{p.id}.{attr} score {score} out of range"

    def test_get_existing_profile(self):
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        p = roster.get("the_bold_strategist")
        assert p is not None
        assert p.name == "Marcus Vane"

    def test_get_missing_returns_none(self):
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        assert roster.get("definitely_not_in_roster") is None

    def test_missing_file_raises(self, tmp_path):
        load_roster.cache_clear()
        with pytest.raises(FileNotFoundError):
            load_roster(tmp_path / "nonexistent.yaml")

    def test_trait_distribution_openness_mean(self):
        """Roster O mean should be approximately 6.0 ± 0.5."""
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        scores = [p.big5.openness.score for p in roster.profiles]
        mean = sum(scores) / len(scores)
        assert 5.5 <= mean <= 6.5, f"Openness mean {mean:.2f} outside expected range"

    def test_trait_distribution_neuroticism_mean(self):
        """Roster N mean should be approximately 4.5 ± 0.5."""
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        scores = [p.big5.neuroticism.score for p in roster.profiles]
        mean = sum(scores) / len(scores)
        assert 4.0 <= mean <= 5.0, f"Neuroticism mean {mean:.2f} outside expected range"

    def test_trait_distribution_agreeableness_mean(self):
        """Roster A mean should be approximately 6.5 ± 0.5."""
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        scores = [p.big5.agreeableness.score for p in roster.profiles]
        mean = sum(scores) / len(scores)
        assert 6.0 <= mean <= 7.0, f"Agreeableness mean {mean:.2f} outside expected range"

    def test_all_ids_unique(self):
        load_roster.cache_clear()
        roster = load_roster(Path("personas/roster.yaml"))
        ids = [p.id for p in roster.profiles]
        assert len(ids) == len(set(ids)), "Duplicate profile IDs found"


# ---------------------------------------------------------------------------
# assign_random_personalities
# ---------------------------------------------------------------------------

def _make_session_config(agent_ids: list[str], moderator_ids: list[str] | None = None) -> "SessionConfig":
    """Build a minimal SessionConfig for testing personality assignment."""
    from src.session.config import AgentConfig, SessionConfig
    agents = []
    for aid in agent_ids:
        role = "moderator" if moderator_ids and aid in moderator_ids else "villager"
        agents.append(AgentConfig(
            id=aid, name=aid, provider="anthropic",
            model="claude-sonnet-4-6", role=role,
        ))
    return SessionConfig(
        title="Test", description="Test", type="social",
        setting="test", topic="Testing", agents=agents,
    )


class TestAssignRandomPersonalities:
    def test_assigns_personalities_to_all_agents_including_moderator(self, tmp_path):
        load_roster.cache_clear()
        config = _make_session_config(
            ["moderator", "agent_1", "agent_2", "agent_3"],
            moderator_ids=["moderator"],
        )
        result = assign_random_personalities(config, seed=42)
        # Moderator now gets a personality from the curated moderator pool
        mod = next(a for a in result.agents if a.id == "moderator")
        assert mod.personality is not None
        assert mod.personality_id is not None
        for aid in ["agent_1", "agent_2", "agent_3"]:
            agent = next(a for a in result.agents if a.id == aid)
            assert agent.personality is not None
            assert agent.personality_id is not None

    def test_moderator_gets_moderator_tagged_profile(self):
        """Moderators must draw from profiles tagged 'moderator'."""
        load_roster.cache_clear()
        config = _make_session_config(["mod"], moderator_ids=["mod"])
        result = assign_random_personalities(config, seed=42)
        mod = next(a for a in result.agents if a.id == "mod")
        assert mod.personality is not None
        assert "moderator" in mod.personality.tags

    def test_narrator_gets_no_personality(self):
        """Narrator role is excluded from personality assignment (type defined per template)."""
        from src.session.config import AgentConfig, SessionConfig
        load_roster.cache_clear()
        narrator = AgentConfig(
            id="nar", name="Narrator", provider="anthropic",
            model="claude-sonnet-4-6", role="narrator",
        )
        config = SessionConfig(
            title="Test", description="Test", type="social",
            setting="test", topic="Testing", agents=[narrator],
        )
        result = assign_random_personalities(config, seed=42)
        nar = next(a for a in result.agents if a.id == "nar")
        assert nar.personality is None

    def test_no_duplicate_personalities(self, tmp_path):
        load_roster.cache_clear()
        config = _make_session_config(["a", "b", "c", "d", "e"])
        result = assign_random_personalities(config, seed=1000)
        ids = [a.personality_id for a in result.agents if a.personality_id]
        assert len(ids) == len(set(ids)), "Duplicate personalities assigned"

    def test_moderator_and_regular_no_duplicate(self):
        """Moderator and regular agents must not share the same personality."""
        load_roster.cache_clear()
        config = _make_session_config(
            ["mod", "agent_1", "agent_2"],
            moderator_ids=["mod"],
        )
        result = assign_random_personalities(config, seed=99)
        all_ids = [a.personality_id for a in result.agents if a.personality_id]
        assert len(all_ids) == len(set(all_ids)), "Duplicate personality across moderator and agents"

    def test_same_seed_same_assignment(self):
        load_roster.cache_clear()
        config = _make_session_config(["x", "y", "z"])
        r1 = assign_random_personalities(config, seed=12345)
        load_roster.cache_clear()
        r2 = assign_random_personalities(config, seed=12345)
        ids1 = [a.personality_id for a in r1.agents]
        ids2 = [a.personality_id for a in r2.agents]
        assert ids1 == ids2

    def test_different_seed_different_assignment(self):
        load_roster.cache_clear()
        config = _make_session_config(["x", "y", "z"])
        r1 = assign_random_personalities(config, seed=1)
        load_roster.cache_clear()
        r2 = assign_random_personalities(config, seed=999999)
        ids1 = [a.personality_id for a in r1.agents]
        ids2 = [a.personality_id for a in r2.agents]
        assert ids1 != ids2

    def test_original_config_not_mutated(self):
        load_roster.cache_clear()
        config = _make_session_config(["a", "b"])
        original_ids = [a.personality_id for a in config.agents]
        assign_random_personalities(config, seed=42)
        assert [a.personality_id for a in config.agents] == original_ids

    def test_too_many_agents_raises(self, tmp_path):
        load_roster.cache_clear()
        # 43 agents (villager) but only 42 profiles total (29 non-moderator-tagged)
        config = _make_session_config([f"agent_{i}" for i in range(43)])
        with pytest.raises(ValueError, match="Not enough personality profiles"):
            assign_random_personalities(config, seed=1)
