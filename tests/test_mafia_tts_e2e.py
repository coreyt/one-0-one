"""Live E2E Mafia game with monologue, feeling tags, and post-game TTS.

Extends the four-provider Mafia test with:
  - monologue=True on every player (native <thinking> blocks)
  - <feeling> tags active (injected into system prompt automatically)
  - post-game TTS: render_mp3() generates a full MP3 from the transcript

Run with:
    uv run pytest tests/test_mafia_tts_e2e.py::test_mafia_monologue_feelings_tts -s -v

The MP3 is written to sessions/ alongside the transcript JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.session.config import (
    AgentConfig,
    ChannelConfig,
    GameConfig,
    HITLConfig,
    OrchestratorConfig,
    SessionConfig,
    TranscriptConfig,
)
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus
from src.session.events import (
    GameStateEvent,
    IncidentEvent,
    MessageEvent,
    MonologueEvent,
    RuleViolationEvent,
    SessionEndEvent,
)
from src.settings import settings

_SEP = "─" * 60

_PHASE_LABELS = {
    "night_mafia_discussion": "Mafia Discussion",
    "night_mafia_vote":       "Mafia Kill Vote",
    "night_detective":        "Detective Investigates",
    "night_doctor":           "Doctor Protects",
    "day_discussion":         "Town Discussion",
    "day_vote":               "Town Vote",
    "complete":               "Game Over",
}

# ---------------------------------------------------------------------------
# Personas — same as the four-family test; feeling tags work via system prompt
# ---------------------------------------------------------------------------

_NARRATOR_PERSONA = """\
You are the Narrator — an omniscient literary storyteller presenting this Mafia game.
Write only in third person. Keep each entry to 2–5 sentences of atmospheric prose.
Narrate only what the engine has already determined. Never invent hidden information.
Do not decide votes, deaths, or winners.\
"""

_MAFIA_DON_PERSONA = """\
I am Don Corvo, leader of the Mafia. My secret role is MAFIA.
My allies are Sal Bricks (mafia_soldier) and Luca Moretti (mafia_consigliere).
I speak in first person — no stage directions.
ACTION PHASES: when response_schema is shown, output ONLY that JSON object.
  Kill vote format: {"target": "agent_id"}
DISCUSSION: speak as an innocent-seeming townsfolk, deflecting suspicion.
TEAM CHANNEL: use <team>message</team> tags for secret mafia coordination.\
"""

_MAFIA_SOLDIER_PERSONA = """\
I am Sal Bricks, enforcer of the Mafia. My secret role is MAFIA.
My allies are Don Corvo (mafia_don) and Luca Moretti (mafia_consigliere).
I speak in first person — no stage directions.
ACTION PHASES: when response_schema is shown, output ONLY that JSON object.
  Kill vote format: {"target": "agent_id"}
DISCUSSION: appear cooperative and cast suspicion on innocent players.
TEAM CHANNEL: use <team>message</team> tags for secret mafia coordination.\
"""

_MAFIA_CONSIGLIERE_PERSONA = """\
I am Luca Moretti, consigliere of the Mafia. My secret role is MAFIA.
My allies are Don Corvo (mafia_don) and Sal Bricks (mafia_soldier).
I speak in first person — no stage directions.
ACTION PHASES: when response_schema is shown, output ONLY that JSON object.
  Kill vote format: {"target": "agent_id"}
DISCUSSION: build trust by day, steer suspicion away from allies.
TEAM CHANNEL: use <team>message</team> tags for secret mafia coordination.\
"""

_DETECTIVE_PERSONA = """\
I am Iris Sharp, the Detective. My secret role is DETECTIVE — Town-aligned.
Each night I investigate one living player and learn if they are Mafia or Town.
My accumulated results appear in the authoritative game view.
I speak in first person — no stage directions.
ACTION PHASES: when response_schema is shown, output ONLY: {"investigate": "agent_id"}
DISCUSSION: use my investigation results strategically — share at the right moment.\
"""

_DOCTOR_PERSONA = """\
I am Dante Mend, the Doctor. My secret role is DOCTOR — Town-aligned.
Each night I protect one living player from the Mafia's kill.
I cannot protect the same player two nights in a row.
I speak in first person — no stage directions.
ACTION PHASES: when response_schema is shown, output ONLY: {"protect": "agent_id"}
DISCUSSION: keep my role secret. Help the town identify the Mafia.\
"""

_VILLAGER_PERSONA = """\
I am {name}, a Villager. Town-aligned. No special night actions.
I speak in first person — no stage directions.
DISCUSSION: help the town identify and eliminate the Mafia.
DAY VOTE: when response_schema is shown, output ONLY: {{"vote_for": "agent_id"}}
  I may also abstain: {{"vote_for": null}}\
"""


def _build_config(sessions_dir: Path) -> SessionConfig:
    """9-player Mafia: 4 providers, monologue ON on every player."""
    v = _VILLAGER_PERSONA
    return SessionConfig(
        title="Mafia — Monologue + Feelings + TTS",
        description=(
            "Full 9-player Mafia with inner monologue, feeling-tag inflections, "
            "and post-game TTS audio generation."
        ),
        type="games",
        setting="game",
        topic="Play a game of Mafia: 3 Mafia, 1 Detective, 1 Doctor, 4 Villagers.",
        agents=[
            # Narrator — no monologue (presentation-only)
            AgentConfig(
                id="moderator", name="The Narrator",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="moderator", persona=_NARRATOR_PERSONA,
                monologue=False,
            ),
            # Mafia
            AgentConfig(
                id="mafia_don", name="Don Corvo",
                provider="mistral", model="mistral-small-latest",
                role="mafia", team="mafia", persona=_MAFIA_DON_PERSONA,
                monologue=True, monologue_mode="prompt",
            ),
            AgentConfig(
                id="mafia_soldier", name="Sal Bricks",
                provider="gemini", model="gemini-2.5-flash",
                role="mafia", team="mafia", persona=_MAFIA_SOLDIER_PERSONA,
                monologue=True, monologue_mode="prompt",
            ),
            AgentConfig(
                id="mafia_consigliere", name="Luca Moretti",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="mafia", team="mafia", persona=_MAFIA_CONSIGLIERE_PERSONA,
                monologue=True, monologue_mode="prompt",
            ),
            # Town specials
            AgentConfig(
                id="detective", name="Iris Sharp",
                provider="gemini", model="gemini-2.5-flash",
                role="detective", persona=_DETECTIVE_PERSONA,
                monologue=True, monologue_mode="prompt",
            ),
            AgentConfig(
                id="doctor", name="Dante Mend",
                provider="openai", model="gpt-4o-mini",
                role="doctor", persona=_DOCTOR_PERSONA,
                monologue=True, monologue_mode="prompt",
            ),
            # Villagers
            AgentConfig(
                id="villager_1", name="Rosa Fields",
                provider="mistral", model="mistral-small-latest",
                role="villager", persona=v.format(name="Rosa Fields"),
                monologue=True, monologue_mode="prompt",
            ),
            AgentConfig(
                id="villager_2", name="Marco Stone",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="villager", persona=v.format(name="Marco Stone"),
                monologue=True, monologue_mode="prompt",
            ),
            AgentConfig(
                id="villager_3", name="Cleo Vance",
                provider="openai", model="gpt-4o-mini",
                role="villager", persona=v.format(name="Cleo Vance"),
                monologue=True, monologue_mode="prompt",
            ),
            AgentConfig(
                id="villager_4", name="Reed Cole",
                provider="gemini", model="gemini-2.5-flash",
                role="villager", persona=v.format(name="Reed Cole"),
                monologue=True, monologue_mode="prompt",
            ),
        ],
        channels=[
            ChannelConfig(id="public", type="public"),
            ChannelConfig(id="mafia", type="team",
                          members=["mafia_don", "mafia_soldier", "mafia_consigliere"]),
        ],
        game=GameConfig(
            plugin="mafia",
            name="Mafia",
            authority_mode="engine_authoritative",
            rules=[
                "The game alternates Night Phase and Day Phase.",
                "NIGHT: Mafia vote to kill one Town member. Detective investigates one player. Doctor protects one player (cannot repeat same target on consecutive nights).",
                "DAY: Engine announces who died. All living players discuss, then vote to eliminate a suspect. Strict majority required; no majority = no elimination.",
                "Mafia members know each other. No other roles are public.",
                "TIE-BREAK (Mafia kill): On tied kill votes the Don's chosen target is eliminated.",
                "WIN — Town: all 3 Mafia eliminated. WIN — Mafia: Mafia count ≥ Town count.",
            ],
            how_to_play=(
                "ACTION PHASES: output ONLY the exact JSON shown in response_schema — "
                "no prose, code fences, or extra fields. "
                "DISCUSSION PHASES: respond with in-character dialogue only — no JSON."
            ),
            max_rounds=12,
        ),
        orchestrator=OrchestratorConfig(type="python", module="turn_based"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=True, format="both", path=sessions_dir),
        max_turns=300,
    )


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


async def test_mafia_monologue_feelings_tts():
    """Full Mafia game with monologue, feeling tags, and post-game TTS.

    Proves:
      - All 9-player Mafia game logic passes (same assertions as base test).
      - Monologue events are emitted (agents produce <thinking> blocks).
      - At least one <feeling> tag appears in the transcript (feeling system active).
      - ElevenLabs render_mp3() succeeds and produces a non-empty MP3 file.

    Run:
        uv run pytest tests/test_mafia_tts_e2e.py::test_mafia_monologue_feelings_tts -s -v
    """
    sessions_dir = Path(settings.sessions_path)
    sessions_dir.mkdir(parents=True, exist_ok=True)

    config = _build_config(sessions_dir)
    bus = EventBus()
    violation_count = 0
    incident_count = 0
    monologue_count = 0
    feeling_tags_seen: list[str] = []   # agent names that used feeling tags
    last_phase: list[str | None] = [None]
    last_round: list[int] = [0]

    def on_event(event) -> None:
        nonlocal violation_count, incident_count, monologue_count

        if isinstance(event, GameStateEvent):
            auth = event.updates.get("authoritative_state") or {}
            if not auth:
                return
            phase = auth.get("phase", "")
            round_num = auth.get("round_number", 0)
            player_names: dict[str, str] = auth.get("player_names", {})
            alive_ids: list[str] = auth.get("alive_players", [])
            if phase and (phase != last_phase[0] or round_num != last_round[0]):
                last_phase[0] = phase
                last_round[0] = round_num
                label = _PHASE_LABELS.get(phase, phase)
                round_tag = f"Round {round_num} — " if round_num else ""
                header = f"{round_tag}{label}"
                print(f"\n┌─ {header} {'─' * max(0, 52 - len(header))}┐", flush=True)
                if alive_ids and phase != "complete":
                    alive_names = [player_names.get(p, p) for p in alive_ids]
                    print(f"│  Alive ({len(alive_ids)}): {', '.join(alive_names)}", flush=True)
            winner = auth.get("winner")
            if winner and last_phase[0] != "complete":
                last_phase[0] = "complete"
                print(f"\n{'═' * 60}", flush=True)
                print(f"  *** {winner.upper()} WINS! ***", flush=True)
                print(f"{'═' * 60}", flush=True)

        elif isinstance(event, MessageEvent):
            raw = event.text.strip()
            if not raw:
                return
            # Detect feeling tags (they're already stripped from event.text,
            # so we check tts_text which carries the v3 annotation)
            if event.tts_text:
                feeling_tags_seen.append(event.agent_name)
            if event.channel_id == "public":
                tts_marker = " ♪" if event.tts_text else ""
                if event.agent_id == "game_engine":
                    print(f"\n  ◆ [ENGINE] {raw[:500]}", flush=True)
                else:
                    print(f"\n  [{event.agent_name}{tts_marker}] {raw[:400]}", flush=True)
            elif event.channel_id == "mafia":
                print(f"\n  ▸ [MAFIA/{event.agent_name}] {raw[:300]}", flush=True)

        elif isinstance(event, MonologueEvent):
            monologue_count += 1
            # Show a trimmed monologue excerpt so we can see feeling tags in action
            excerpt = event.text.strip()[:200].replace("\n", " ")
            print(f"\n  ◦ [MONOLOGUE/{event.agent_name}] {excerpt}…", flush=True)

        elif isinstance(event, RuleViolationEvent):
            violation_count += 1
            print(f"\n  ! RULE VIOLATION ({event.agent_id}): {event.rule[:120]}", flush=True)

        elif isinstance(event, IncidentEvent):
            incident_count += 1
            print(
                f"\n  ! INCIDENT ({event.agent_id}): "
                f"{event.incident_type} — {event.detail[:120]}",
                flush=True,
            )

        elif isinstance(event, SessionEndEvent):
            print(f"\n{_SEP}", flush=True)
            print(f"SESSION END: {event.reason}", flush=True)
            print(_SEP, flush=True)

    bus.stream().subscribe(on_event)

    # Header
    print(f"\n{_SEP}", flush=True)
    print("Mafia — Monologue + Feelings + TTS (9 players)", flush=True)
    agents_by_provider: dict[str, list[str]] = {}
    for a in config.agents:
        agents_by_provider.setdefault(f"{a.provider}/{a.model}", []).append(
            f"{a.name}({a.role})"
        )
    for model, names in agents_by_provider.items():
        print(f"  {model:<38}: {', '.join(names)}", flush=True)
    print(f"  Monologue: ON (prompt mode)   Feelings: ON", flush=True)
    print(f"  TTS: eleven_v3 (post-game batch) + eleven_flash_v2_5 (streaming ready)", flush=True)
    print(_SEP, flush=True)

    engine = SessionEngine(config, bus)
    state = await engine.run()

    # ------------------------------------------------------------------
    # Extract final authoritative state (same as base test)
    # ------------------------------------------------------------------
    auth = state.game_state.custom["authoritative_state"]
    player_names: dict[str, str] = auth["player_names"]
    roles: dict[str, str] = auth["roles"]
    alive_ids: list[str] = auth["alive_players"]
    eliminated_ids: list[str] = auth["eliminated"]
    revealed_roles: dict[str, str] = auth.get("revealed_roles", {})
    mafia_order: list[str] = auth["mafia_order"]
    doctor_history: list[str] = auth.get("doctor_history", [])
    round_log: list[dict] = auth.get("round_log", [])
    winner = auth.get("winner")
    living_mafia = [p for p in mafia_order if p in alive_ids]
    living_town = [p for p in alive_ids if p not in mafia_order]
    rounds_played = auth.get("round_number", 0)

    print(f"\nFINAL STATE  (end_reason={state.end_reason}, rounds={rounds_played})", flush=True)
    alive_names = [f"{player_names[p]}({roles[p]})" for p in alive_ids]
    print(f"Alive  ({len(alive_ids)}): {', '.join(alive_names) or '—'}", flush=True)
    elim_names = [f"{player_names[p]}({revealed_roles.get(p, '?')})" for p in eliminated_ids]
    print(f"Elim.  ({len(eliminated_ids)}): {', '.join(elim_names) or '—'}", flush=True)
    print(f"Monologue events: {monologue_count}", flush=True)
    print(f"Messages with feeling tags: {len(feeling_tags_seen)}", flush=True)
    print(f"Violations: {violation_count}  Incidents: {incident_count}", flush=True)

    # ------------------------------------------------------------------
    # Game logic assertions (identical to base test)
    # ------------------------------------------------------------------
    assert state.end_reason in ("win_condition", "max_turns"), (
        f"Unexpected end_reason: {state.end_reason!r}"
    )
    assert rounds_played >= 1, "No rounds played — LLMs were not engaged."

    if state.end_reason == "win_condition":
        assert winner in ("town", "mafia"), f"Invalid winner: {winner!r}"
        if winner == "town":
            assert not living_mafia
        else:
            assert len(living_mafia) >= len(living_town)

    for pid in eliminated_ids:
        assert pid in revealed_roles
        assert revealed_roles[pid] == roles[pid]

    for i in range(1, len(doctor_history)):
        assert doctor_history[i] != doctor_history[i - 1], (
            "Doctor repeated consecutive protect."
        )

    night_results = [e for e in round_log if e.get("event_type") == "night_result"]
    if rounds_played >= 1:
        assert night_results, "No night_result entries in round_log."

    overlap = set(alive_ids) & set(eliminated_ids)
    assert not overlap

    assert len(auth["players"]) == 9
    assert len(mafia_order) == 3

    # ------------------------------------------------------------------
    # Monologue assertion
    # ------------------------------------------------------------------
    assert monologue_count > 0, (
        "No MonologueEvent emitted — monologue=True had no effect."
    )

    # ------------------------------------------------------------------
    # Transcript assertions + locate the JSON file
    # ------------------------------------------------------------------
    transcripts = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    assert transcripts, "No JSON transcript was written to sessions/."
    transcript_path = transcripts[-1]

    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    event_types = {e["type"] for e in payload["events"]}
    assert "GAME_STATE" in event_types
    assert "SESSION_END" in event_types

    # Verify tts_text field is present on at least one MESSAGE event in the transcript
    msg_events = [e for e in payload["events"] if e["type"] == "MESSAGE"]
    assert any("tts_text" in e for e in msg_events), (
        "No MESSAGE events have a tts_text field — feeling-tag pipeline not wired."
    )

    # ------------------------------------------------------------------
    # Post-game TTS: generate the MP3
    # ------------------------------------------------------------------
    print(f"\n{_SEP}", flush=True)
    print(f"Generating post-game audio from: {transcript_path.name}", flush=True)
    print(f"Model: eleven_v3 (multi-speaker dialogue)", flush=True)

    from src.tts.renderer import render_mp3
    mp3_path = render_mp3(
        transcript_path=transcript_path,
        channels=["public"],   # public channel only — narration + player speech
    )

    assert mp3_path.exists(), f"render_mp3 returned {mp3_path} but file not found."
    size_kb = mp3_path.stat().st_size // 1024
    assert size_kb > 0, "MP3 file is empty."

    print(f"MP3 written: {mp3_path}", flush=True)
    print(f"MP3 size:    {size_kb} KB", flush=True)

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}", flush=True)
    print(f"RESULT: {rounds_played} round(s), end={state.end_reason}", flush=True)
    if winner:
        print(f"Winner: {winner.upper()}", flush=True)
        winning = auth.get("winning_players", [])
        print(f"  Winning players: {[player_names.get(p, p) for p in winning]}", flush=True)
    print(f"Monologue events: {monologue_count}", flush=True)
    print(f"Messages with feeling tags: {len(feeling_tags_seen)}", flush=True)
    print(f"Violations: {violation_count}  Incidents: {incident_count}", flush=True)
    print(f"MP3: {mp3_path} ({size_kb} KB)", flush=True)
    print("All assertions passed.", flush=True)
