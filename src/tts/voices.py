"""
Voice listing and assignment utilities for ElevenLabs TTS.

Voices are assigned via seeded random sampling so the same seed always
produces the same agent→voice mapping — matching the persona assignment
convention used throughout the rest of the engine.
"""

from __future__ import annotations

import random
from typing import Any


def assign_voices(
    agents: list[dict],
    voices: list[Any],
    seed: int,
) -> dict[str, str]:
    """
    Assign a unique ElevenLabs voice to each agent.

    Args:
        agents: List of agent dicts, each with at least an "id" key.
        voices: Available Voice objects from the ElevenLabs API.
        seed:   Random seed — same seed yields the same assignment.

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
    selected = rng.sample(voices, k=len(agents))
    return {agent["id"]: voice.voice_id for agent, voice in zip(agents, selected)}
