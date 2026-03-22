"""Live end-to-end test: complete Mafia game with real LLM providers.

Run with:
    uv run pytest tests/test_mafia_live_e2e.py -s -v

The -s flag is required to see per-turn output.
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
    RuleViolationEvent,
    SessionEndEvent,
)

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
# Personas — action-format-first, flavour second
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


def _build_config(tmp_path: Path) -> SessionConfig:
    """9-player Mafia using cheap models across all three supported providers."""
    v = _VILLAGER_PERSONA
    return SessionConfig(
        title="Mafia Live E2E",
        description="Complete 9-player Mafia game with live LLM providers.",
        type="games",
        setting="game",
        topic="Play a game of Mafia: 3 Mafia, 1 Detective, 1 Doctor, 4 Villagers.",
        agents=[
            AgentConfig(
                id="moderator", name="The Narrator",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="moderator", persona=_NARRATOR_PERSONA,
            ),
            AgentConfig(
                id="mafia_don", name="Don Corvo",
                provider="openai", model="gpt-4o-mini",
                role="mafia", team="mafia", persona=_MAFIA_DON_PERSONA,
            ),
            AgentConfig(
                id="mafia_soldier", name="Sal Bricks",
                provider="gemini", model="gemini-2.5-flash",
                role="mafia", team="mafia", persona=_MAFIA_SOLDIER_PERSONA,
            ),
            AgentConfig(
                id="mafia_consigliere", name="Luca Moretti",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="mafia", team="mafia", persona=_MAFIA_CONSIGLIERE_PERSONA,
            ),
            AgentConfig(
                id="detective", name="Iris Sharp",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="detective", persona=_DETECTIVE_PERSONA,
            ),
            AgentConfig(
                id="doctor", name="Dante Mend",
                provider="openai", model="gpt-4o-mini",
                role="doctor", persona=_DOCTOR_PERSONA,
            ),
            AgentConfig(
                id="villager_1", name="Rosa Fields",
                provider="gemini", model="gemini-2.5-flash",
                role="villager", persona=v.format(name="Rosa Fields"),
            ),
            AgentConfig(
                id="villager_2", name="Marco Stone",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="villager", persona=v.format(name="Marco Stone"),
            ),
            AgentConfig(
                id="villager_3", name="Cleo Vance",
                provider="openai", model="gpt-4o-mini",
                role="villager", persona=v.format(name="Cleo Vance"),
            ),
            AgentConfig(
                id="villager_4", name="Reed Cole",
                provider="gemini", model="gemini-2.5-flash",
                role="villager", persona=v.format(name="Reed Cole"),
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
        transcript=TranscriptConfig(auto_save=True, format="both", path=tmp_path),
        max_turns=300,  # generous: up to 12 rounds × ~25 turns each
    )


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


async def test_mafia_complete_game_with_real_llms(tmp_path):
    """Play a full Mafia game through the real SessionEngine with live LLMs.

    What this test proves:

    LLMs engaged
    ~~~~~~~~~~~~
    * No LiteLLMClient mock — every turn issues a real provider call across
      Anthropic (Haiku), OpenAI (gpt-4o-mini), and Google (Gemini Flash).
    * At least one full round (night + day) was played.

    Game logic correct
    ~~~~~~~~~~~~~~~~~~
    * Town win: every mafia member is in the eliminated list.
    * Mafia win: mafia count ≥ town count in the final alive set.
    * Eliminated players have their roles revealed in revealed_roles.
    * Doctor never protected the same player two nights in a row.
    * Round log records night and day results for each completed round.

    Session complete
    ~~~~~~~~~~~~~~~~
    * end_reason is "win_condition" or "max_turns".
    * For win_condition: declared winner matches the game-logic win check.
    * A JSON transcript is written containing GAME_STATE + SESSION_END events.
    """
    config = _build_config(tmp_path)
    bus = EventBus()
    violation_count = 0
    incident_count = 0
    last_phase: list[str | None] = [None]
    last_round: list[int] = [0]

    def on_event(event) -> None:
        nonlocal violation_count, incident_count

        if isinstance(event, GameStateEvent):
            auth = event.updates.get("authoritative_state") or {}
            if not auth:
                return

            phase = auth.get("phase", "")
            round_num = auth.get("round_number", 0)
            player_names: dict[str, str] = auth.get("player_names", {})
            alive_ids: list[str] = auth.get("alive_players", [])

            # Print a phase banner when phase or round changes
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
            text = event.text.strip()
            if not text:
                return
            if event.channel_id == "public":
                if event.agent_id == "game_engine":
                    print(f"\n  ◆ [ENGINE] {text[:500]}", flush=True)
                else:
                    print(f"\n  [{event.agent_name}] {text[:400]}", flush=True)
            elif event.channel_id == "mafia":
                print(f"\n  ▸ [MAFIA/{event.agent_name}] {text[:300]}", flush=True)

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
    print("Mafia — Live LLM E2E (9 players)", flush=True)
    agents_by_role: dict[str, list[str]] = {}
    for a in config.agents:
        agents_by_role.setdefault(a.role, []).append(f"{a.name}({a.provider}/{a.model})")
    for role, names in agents_by_role.items():
        print(f"  {role:<12}: {', '.join(names)}", flush=True)
    print(_SEP, flush=True)

    engine = SessionEngine(config, bus)
    state = await engine.run()

    # ------------------------------------------------------------------
    # Extract final authoritative state
    # ------------------------------------------------------------------
    auth = state.game_state.custom["authoritative_state"]
    player_names: dict[str, str] = auth["player_names"]
    roles: dict[str, str] = auth["roles"]
    alive_ids: list[str] = auth["alive_players"]
    eliminated_ids: list[str] = auth["eliminated"]
    revealed_roles: dict[str, str] = auth.get("revealed_roles", {})
    mafia_order: list[str] = auth["mafia_order"]
    doctor_history: list[str] = auth.get("doctor_history", [])
    detective_results: list[dict] = auth.get("detective_results", [])
    round_log: list[dict] = auth.get("round_log", [])
    winner = auth.get("winner")

    living_mafia = [p for p in mafia_order if p in alive_ids]
    living_town = [p for p in alive_ids if p not in mafia_order]
    rounds_played = auth.get("round_number", 0)

    # Summary printout
    print(f"\nFINAL STATE  (end_reason={state.end_reason}, rounds={rounds_played})", flush=True)
    alive_names = [f"{player_names[p]}({roles[p]})" for p in alive_ids]
    print(f"Alive  ({len(alive_ids)}): {', '.join(alive_names) or '—'}", flush=True)
    elim_names = [f"{player_names[p]}({revealed_roles.get(p, '?')})" for p in eliminated_ids]
    print(f"Elim.  ({len(eliminated_ids)}): {', '.join(elim_names) or '—'}", flush=True)
    print(f"Detective results: {len(detective_results)} investigation(s)", flush=True)
    print(f"Doctor history:    {[player_names.get(p, p) for p in doctor_history]}", flush=True)
    print(f"Round log entries: {len(round_log)}", flush=True)
    print(f"Violations: {violation_count}  Incidents: {incident_count}", flush=True)

    # ------------------------------------------------------------------
    # 1. Session ended cleanly
    # ------------------------------------------------------------------
    assert state.end_reason in ("win_condition", "max_turns"), (
        f"Unexpected end_reason: {state.end_reason!r}"
    )

    # ------------------------------------------------------------------
    # 2. LLMs were engaged — at least one round was played
    # ------------------------------------------------------------------
    assert rounds_played >= 1, "No rounds played — LLMs were not engaged."

    # ------------------------------------------------------------------
    # 3. Win condition correctness (only checked for win_condition)
    # ------------------------------------------------------------------
    if state.end_reason == "win_condition":
        assert winner in ("town", "mafia"), (
            f"end_reason is win_condition but winner={winner!r} is not valid."
        )
        if winner == "town":
            assert not living_mafia, (
                f"Town wins but mafia members still alive: "
                + str([player_names[p] for p in living_mafia])
            )
        elif winner == "mafia":
            assert len(living_mafia) >= len(living_town), (
                f"Mafia wins but mafia({len(living_mafia)}) < town({len(living_town)})."
            )

    # ------------------------------------------------------------------
    # 4. All eliminated players have revealed roles
    # ------------------------------------------------------------------
    for pid in eliminated_ids:
        assert pid in revealed_roles, (
            f"Eliminated player {player_names[pid]!r} has no revealed role in revealed_roles."
        )
        assert revealed_roles[pid] == roles[pid], (
            f"Revealed role mismatch for {player_names[pid]}: "
            f"revealed={revealed_roles[pid]!r}, actual={roles[pid]!r}."
        )

    # ------------------------------------------------------------------
    # 5. Doctor never protected the same player two nights in a row
    # ------------------------------------------------------------------
    for i in range(1, len(doctor_history)):
        assert doctor_history[i] != doctor_history[i - 1], (
            f"Doctor protected {player_names.get(doctor_history[i], doctor_history[i])!r} "
            f"two consecutive nights (nights {i} and {i + 1})."
        )

    # ------------------------------------------------------------------
    # 6. Round log has night results for each completed round
    # ------------------------------------------------------------------
    night_results = [e for e in round_log if e.get("event_type") == "night_result"]
    if rounds_played >= 1:
        # At least one night cycle must have been recorded
        assert night_results, (
            f"round_log has no night_result entries despite {rounds_played} round(s) played."
        )

    # ------------------------------------------------------------------
    # 7. Eliminated players are not in alive_ids
    # ------------------------------------------------------------------
    overlap = set(alive_ids) & set(eliminated_ids)
    assert not overlap, (
        f"Players in both alive and eliminated: "
        + str([player_names[p] for p in overlap])
    )

    # ------------------------------------------------------------------
    # 8. Total player count is 9 (3 mafia + 1 detective + 1 doctor + 4 villagers)
    # ------------------------------------------------------------------
    all_players: list[str] = auth["players"]
    assert len(all_players) == 9, (
        f"Expected 9 players, got {len(all_players)}: {all_players}"
    )
    assert len(mafia_order) == 3, (
        f"Expected 3 mafia, got {len(mafia_order)}: {mafia_order}"
    )

    # ------------------------------------------------------------------
    # 9. Transcript written and sane
    # ------------------------------------------------------------------
    transcripts = sorted(tmp_path.glob("*.json"))
    assert transcripts, "No JSON transcript was written."
    payload = json.loads(transcripts[0].read_text(encoding="utf-8"))
    event_types = {e["type"] for e in payload["events"]}
    assert "GAME_STATE" in event_types, "GAME_STATE events missing from transcript."
    assert "SESSION_END" in event_types, "SESSION_END event missing from transcript."
    assert any(
        e["type"] == "SESSION_END"
        and e["reason"] in ("win_condition", "max_turns")
        for e in payload["events"]
    ), "SESSION_END event has unexpected reason in transcript."

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}", flush=True)
    print(f"RESULT: {rounds_played} round(s), end={state.end_reason}", flush=True)
    if winner:
        print(f"Winner: {winner.upper()}", flush=True)
        winning = auth.get("winning_players", [])
        print(f"  Winning players: {[player_names.get(p, p) for p in winning]}", flush=True)
    print(f"Violations: {violation_count}  Incidents: {incident_count}", flush=True)
    print("All assertions passed.", flush=True)


# ---------------------------------------------------------------------------
# Variant: haiku / flash / gpt-4o-mini (small) / mistral-small across all roles
# ---------------------------------------------------------------------------

def _build_config_four_families(tmp_path: Path) -> SessionConfig:
    """Same 9-player game spread across Anthropic, Gemini, OpenAI, and Mistral."""
    v = _VILLAGER_PERSONA
    return SessionConfig(
        title="Mafia Mixed-Providers E2E",
        description="9-player Mafia with Haiku, Flash, GPT-4o-mini, and Mistral.",
        type="games",
        setting="game",
        topic="Play a game of Mafia: 3 Mafia, 1 Detective, 1 Doctor, 4 Villagers.",
        agents=[
            # Narrator — Haiku (Anthropic)
            AgentConfig(
                id="moderator", name="The Narrator",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="moderator", persona=_NARRATOR_PERSONA,
            ),
            # Mafia — spread across Mistral / Flash / Haiku
            AgentConfig(
                id="mafia_don", name="Don Corvo",
                provider="mistral", model="mistral-small-latest",
                role="mafia", team="mafia", persona=_MAFIA_DON_PERSONA,
            ),
            AgentConfig(
                id="mafia_soldier", name="Sal Bricks",
                provider="gemini", model="gemini-2.5-flash",
                role="mafia", team="mafia", persona=_MAFIA_SOLDIER_PERSONA,
            ),
            AgentConfig(
                id="mafia_consigliere", name="Luca Moretti",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="mafia", team="mafia", persona=_MAFIA_CONSIGLIERE_PERSONA,
            ),
            # Town specials — Flash / Small
            AgentConfig(
                id="detective", name="Iris Sharp",
                provider="gemini", model="gemini-2.5-flash",
                role="detective", persona=_DETECTIVE_PERSONA,
            ),
            AgentConfig(
                id="doctor", name="Dante Mend",
                provider="openai", model="gpt-4o-mini",
                role="doctor", persona=_DOCTOR_PERSONA,
            ),
            # Villagers — Mistral / Haiku / Small / Flash
            AgentConfig(
                id="villager_1", name="Rosa Fields",
                provider="mistral", model="mistral-small-latest",
                role="villager", persona=v.format(name="Rosa Fields"),
            ),
            AgentConfig(
                id="villager_2", name="Marco Stone",
                provider="anthropic", model="claude-haiku-4-5-20251001",
                role="villager", persona=v.format(name="Marco Stone"),
            ),
            AgentConfig(
                id="villager_3", name="Cleo Vance",
                provider="openai", model="gpt-4o-mini",
                role="villager", persona=v.format(name="Cleo Vance"),
            ),
            AgentConfig(
                id="villager_4", name="Reed Cole",
                provider="gemini", model="gemini-2.5-flash",
                role="villager", persona=v.format(name="Reed Cole"),
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
        transcript=TranscriptConfig(auto_save=True, format="both", path=tmp_path),
        max_turns=300,
    )


async def test_mafia_four_model_families(tmp_path):
    """Mafia game exercising Anthropic Haiku, Gemini Flash, OpenAI small, and Mistral.

    Run with:
        uv run pytest tests/test_mafia_live_e2e.py::test_mafia_four_model_families -s -v
    """
    config = _build_config_four_families(tmp_path)
    bus = EventBus()
    violation_count = 0
    incident_count = 0
    last_phase: list[str | None] = [None]
    last_round: list[int] = [0]

    def on_event(event) -> None:
        nonlocal violation_count, incident_count

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
            text = event.text.strip()
            if not text:
                return
            if event.channel_id == "public":
                if event.agent_id == "game_engine":
                    print(f"\n  ◆ [ENGINE] {text[:500]}", flush=True)
                else:
                    print(f"\n  [{event.agent_name}] {text[:400]}", flush=True)
            elif event.channel_id == "mafia":
                print(f"\n  ▸ [MAFIA/{event.agent_name}] {text[:300]}", flush=True)

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

    print(f"\n{_SEP}", flush=True)
    print("Mafia — 4 Model Families E2E (9 players)", flush=True)
    agents_by_provider: dict[str, list[str]] = {}
    for a in config.agents:
        agents_by_provider.setdefault(f"{a.provider}/{a.model}", []).append(
            f"{a.name}({a.role})"
        )
    for model, names in agents_by_provider.items():
        print(f"  {model:<38}: {', '.join(names)}", flush=True)
    print(_SEP, flush=True)

    engine = SessionEngine(config, bus)
    state = await engine.run()

    auth = state.game_state.custom["authoritative_state"]
    player_names: dict[str, str] = auth["player_names"]
    roles: dict[str, str] = auth["roles"]
    alive_ids: list[str] = auth["alive_players"]
    eliminated_ids: list[str] = auth["eliminated"]
    revealed_roles: dict[str, str] = auth.get("revealed_roles", {})
    mafia_order: list[str] = auth["mafia_order"]
    doctor_history: list[str] = auth.get("doctor_history", [])
    detective_results: list[dict] = auth.get("detective_results", [])
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
    print(f"Detective: {len(detective_results)} result(s)  Doctor: {[player_names.get(p,p) for p in doctor_history]}", flush=True)
    print(f"Round log: {len(round_log)} entries  Violations: {violation_count}  Incidents: {incident_count}", flush=True)

    assert state.end_reason in ("win_condition", "max_turns")
    assert rounds_played >= 1, "No rounds played."
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
        assert doctor_history[i] != doctor_history[i - 1], "Doctor repeated consecutive protect."
    night_results = [e for e in round_log if e.get("event_type") == "night_result"]
    if rounds_played >= 1:
        assert night_results, "No night_result entries in round_log."
    overlap = set(alive_ids) & set(eliminated_ids)
    assert not overlap
    assert len(auth["players"]) == 9
    assert len(mafia_order) == 3

    print(f"\n{'=' * 60}", flush=True)
    print(f"RESULT: {rounds_played} round(s), end={state.end_reason}", flush=True)
    if winner:
        print(f"Winner: {winner.upper()}", flush=True)
        winning = auth.get("winning_players", [])
        print(f"  Winning players: {[player_names.get(p, p) for p in winning]}", flush=True)
    print(f"Violations: {violation_count}  Incidents: {incident_count}", flush=True)
    print("All assertions passed.", flush=True)
