"""
one-0-one-audio — generate an MP3 audio file from a session transcript.

Usage:
    one-0-one-audio sessions/mafia_game_20260223.json
    one-0-one-audio sessions/mafia_game_20260223.json --output mafia.mp3
    one-0-one-audio sessions/mafia_game_20260223.json --channel public,mafia
    one-0-one-audio --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.settings import settings
from src.tts.renderer import render_mp3


def _list_transcripts() -> None:
    path = settings.sessions_path
    jsons = sorted(path.glob("*.json"))
    if not jsons:
        print(f"No transcripts found in {path}/")
        return
    print(f"Transcripts in {path}/:")
    for f in jsons:
        size_kb = f.stat().st_size // 1024
        print(f"  {f.name}  ({size_kb} KB)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="one-0-one-audio",
        description="Generate an MP3 audio file from a one-0-one session transcript.",
    )
    parser.add_argument(
        "transcript",
        nargs="?",
        type=Path,
        help="Path to a session transcript JSON file.",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        metavar="PATH",
        help="Output MP3 path (default: same dir as transcript, .mp3 extension).",
    )
    parser.add_argument(
        "--channel", "-c",
        default="public",
        metavar="CHANNELS",
        help="Comma-separated channel IDs to include (default: public).",
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=None,
        metavar="N",
        help="Random seed for voice assignment (default: seconds since 2000-01-01 UTC).",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available transcripts in the sessions directory and exit.",
    )
    args = parser.parse_args()

    if args.list:
        _list_transcripts()
        return

    if not args.transcript:
        parser.error(
            "transcript path is required. "
            "Use --list to browse available transcripts."
        )

    transcript_path = args.transcript.expanduser().resolve()
    if not transcript_path.exists():
        print(f"Error: transcript not found: {transcript_path}", file=sys.stderr)
        sys.exit(1)

    channels = [c.strip() for c in args.channel.split(",") if c.strip()]

    print(f"Generating audio from: {transcript_path.name}")
    print(f"Channels: {', '.join(channels)}")
    if args.seed is not None:
        print(f"Seed: {args.seed}")

    try:
        output = render_mp3(
            transcript_path=transcript_path,
            output_path=args.output,
            channels=channels,
            seed=args.seed,
        )
        print(f"MP3 saved to: {output}")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
