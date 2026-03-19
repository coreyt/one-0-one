"""
TranscriptWriter — saves sessions as markdown + optional JSON sidecar.

Features:
    - Auto-saves on session end
    - Checkpoint flushes every N events (configurable, default 10)
      so a crash mid-session doesn't lose everything
    - Markdown format is human-readable; JSON sidecar is machine-readable
    - Monologue events are included in transcripts (clearly marked observer-only)
    - Filename format: <slug>_<setting>_<YYYYMMDD_HHMMSS>.{md,json}

Usage:
    writer = TranscriptWriter(config)
    bus.stream().subscribe(writer.record)   # attach to EventBus
    # ... session runs ...
    await writer.flush()                    # explicit flush at session end
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.logging import get_logger
from src.settings import settings

if TYPE_CHECKING:
    from src.session.config import SessionConfig
    from src.session.events import SessionEvent

log = get_logger(__name__)


def _slugify(text: str) -> str:
    """Convert a title to a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60]


class TranscriptWriter:
    """Accumulates session events and writes transcripts to disk."""

    def __init__(self, config: "SessionConfig") -> None:
        self._config = config
        self._events: list["SessionEvent"] = []
        self._event_count_since_checkpoint = 0
        self._checkpoint_interval = settings.transcript_checkpoint_interval
        self._started_at = datetime.now(UTC)
        self._output_dir = Path(config.transcript.path)
        self._base_name = self._build_base_name()
        self._finalized = False

    def _build_base_name(self) -> str:
        slug = _slugify(self._config.title)
        setting = _slugify(self._config.setting)
        ts = self._started_at.strftime("%Y%m%d_%H%M%S")
        return f"{slug}_{setting}_{ts}"

    def record(self, event: "SessionEvent") -> None:
        """
        Called by EventBus subscriber for every session event.
        Accumulates events and triggers checkpoint flushes.
        """
        self._events.append(event)
        self._event_count_since_checkpoint += 1

        if self._event_count_since_checkpoint >= self._checkpoint_interval:
            self._write_checkpoint()
            self._event_count_since_checkpoint = 0

    async def flush(self) -> None:
        """Write the final transcript files. Call once at session end."""
        if self._finalized:
            return
        self._finalized = True
        self._output_dir.mkdir(parents=True, exist_ok=True)

        fmt = self._config.transcript.format
        if fmt in ("markdown", "both"):
            md_path = self._output_dir / f"{self._base_name}.md"
            md_path.write_text(self._render_markdown(), encoding="utf-8")
            log.info("transcript.saved", format="markdown", path=str(md_path))

        if fmt in ("json", "both"):
            json_path = self._output_dir / f"{self._base_name}.json"
            json_path.write_text(self._render_json(), encoding="utf-8")
            log.info("transcript.saved", format="json", path=str(json_path))

        # Remove checkpoint file if it exists
        chk = self._output_dir / f"{self._base_name}.checkpoint.json"
        if chk.exists():
            chk.unlink()

    def _write_checkpoint(self) -> None:
        """Write a partial JSON checkpoint for crash resilience."""
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            chk = self._output_dir / f"{self._base_name}.checkpoint.json"
            chk.write_text(self._render_json(), encoding="utf-8")
            log.debug(
                "transcript.checkpoint",
                events=len(self._events),
                path=str(chk),
            )
        except OSError as exc:
            log.warning("transcript.checkpoint_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_markdown(self) -> str:
        config = self._config
        lines: list[str] = [
            f"# Session: {config.title}",
            f"**Setting:** {config.setting} | "
            f"**Date:** {self._started_at.isoformat()} | "
            f"**Type:** {config.type}",
            "",
            "## Agents",
            "| Name | Model | Role | Team |",
            "|------|-------|------|------|",
        ]
        for agent in config.agents:
            team = agent.team or "—"
            lines.append(f"| {agent.name} | {agent.provider}/{agent.model} | {agent.role} | {team} |")

        lines.append("")
        lines.append(f"**Topic:** {config.topic}")
        lines.append("")
        lines.append("---")
        lines.append("")

        for event in self._events:
            block = self._event_to_markdown(event)
            if block:
                lines.append(block)
                lines.append("")

        return "\n".join(lines)

    def _event_to_markdown(self, event: "SessionEvent") -> str | None:
        match event.type:
            case "MESSAGE":
                channel_label = (
                    f"[{event.channel_id}]"
                    if event.channel_id != "public"
                    else "[public]"
                )
                private_note = (
                    f" → {event.recipient_id}" if event.recipient_id else ""
                )
                return (
                    f"### Turn {event.turn_number} — {event.agent_name} "
                    f"{channel_label}{private_note}\n\n{event.text}"
                )
            case "MONOLOGUE":
                return (
                    f"### Turn {event.turn_number} — {event.agent_name} "
                    f"[thinking] *(observer only)*\n\n> {event.text}"
                )
            case "GAME_STATE":
                return (
                    f"**[Game state — Turn {event.turn_number}]** "
                    f"`{json.dumps(event.updates)}`"
                )
            case "HYBRID_AUDIT":
                return (
                    f"**[Hybrid audit — Turn {event.turn_number}]** "
                    f"actor={event.actor_id} "
                    f"diverged={event.diverged} "
                    f"proposed_action=`{json.dumps(event.proposed_action)}`"
                )
            case "RULE_VIOLATION":
                return (
                    f"**[Rule violation — Turn {event.turn_number}]** "
                    f"{event.agent_id}: {event.rule}"
                )
            case "SESSION_END":
                return (
                    f"---\n\n**Session ended** — Reason: {event.reason}"
                )
            case _:
                return None

    def _render_json(self) -> str:
        config = self._config
        payload: dict[str, Any] = {
            "title": config.title,
            "setting": config.setting,
            "type": config.type,
            "topic": config.topic,
            "started_at": self._started_at.isoformat(),
            "agents": [
                {
                    "id": a.id,
                    "name": a.name,
                    "provider": a.provider,
                    "model": a.model,
                    "role": a.role,
                    "team": a.team,
                }
                for a in config.agents
            ],
            "events": [
                json.loads(e.model_dump_json()) for e in self._events
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
