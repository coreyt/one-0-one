"""
Transcript → MP3 renderer using ElevenLabs.

Workflow:
  1. Load transcript JSON from disk.
  2. Extract public MESSAGE events into an ordered script.
  3. Assign one unique ElevenLabs voice per speaking agent (seeded random).
  4. If ≤10 unique speakers: use text_to_dialogue (seamless multi-speaker audio).
     If >10 unique speakers: fall back to per-turn text_to_speech + concatenation.
  5. Write MP3 bytes to output_path and return it.

Usage:
    from src.tts.renderer import render_mp3
    from pathlib import Path

    output = render_mp3(Path("sessions/mafia_game_20260223.json"))
    print(f"Saved to {output}")
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from src.logging import get_logger
from src.settings import settings
from src.tts.voices import assign_voices

log = get_logger(__name__)

# ElevenLabs model and format constants
_DIALOGUE_MODEL = "eleven_v3"          # required by text_to_dialogue
_FALLBACK_MODEL = "eleven_multilingual_v2"  # used for per-turn TTS (>10 speakers)
_OUTPUT_FORMAT = "mp3_44100_128"
_MAX_DIALOGUE_VOICES = 10  # ElevenLabs text_to_dialogue limit
_MAX_DIALOGUE_CHARS = 5000  # ElevenLabs text_to_dialogue max chars per request

# Retry settings for transient errors (5xx server errors and 429 rate limits)
_RETRY_WAITS = (2, 6, 18)  # seconds between attempts (3 retries total)


def _collect_bytes_with_retry(fn: Any, *args: Any, **kwargs: Any) -> bytes:
    """
    Call fn(*args, **kwargs), consume the returned Iterator[bytes], and return bytes.

    ElevenLabs SDK convert() methods are generator functions — the HTTP request
    fires during iteration, not during the initial call.  Both the call and the
    full b"".join() consumption are therefore inside the same try block so that
    transient errors anywhere in the stream are retried correctly.

    Retries on:
      - 5xx server errors (transient outages)
      - 429 rate-limit errors (too_many_concurrent_requests / system_busy)

    Raises immediately on 4xx client errors (bad request, invalid voice ID, etc.).
    """
    from elevenlabs.core.api_error import ApiError

    last_exc: ApiError | None = None
    for attempt, wait in enumerate(_RETRY_WAITS):
        try:
            stream = fn(*args, **kwargs)
            return b"".join(chunk for chunk in stream if isinstance(chunk, bytes))
        except ApiError as exc:
            retryable = exc.status_code is not None and (
                exc.status_code >= 500 or exc.status_code == 429
            )
            if retryable and attempt < len(_RETRY_WAITS) - 1:
                last_exc = exc
                log.warning(
                    "tts.api_error_retrying",
                    attempt=attempt + 1,
                    status_code=exc.status_code,
                    wait_secs=wait,
                )
                time.sleep(wait)
            else:
                raise

    raise last_exc  # type: ignore[misc]


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting so it doesn't get read aloud verbatim."""
    # Remove markdown tables (lines with | separators)
    text = re.sub(r"^\|.*\|$", "", text, flags=re.MULTILINE)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}$", "", text, flags=re.MULTILINE)
    # Remove ATX headers (# Heading)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", text)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _make_client() -> Any:
    """Instantiate the ElevenLabs sync client."""
    from elevenlabs.client import ElevenLabs
    return ElevenLabs(api_key=settings.eleven_labs_api_key)


def build_script(
    transcript: dict,
    channels: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """
    Extract speakable messages from a transcript dict.

    Args:
        transcript: Parsed session JSON (title, agents, events, …).
        channels:   Channel IDs to include. Defaults to ["public"].

    Returns:
        Ordered list of (agent_id, agent_name, text) tuples.
    """
    if channels is None:
        channels = ["public"]

    script: list[tuple[str, str, str]] = []
    for event in transcript.get("events", []):
        if event.get("type") != "MESSAGE":
            continue
        if event.get("channel_id") not in channels:
            continue
        text = (event.get("text") or "").strip()
        if not text:
            continue
        script.append((event["agent_id"], event["agent_name"], text))
    return script


def render_mp3(
    transcript_path: Path,
    output_path: Path | None = None,
    channels: list[str] | None = None,
    seed: int | None = None,
) -> Path:
    """
    Generate a single MP3 audio file from a session transcript.

    Args:
        transcript_path: Path to the session JSON transcript.
        output_path:     Where to write the MP3. Defaults to the same directory
                         as the transcript with a .mp3 extension.
        channels:        Which channels to include (default: ["public"]).
        seed:            Random seed for voice assignment. Defaults to seconds
                         since 2000-01-01 UTC (same convention as personas).

    Returns:
        Path to the written MP3 file.

    Raises:
        RuntimeError: If ELEVEN_LABS_API_KEY is not configured.
        ValueError:   If no speakable messages are found, or not enough voices.
    """
    if not settings.eleven_labs_api_key:
        raise RuntimeError(
            "ELEVEN_LABS_API_KEY is not set. Add it to your .env file to generate audio."
        )

    if output_path is None:
        output_path = transcript_path.with_suffix(".mp3")

    if channels is None:
        channels = ["public"]

    # --- Load transcript ---
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

    # --- Build ordered script ---
    script = build_script(transcript, channels=channels)
    if not script:
        raise ValueError(
            f"No speakable messages found in '{transcript_path.name}' "
            f"for channels: {channels}"
        )

    # --- Determine which agents actually speak (ordered, unique) ---
    seen: dict[str, str] = {}  # agent_id → agent_name (first occurrence)
    for agent_id, agent_name, _ in script:
        if agent_id not in seen:
            seen[agent_id] = agent_name

    agents_map = {a["id"]: a for a in transcript.get("agents", [])}
    speaking_agents: list[dict] = []
    for agent_id in seen:
        if agent_id in agents_map:
            speaking_agents.append(agents_map[agent_id])
        else:
            # e.g. hitl messages — synthesize with a fallback entry
            speaking_agents.append({"id": agent_id, "name": seen[agent_id], "role": "participant"})

    # --- Resolve seed ---
    if seed is None:
        from datetime import UTC, datetime
        _epoch = datetime(2000, 1, 1, tzinfo=UTC)
        seed = int((datetime.now(UTC) - _epoch).total_seconds())

    # --- Assign voices ---
    client = _make_client()
    # Use the v2 search API with pagination (get_all() is legacy v1).
    all_voices = []
    next_page_token: str | None = None
    while True:
        page = client.voices.search(
            page_size=100,
            include_total_count=False,  # skip live-count query for performance
            next_page_token=next_page_token,
        )
        all_voices.extend(page.voices)
        if not page.has_more:
            break
        next_page_token = page.next_page_token

    voice_map = assign_voices(speaking_agents, all_voices, seed=seed)

    log.info(
        "tts.voices_assigned",
        agents=list(voice_map.keys()),
        seed=seed,
        total_lines=len(script),
    )

    # --- Render ---
    if len(speaking_agents) <= _MAX_DIALOGUE_VOICES:
        audio_bytes = _render_dialogue(client, script, voice_map)
    else:
        audio_bytes = _render_per_turn(client, script, voice_map)

    output_path.write_bytes(audio_bytes)
    log.info("tts.mp3_written", path=str(output_path), size_kb=len(audio_bytes) // 1024)
    return output_path


def _call_dialogue_batch(client: Any, batch: list) -> bytes:
    """Send one batch of DialogueInputs and return the audio bytes."""
    return _collect_bytes_with_retry(
        client.text_to_dialogue.convert,
        inputs=batch,
        model_id=_DIALOGUE_MODEL,
        output_format=_OUTPUT_FORMAT,
    )


def _render_dialogue(
    client: Any,
    script: list[tuple[str, str, str]],
    voice_map: dict[str, str],
) -> bytes:
    """
    text_to_dialogue calls batched to stay within the 5000-char limit.

    Each batch is a contiguous slice of turns whose total text length ≤ 5000.
    Results are concatenated in order.
    """
    from elevenlabs import DialogueInput

    parts: list[bytes] = []
    batch: list[DialogueInput] = []
    batch_chars = 0

    for agent_id, _, raw_text in script:
        text = _strip_markdown(raw_text)
        if not text:
            continue
        turn_len = len(text)
        # If adding this turn would exceed the limit, flush the current batch first.
        if batch and batch_chars + turn_len > _MAX_DIALOGUE_CHARS:
            parts.append(_call_dialogue_batch(client, batch))
            batch = []
            batch_chars = 0
        # If a single turn exceeds the limit on its own, truncate it.
        if turn_len > _MAX_DIALOGUE_CHARS:
            text = text[:_MAX_DIALOGUE_CHARS]
            turn_len = _MAX_DIALOGUE_CHARS
        batch.append(DialogueInput(text=text, voice_id=voice_map[agent_id]))
        batch_chars += turn_len

    if batch:
        parts.append(_call_dialogue_batch(client, batch))

    return b"".join(parts)


def _render_per_turn(
    client: Any,
    script: list[tuple[str, str, str]],
    voice_map: dict[str, str],
) -> bytes:
    """Per-message TTS calls, concatenated — fallback for >10 unique speakers."""
    parts: list[bytes] = []
    for agent_id, _, raw_text in script:
        text = _strip_markdown(raw_text)
        if not text:
            continue
        parts.append(_collect_bytes_with_retry(
            client.text_to_speech.convert,
            voice_id=voice_map[agent_id],
            text=text,
            model_id=_FALLBACK_MODEL,
            output_format=_OUTPUT_FORMAT,
        ))
    return b"".join(parts)
