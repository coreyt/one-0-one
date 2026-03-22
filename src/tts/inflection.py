"""
Feeling registry and text renderer for ElevenLabs speech inflection.

LLMs tag their speech with <feeling>NAME</feeling> markers. This module:
  1. Strips those tags to produce clean text shown to other agents.
  2. Converts them to ElevenLabs [audio_tag] syntax for eleven_v3 (post-game batch).
  3. Aggregates voice_settings overrides (stability / style) for eleven_flash_v2_5
     (near real-time streaming, which does not support inline audio tags).

Markup reference:
  eleven_v3         — [tag] square-bracket audio events, embedded inline in text.
  eleven_flash_v2_5 — voice_settings only (stability 0-1, style 0-1).

Registry design:
  scope "external" — feelings observable in spoken delivery (public / team speech).
  scope "internal" — feelings for internal monologue only.
  scope "both"     — valid in either context.

voice_settings aggregation across multiple feelings in one turn:
  stability → min (most expressive / unstable reading wins)
  style     → max (most stylised reading wins)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Inflection:
    """One entry in the feeling registry."""

    label: str
    """Human-readable name shown in LLM prompt instructions."""

    scope: Literal["external", "internal", "both"]
    """Where this feeling may be used."""

    v3_tag: str | None = None
    """ElevenLabs eleven_v3 audio-event tag (without brackets).
    Inserted inline as [tag] at the position of the <feeling> marker.
    None means no audio-event tag — voice_settings only."""

    stability: float | None = None
    """ElevenLabs voice_settings.stability override (0-1).
    Lower = more expressive/variable. None = use model default."""

    style: float | None = None
    """ElevenLabs voice_settings.style override (0-1).
    Higher = more stylised. None = use model default."""


@dataclass
class ProcessedText:
    """Result of running raw tagged text through process_text()."""

    clean: str
    """Feeling tags removed — shown to other agents and stored in MessageEvent.text."""

    v3_annotated: str
    """Feeling tags converted to [audio_tag] for eleven_v3 batch rendering.
    Falls back to clean when no v3_tag is defined for a feeling."""

    voice_settings: dict[str, float] = field(default_factory=dict)
    """Aggregated voice_settings override for streaming (eleven_flash_v2_5).
    Keys: "stability", "style" — only present when at least one feeling defines them."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


REGISTRY: dict[str, Inflection] = {
    # ── External feelings (observable in spoken delivery) ─────────────────
    "excited":    Inflection("excited",    "external", v3_tag="excited",    stability=0.40, style=0.70),
    "angry":      Inflection("angry",      "external", v3_tag="angry",      stability=0.30, style=0.80),
    "sad":        Inflection("sad",        "external", v3_tag="sad",        stability=0.65, style=0.40),
    "nervous":    Inflection("nervous",    "external", v3_tag="nervous",    stability=0.25, style=0.50),
    "surprised":  Inflection("surprised",  "external", v3_tag="gasps",      stability=0.30, style=0.60),
    "amused":     Inflection("amused",     "external", v3_tag="laughs",     stability=0.50, style=0.50),
    "suspicious": Inflection("suspicious", "external", v3_tag="curious",    stability=0.60, style=0.45),
    "confident":  Inflection("confident",  "external", v3_tag=None,         stability=0.85, style=0.35),
    "desperate":  Inflection("desperate",  "external", v3_tag="frustrated", stability=0.20, style=0.80),
    "apologetic": Inflection("apologetic", "external", v3_tag="sighs",      stability=0.55, style=0.30),
    "taunting":   Inflection("taunting",   "external", v3_tag="sarcastic",  stability=0.50, style=0.70),
    "resigned":   Inflection("resigned",   "external", v3_tag="tired",      stability=0.80, style=0.20),
    "whispering": Inflection("whispering", "external", v3_tag="whispering", stability=0.70, style=0.30),

    # ── Internal feelings (monologue / private state) ──────────────────────
    "calculating": Inflection("calculating", "internal", v3_tag=None,       stability=0.85, style=0.20),
    "paranoid":    Inflection("paranoid",    "internal", v3_tag="nervous",  stability=0.20, style=0.60),
    "relieved":    Inflection("relieved",    "internal", v3_tag="sighs",    stability=0.70, style=0.30),
    "conflicted":  Inflection("conflicted",  "internal", v3_tag=None,       stability=0.35, style=0.40),
    "determined":  Inflection("determined",  "internal", v3_tag=None,       stability=0.85, style=0.60),
    "hopeful":     Inflection("hopeful",     "internal", v3_tag="excited",  stability=0.50, style=0.50),
    "despairing":  Inflection("despairing",  "internal", v3_tag="crying",   stability=0.20, style=0.70),
    "triumphant":  Inflection("triumphant",  "internal", v3_tag="laughs",   stability=0.50, style=0.80),
    "cunning":     Inflection("cunning",     "internal", v3_tag="sarcastic", stability=0.65, style=0.60),
}

EXTERNAL_FEELINGS: list[str] = sorted(
    name for name, inf in REGISTRY.items() if inf.scope in ("external", "both")
)
INTERNAL_FEELINGS: list[str] = sorted(
    name for name, inf in REGISTRY.items() if inf.scope in ("internal", "both")
)


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------


_FEELING_RE = re.compile(r"<feeling>([^<]+)</feeling>", re.IGNORECASE)
_MULTI_SPACE = re.compile(r" {2,}")


def strip_feeling_tags(text: str) -> str:
    """Remove all <feeling>…</feeling> tags; collapse any resulting double spaces."""
    result = _FEELING_RE.sub("", text)
    return _MULTI_SPACE.sub(" ", result).strip()


def process_text(raw: str) -> ProcessedText:
    """
    Parse feeling tags from raw agent text and produce three representations.

    Returns:
        ProcessedText with:
          .clean         — tags stripped (for other agents and transcript display)
          .v3_annotated  — tags converted to [audio_tag] (for eleven_v3 batch TTS)
          .voice_settings — aggregated stability/style (for eleven_flash_v2_5 streaming)
    """
    found: list[Inflection | None] = []

    def _to_v3(m: re.Match) -> str:
        name = m.group(1).strip().lower()
        inf = REGISTRY.get(name)
        found.append(inf)
        if inf and inf.v3_tag:
            return f"[{inf.v3_tag}]"
        return ""

    v3_annotated = _FEELING_RE.sub(_to_v3, raw)
    clean = _FEELING_RE.sub("", raw)

    # Normalise whitespace
    v3_annotated = _MULTI_SPACE.sub(" ", v3_annotated).strip()
    clean = _MULTI_SPACE.sub(" ", clean).strip()

    # Aggregate voice_settings from known feelings
    known = [inf for inf in found if inf is not None]
    voice_settings: dict[str, float] = {}
    if known:
        stabilities = [inf.stability for inf in known if inf.stability is not None]
        styles = [inf.style for inf in known if inf.style is not None]
        if stabilities:
            voice_settings["stability"] = min(stabilities)   # most expressive wins
        if styles:
            voice_settings["style"] = max(styles)            # most stylised wins

    return ProcessedText(clean=clean, v3_annotated=v3_annotated, voice_settings=voice_settings)


# ---------------------------------------------------------------------------
# LLM prompt instructions
# ---------------------------------------------------------------------------


def feeling_instructions() -> str:
    """
    Return the system prompt section injected into every agent's prompt.

    Tells agents how to use <feeling> tags, lists valid names by scope,
    and explains that the tags are stripped before other players see the text.
    """
    ext_list = ", ".join(EXTERNAL_FEELINGS)
    int_list = ", ".join(INTERNAL_FEELINGS)

    return f"""\
## Emotional Expression

You may annotate your speech and thoughts with feeling tags to convey emotional state.
These tags shape how your voice sounds in audio playback. Other participants never see
the tags — they hear the emotion in your delivery.

**Format:** `<feeling>NAME</feeling>` — place it where the emotion begins.
A tag colours the delivery of everything that follows, until the next tag or end of turn.

**External feelings** (use in spoken messages — public, team, or private):
{ext_list}

**Internal feelings** (use inside <thinking>…</thinking> monologue only):
{int_list}

**Examples:**

Public speech:
  "I believe we need to act now. <feeling>nervous</feeling> Though I could be wrong about this."

Monologue:
  "<thinking><feeling>calculating</feeling> If I stay quiet here, I learn more than I reveal.</thinking>"

Only use feeling names from the lists above; unrecognised tags are silently removed.\
"""
