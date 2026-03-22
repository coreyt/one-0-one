"""
Voice listing and assignment utilities for ElevenLabs TTS.

Voices are assigned via persona-aware scoring then seeded random tie-breaking,
so sessions with the same seed and personas produce deterministic agent→voice
mappings — matching the persona assignment convention throughout the engine.

Matching priority (highest → lowest):
  1. Gender (male/female) — 10 points for match
  2. Age bracket (young/middle_aged/old) — 5 points for match
  3. Big-Five extraversion → use_case preference — 3 points for match
       high extraversion (≥7): conversational / social_media / entertainment_tv
       low  extraversion (≤3): informative_educational / narrative_story

Falls back gracefully through each tier when no exact-match pool is available.
"""

from __future__ import annotations

import random
from typing import Any


# ElevenLabs label values used for scoring
_GENDER_LABELS = {"male", "female"}   # "neutral" voices have no gender filter

_AGE_BRACKETS: dict[str, tuple[int, int]] = {
    "young":       (0,  34),
    "middle_aged": (35, 55),
    "old":         (56, 999),
}

_HIGH_EXTRAVERSION_USE_CASES = frozenset({
    "conversational", "social_media", "entertainment_tv", "characters_animation",
})
_LOW_EXTRAVERSION_USE_CASES = frozenset({
    "informative_educational", "narrative_story",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _age_bracket(age: int | float | None) -> str | None:
    if age is None:
        return None
    for bracket, (lo, hi) in _AGE_BRACKETS.items():
        if lo <= int(age) <= hi:
            return bracket
    return None


def _extraversion_score(big5: dict) -> int | None:
    entry = big5.get("extraversion")
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("score")
    if isinstance(entry, (int, float)):
        return int(entry)
    return None


def _score_voice(
    voice: Any,
    gender: str | None,
    age_bracket: str | None,
    preferred_use_cases: frozenset[str] | None,
) -> int:
    """Score a voice against persona attributes. Higher is a better match."""
    labels: dict = getattr(voice, "labels", None) or {}
    score = 0
    if gender in _GENDER_LABELS and labels.get("gender") == gender:
        score += 10
    if age_bracket and labels.get("age") == age_bracket:
        score += 5
    if preferred_use_cases and labels.get("use_case") in preferred_use_cases:
        score += 3
    return score


def _extract_persona(agent: dict) -> dict | None:
    """Extract persona attributes from a serialized agent dict, if present."""
    p = agent.get("personality")
    if not p:
        return None
    return {
        "gender": p.get("gender"),
        "age":    p.get("age"),
        "big5":   p.get("big5") or {},
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def match_voice_for_agent(
    agent: dict,
    available_voices: list[Any],
    rng: random.Random,
) -> Any:
    """
    Select the best-matching voice for one agent from the available pool.

    Among voices with equal top score, one is chosen at random (seeded) to
    prevent always picking the same voice when many agents share attributes.

    Args:
        agent:            Agent dict with optional "personality" key.
        available_voices: Voices not yet assigned to another agent.
        rng:              Seeded RNG for deterministic tie-breaking.

    Returns:
        A Voice object from available_voices.

    Raises:
        ValueError: If available_voices is empty.
    """
    if not available_voices:
        raise ValueError("No voices remaining for assignment")

    persona = _extract_persona(agent)
    if persona is None:
        return rng.choice(available_voices)

    gender = persona.get("gender")
    el_gender = gender if gender in _GENDER_LABELS else None
    age_bracket = _age_bracket(persona.get("age"))

    big5 = persona.get("big5") or {}
    ext = _extraversion_score(big5)
    preferred_use_cases: frozenset[str] | None = None
    if ext is not None:
        if ext >= 7:
            preferred_use_cases = _HIGH_EXTRAVERSION_USE_CASES
        elif ext <= 3:
            preferred_use_cases = _LOW_EXTRAVERSION_USE_CASES

    scored = sorted(
        available_voices,
        key=lambda v: _score_voice(v, el_gender, age_bracket, preferred_use_cases),
        reverse=True,
    )
    top_score = _score_voice(scored[0], el_gender, age_bracket, preferred_use_cases)
    top_tier = [
        v for v in scored
        if _score_voice(v, el_gender, age_bracket, preferred_use_cases) == top_score
    ]
    return rng.choice(top_tier)


def assign_voices(
    agents: list[dict],
    voices: list[Any],
    seed: int,
) -> dict[str, str]:
    """
    Assign a unique ElevenLabs voice to each agent using persona-aware scoring.

    Agents with an explicit "voice_id" field bypass scoring and use that voice
    directly (the voice is still removed from the available pool so no other
    agent can be assigned it).

    Args:
        agents: List of agent dicts, each with at least an "id" key.
                Optional "voice_id" key pins a specific ElevenLabs voice_id.
                Optional "personality" key enables persona-aware matching.
        voices: Available Voice objects from the ElevenLabs API.
        seed:   Random seed — same seed + same persona data = same mapping.

    Returns:
        Dict mapping agent_id → voice_id.

    Raises:
        ValueError: If there are more agents than available voices.
    """
    if len(agents) > len(voices):
        raise ValueError(
            f"Not enough voices available: need {len(agents)}, "
            f"have {len(voices)}. Check your ElevenLabs account voice library."
        )

    rng = random.Random(seed)
    available = list(voices)
    result: dict[str, str] = {}

    for agent in agents:
        agent_id = agent["id"]
        pinned_voice_id = agent.get("voice_id")

        if pinned_voice_id:
            # Explicit override — use directly, remove from pool if present
            result[agent_id] = pinned_voice_id
            available = [v for v in available if v.voice_id != pinned_voice_id]
        else:
            chosen = match_voice_for_agent(agent, available, rng)
            result[agent_id] = chosen.voice_id
            available.remove(chosen)

    return result
