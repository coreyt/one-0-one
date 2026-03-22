"""
Radio script compiler — transforms a raw session transcript into a curated
list of RadioLine objects suitable for dramatic audio playback.

Transformation rules (applied in order):
  1. JSON-only MESSAGE events → drop (raw action payloads, not speech).
  2. game_engine MESSAGE events → drop (engine system strings; Narrator covers these).
  3. MONOLOGUE from Narrator/moderator agents → drop (production notes).
  4. MONOLOGUE from player agents → "aside", first sentence only, tags stripped.
  5. Aside overlaps >55% with next same-agent public MESSAGE → drop (bleed).
  6. Public MESSAGE events → "speech", residual <thinking>/<feeling> tags stripped.
  7. Private/team MESSAGE events → drop (not part of the public radio script).
  8. GAME_STATE phase transitions → inject synthetic "narration" header.
     Consecutive same-phase GAME_STATE events produce only one header.

The result is an ordered list of RadioLine objects representing the final
radio play script: narration headers, character asides, and spoken lines.

Usage:
    from src.tts.script_compiler import compile_radio_script, RadioLine

    script = compile_radio_script(transcript)
    for line in script:
        print(f"[{line.delivery}] {line.speaker}: {line.text}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from src.tts.inflection import strip_feeling_tags

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class RadioLine:
    """One curated line in the radio play script."""

    speaker: str
    """Agent name for voice lookup (Narrator for narration headers)."""

    agent_id: str
    """Agent identifier used for voice assignment."""

    delivery: Literal["narration", "aside", "speech"]
    """How this line is delivered:
      narration — Narrator reads a phase transition header.
      aside     — Character internal thought (whispered / parenthetical).
      speech    — Character spoken dialogue.
    """

    text: str
    """Clean, TTS-ready text (no XML tags)."""

    tts_text: str | None
    """ElevenLabs eleven_v3 annotated text, or None if identical to text."""

    voice_settings: dict[str, float] = field(default_factory=dict)
    """voice_settings overrides from feeling tags (stability, style)."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLEED_THRESHOLD = 0.55
"""Monologue → speech overlap ratio above which the aside is dropped."""

_NARRATOR_IDS = {"narrator", "moderator", "game_engine"}
"""agent_id values whose MONOLOGUE is treated as production notes, not drama."""

_UNCLOSED_THINKING_RE = re.compile(r"<thinking>.*", re.DOTALL | re.IGNORECASE)
"""Matches an opening <thinking> tag with no closing tag — strips to end of string."""

_PHASE_LABELS: dict[str, str] = {
    "night_mafia_discussion": "Night — the Mafia gathers in the shadows.",
    "night_mafia_vote": "Night — the Mafia casts their vote.",
    "night_detective": "Night — the Detective makes her move.",
    "night_doctor": "Night — the Doctor chooses who to protect.",
    "day_discussion": "Morning — the village wakes to uncertainty.",
    "day_vote": "Day — the town casts their votes.",
    "day_result": "The votes are counted.",
    "night_result": "Dawn breaks on a changed village.",
    "game_over": "The game is over.",
}

_DEFAULT_PHASE_LABEL = "The scene shifts."

_JSON_RE = re.compile(r"^\s*\{.*\}\s*$", re.DOTALL)
_THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_NARRATOR_AGENT_ID = "narrator"
_NARRATOR_NAME = "Narrator"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clean_monologue(text: str) -> str:
    """Strip <thinking> and <feeling> tags from monologue text."""
    text = _THINKING_RE.sub(lambda m: m.group(0)[len("<thinking>"):-len("</thinking>")], text)
    text = strip_feeling_tags(text)
    return text.strip()


def _clean_speech(text: str) -> str:
    """Strip residual <thinking> and <feeling> tags from speech text.

    Handles both closed tags (<thinking>…</thinking>) and orphaned opening
    tags where the model never emitted a closing tag (strips from <thinking>
    to end of string).
    """
    text = _THINKING_RE.sub("", text)            # closed <thinking>…</thinking>
    text = _UNCLOSED_THINKING_RE.sub("", text)   # unclosed <thinking>…EOF
    text = strip_feeling_tags(text)
    return re.sub(r" {2,}", " ", text).strip()


def _first_sentence(text: str) -> str:
    """Return only the first sentence of text.

    Splits on the first sentence-ending punctuation (. ! ?) followed by
    whitespace, OR on the first newline — whichever comes first. This
    ensures that multi-line bullet-list monologues are truncated to their
    opening line rather than being returned in full.
    """
    text = text.strip()
    m = re.search(r"(?<=[.!?])[ \t]+|\n", text)
    if m:
        return text[: m.start()].strip()
    return text


def _overlap_ratio(a: str, b: str) -> float:
    """
    Fraction of unique words in `a` that also appear in `b`.

    Used for bleed detection: if the monologue covers most of the same ground
    as the following speech, the aside is redundant and should be dropped.
    """
    words_a = set(a.lower().split())
    if not words_a:
        return 0.0
    words_b = set(b.lower().split())
    return len(words_a & words_b) / len(words_a)


def _phase_from_game_state(event: dict) -> str | None:
    """Extract phase string from a GAME_STATE event dict."""
    # Try full_state.custom.authoritative_state.phase first (most reliable)
    try:
        return event["full_state"]["custom"]["authoritative_state"]["phase"]
    except (KeyError, TypeError):
        pass
    # Fall back to updates.phase
    return event.get("updates", {}).get("phase")


def _phase_header(phase: str, round_number: int) -> str:
    """Build a human-readable narration header for a phase."""
    label = _PHASE_LABELS.get(phase, _DEFAULT_PHASE_LABEL)
    # Append round number for phases that recur each round
    recurring = {"night_mafia_discussion", "night_mafia_vote", "night_detective",
                 "night_doctor", "day_discussion", "day_vote"}
    if phase in recurring and round_number > 1:
        ordinals = {2: "Two", 3: "Three", 4: "Four", 5: "Five",
                    6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}
        round_label = ordinals.get(round_number, str(round_number))
        # Replace "Night —" / "Morning —" / "Day —" prefix with ordinal version
        label = re.sub(r"^(Night|Morning|Day)\b", rf"\1 {round_label}", label)
    return label


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compile_radio_script(
    transcript: dict,
    channels: list[str] | None = None,
) -> list[RadioLine]:
    """
    Transform a session transcript dict into a curated list of RadioLine objects.

    Args:
        transcript: Parsed session JSON (title, agents, events, …).
        channels:   Public channels to include. Defaults to ["public"].

    Returns:
        Ordered list of RadioLine objects representing the radio play script.
    """
    if channels is None:
        channels = ["public"]

    events: list[dict] = transcript.get("events", [])

    # Pass 1 — collect asides with their position so we can do bleed detection.
    # We'll index events that are MONOLOGUE by position, then in Pass 2 find
    # the next same-agent public MESSAGE and check overlap.
    pending_asides: dict[int, dict] = {}  # index → monologue event
    for i, e in enumerate(events):
        if e["type"] != "MONOLOGUE":
            continue
        agent_id = e.get("agent_id", "")
        if agent_id in _NARRATOR_IDS:
            continue
        pending_asides[i] = e

    # For each pending aside, find the next public MESSAGE from the same agent.
    aside_next_speech: dict[int, str] = {}  # aside_idx → speech text
    for aside_idx, aside_e in pending_asides.items():
        agent_id = aside_e.get("agent_id", "")
        for j in range(aside_idx + 1, len(events)):
            ne = events[j]
            if (
                ne["type"] == "MESSAGE"
                and ne.get("agent_id") == agent_id
                and ne.get("channel_id") in channels
            ):
                aside_next_speech[aside_idx] = ne.get("text", "")
                break

    # Pass 2 — build RadioLine list in event order.
    lines: list[RadioLine] = []
    last_phase: str | None = None
    last_round: int | None = None

    for i, event in enumerate(events):
        etype = event["type"]

        # ── Phase transition header ──────────────────────────────────────
        if etype == "GAME_STATE":
            phase = _phase_from_game_state(event)
            if phase is None:
                continue
            round_number = (
                event.get("full_state", {}).get("round")
                or event.get("full_state", {}).get("custom", {})
                    .get("authoritative_state", {}).get("round_number")
                or 1
            )
            if phase == last_phase and round_number == last_round:
                continue  # same phase, no new header
            last_phase = phase
            last_round = round_number
            header_text = _phase_header(phase, round_number)
            lines.append(RadioLine(
                speaker=_NARRATOR_NAME,
                agent_id=_NARRATOR_AGENT_ID,
                delivery="narration",
                text=header_text,
                tts_text=None,
                voice_settings={},
            ))
            continue

        # ── MONOLOGUE ─────────────────────────────────────────────────────
        if etype == "MONOLOGUE":
            agent_id = event.get("agent_id", "")
            if agent_id in _NARRATOR_IDS:
                continue  # Rule 3: narrator/moderator production notes

            if i not in pending_asides:
                continue  # already filtered above (defensive)

            # Clean and truncate to first sentence
            raw = event.get("text", "")
            cleaned = _clean_monologue(raw)
            first = _first_sentence(cleaned)
            if not first:
                continue

            # Bleed detection: drop if overlap with next same-agent speech > threshold
            next_speech = aside_next_speech.get(i, "")
            if next_speech and _overlap_ratio(first, next_speech) > _BLEED_THRESHOLD:
                continue  # Rule 5: aside is redundant

            lines.append(RadioLine(
                speaker=event.get("agent_name", agent_id),
                agent_id=agent_id,
                delivery="aside",
                text=first,
                tts_text=None,  # asides don't carry eleven_v3 annotations
                voice_settings={},
            ))
            continue

        # ── MESSAGE ───────────────────────────────────────────────────────
        if etype == "MESSAGE":
            agent_id = event.get("agent_id", "")
            channel_id = event.get("channel_id", "")
            text = event.get("text", "")

            # Rule 2: drop game_engine messages
            if agent_id == "game_engine":
                continue

            # Rule 7: drop non-public channels
            if channel_id not in channels:
                continue

            # Rule 1: drop JSON-only messages
            if _JSON_RE.match(text):
                continue

            # Clean speech text
            clean = _clean_speech(text)
            if not clean:
                continue

            tts_text = event.get("tts_text") or None
            voice_settings: dict[str, float] = event.get("tts_voice_settings") or {}

            lines.append(RadioLine(
                speaker=event.get("agent_name", agent_id),
                agent_id=agent_id,
                delivery="speech",
                text=clean,
                tts_text=tts_text,
                voice_settings=dict(voice_settings),
            ))
            continue

    return lines
