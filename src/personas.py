"""
Personality profiles for one-0-one agents.

A PersonalityProfile describes WHO an agent is as a person (stable character
traits) independently of WHAT game role they hold in a session. It is injected
above the game persona in the system prompt so it colors tone and behavior
without overriding game-critical instructions.

Three ways to attach a personality to an agent:
  1. Via roster ID   — personality_id: "the_bold_strategist"
  2. Inline in YAML  — personality: { name: ..., age: ..., big5: ... }
  3. No personality  — omit both fields (Moderator/Narrator roles usually skip this)

Public API:
    from src.personas import load_roster, resolve_personality, build_personality_prompt
"""

from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.session.config import SessionConfig

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_TRAIT_NAMES = ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism")


class Big5Trait(BaseModel):
    score: int = Field(..., ge=0, le=10)
    note: str = ""


class Big5(BaseModel):
    openness: Big5Trait
    conscientiousness: Big5Trait
    extraversion: Big5Trait
    agreeableness: Big5Trait
    neuroticism: Big5Trait


class PersonalityProfile(BaseModel):
    id: str = ""
    name: str
    age: int = Field(..., ge=1, le=120)
    gender: str
    big5: Big5
    tags: list[str] = Field(default_factory=list)
    """Optional role tags, e.g. ['moderator'] for low-drama, high-C profiles."""


class PersonalityRoster(BaseModel):
    profiles: list[PersonalityProfile]

    def get(self, personality_id: str) -> PersonalityProfile | None:
        for p in self.profiles:
            if p.id == personality_id:
                return p
        return None


# ---------------------------------------------------------------------------
# Roster loader
# ---------------------------------------------------------------------------

_DEFAULT_ROSTER_PATH = Path("personas/roster.yaml")


@lru_cache(maxsize=4)
def load_roster(path: Path = _DEFAULT_ROSTER_PATH) -> PersonalityRoster:
    """
    Load and cache the personality roster from YAML.

    The result is cached so the file is only read once per process.
    Pass a different path to override (e.g., in tests).

    Raises:
        FileNotFoundError: if the roster file does not exist.
        pydantic.ValidationError: if the roster fails schema validation.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return PersonalityRoster.model_validate(raw)


def resolve_personality(
    personality_id: str | None,
    inline: "PersonalityProfile | None",
    roster_path: Path = _DEFAULT_ROSTER_PATH,
) -> "PersonalityProfile | None":
    """
    Resolve which PersonalityProfile (if any) applies to an agent.

    Priority:
      1. Inline profile (personality: {...} in agent config) — takes precedence
      2. Roster lookup by personality_id
      3. None if neither is provided
    """
    if inline is not None:
        return inline
    if personality_id:
        roster = load_roster(roster_path)
        profile = roster.get(personality_id)
        if profile is None:
            raise ValueError(
                f"personality_id {personality_id!r} not found in roster at {roster_path}"
            )
        return profile
    return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_TRAIT_LABELS: dict[str, str] = {
    "openness":          "Openness             ",
    "conscientiousness": "Conscientiousness    ",
    "extraversion":      "Extraversion         ",
    "agreeableness":     "Agreeableness        ",
    "neuroticism":       "Neuroticism (volatility)",
}


_ROLES_WITHOUT_PERSONALITY = frozenset(["narrator"])
"""
Roles that never receive a personality profile.

'narrator' is excluded because narrator type (factual vs storytelling) must be
defined per template before personality assignment can be meaningful.
Moderators receive a personality from a curated pool (profiles tagged 'moderator').
"""


def assign_random_personalities(config: "SessionConfig", seed: int) -> "SessionConfig":
    """
    Assign a unique random personality to each eligible agent.

    Pools:
      - Moderator agents (role='moderator') draw from profiles tagged 'moderator'
        (high conscientiousness, low neuroticism, low-drama).
      - All other agents except those in _ROLES_WITHOUT_PERSONALITY draw from the
        remaining profiles after moderator assignments are made.

    Args:
        config: Session configuration (not mutated).
        seed: Random seed for reproducibility (use seconds since 2000-01-01 UTC).

    Returns:
        New SessionConfig with personality_id and personality set on eligible agents.

    Raises:
        ValueError: If there are more moderators than moderator-tagged profiles,
                    or more regular agents than remaining profiles.
    """
    roster = load_roster()
    profiles = roster.profiles
    rng = random.Random(seed)

    # --- Moderator pool (tagged) ---
    mod_pool = [p for p in profiles if "moderator" in p.tags]
    mod_agents = [a for a in config.agents if a.role == "moderator"]
    if len(mod_agents) > len(mod_pool):
        raise ValueError(
            f"Not enough moderator personality profiles: need {len(mod_agents)}, "
            f"have {len(mod_pool)}. Add more 'moderator'-tagged profiles to roster.yaml."
        )
    mod_selected = rng.sample(mod_pool, k=len(mod_agents))
    mod_used_ids = {p.id for p in mod_selected}

    # --- Regular pool (everything not already used by a moderator) ---
    regular_agents = [
        a for a in config.agents
        if a.role not in _ROLES_WITHOUT_PERSONALITY and a.role != "moderator"
    ]
    remaining = [p for p in profiles if p.id not in mod_used_ids]
    if len(regular_agents) > len(remaining):
        raise ValueError(
            f"Not enough personality profiles in roster: need {len(regular_agents)}, "
            f"have {len(remaining)}. Add more profiles to personas/roster.yaml."
        )
    reg_selected = rng.sample(remaining, k=len(regular_agents))

    # Build assignment map
    assignment: dict[str, PersonalityProfile] = {}
    for agent, profile in zip(mod_agents, mod_selected):
        assignment[agent.id] = profile
    for agent, profile in zip(regular_agents, reg_selected):
        assignment[agent.id] = profile

    new_agents = []
    for agent in config.agents:
        if agent.id in assignment:
            profile = assignment[agent.id]
            new_agents.append(agent.model_copy(update={
                "personality_id": profile.id,
                "personality": profile,
            }))
        else:
            new_agents.append(agent)

    return config.model_copy(update={"agents": new_agents})


def build_personality_prompt(profile: PersonalityProfile) -> str:
    """
    Render a PersonalityProfile as a system-prompt block.

    The block is designed to:
      - Give the LLM a vivid, specific character to embody
      - Explicitly frame personality as stylistic color, not mission override
      - Use plain prose + a structured trait list so any LLM can parse it

    Returns a string prepended before the game persona in the system prompt.
    """
    traits = "\n".join(
        f"  {_TRAIT_LABELS[attr]} {getattr(profile.big5, attr).score}/10"
        + (f"  — {getattr(profile.big5, attr).note}" if getattr(profile.big5, attr).note else "")
        for attr in _TRAIT_NAMES
    )

    return (
        f"=== CHARACTER BACKGROUND ===\n"
        f"You are playing as {profile.name}, {profile.age} years old, {profile.gender}.\n"
        f"\n"
        f"Personality (Big Five, scored 0–10 where higher = more of that trait):\n"
        f"{traits}\n"
        f"\n"
        f"Let these traits naturally color your voice, word choice, emotional reactions, "
        f"and reasoning style throughout the session. "
        f"This is who you ARE — your game role and objectives always take priority, "
        f"but personality shapes HOW you pursue them.\n"
        f"=== END CHARACTER BACKGROUND ==="
    )
