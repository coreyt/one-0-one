"""
Generic journal rendering for game plugins.

Text and XML renderers format a game's move history as an ordered log using
two presentation paradigms:

    xml   — structured named-attribute elements; LLMs can match field values
             by name without any spatial reasoning
    text  — compact token stream; tests whether a model can track state from
             plain text

The visual (board) format is intentionally absent here: each game plugin
provides its own visual renderer because spatial layout is game-specific
(a Battleship 10×10 attack grid looks nothing like a Connect Four 6×7 board).

Usage in a game plugin::

    from src.games.renderers import JournalEntry, render_journal_xml, render_journal_text

    entries = [
        JournalEntry(turn=1, actor_id="p1", action_type="drop_disc",
                     details={"column": "4", "disc": "R"}, result="playing"),
    ]
    xml_output  = render_journal_xml(entries)
    text_output = render_journal_text(entries)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class JournalEntry:
    """A single recorded game action for text/XML journal rendering."""

    turn: int
    actor_id: str
    action_type: str
    details: dict[str, str] = field(default_factory=dict)
    result: str | None = None


def render_journal_xml(entries: list[JournalEntry]) -> str:
    """Structured XML journal — one named-attribute element per action.

    Each action's detail key-value pairs become XML attributes so the LLM
    can locate any field by name without parsing positional text.
    """
    parts = ["<move_journal>"]
    for e in entries:
        attrs = " ".join(f'{k}="{v}"' for k, v in e.details.items())
        result_attr = f' result="{e.result}"' if e.result is not None else ""
        attr_str = (" " + attrs) if attrs else ""
        parts.append(
            f'  <action turn="{e.turn}" actor="{e.actor_id}"'
            f' type="{e.action_type}"{attr_str}{result_attr}/>'
        )
    parts.append("</move_journal>")
    return "\n".join(parts)


def render_journal_text(entries: list[JournalEntry]) -> str:
    """Compact token journal — tests whether a model can parse plain text.

    Format: ``[T{n}]{actor}:{action_type}({k}={v},...)->{result}``
    Tokens are wrapped 5 per line to reduce vertical space.
    """
    lines = ["=== MOVE JOURNAL ==="]
    if not entries:
        lines.append("  (no moves yet)")
        return "\n".join(lines)
    tokens = []
    for e in entries:
        detail_str = ",".join(f"{k}={v}" for k, v in e.details.items())
        result_str = f"->{e.result}" if e.result is not None else ""
        tokens.append(f"[T{e.turn}]{e.actor_id}:{e.action_type}({detail_str}){result_str}")
    for i in range(0, len(tokens), 5):
        lines.append("  " + "  ".join(tokens[i : i + 5]))
    return "\n".join(lines)
