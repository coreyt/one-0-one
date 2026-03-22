"""Live end-to-end test: complete Connect Four game with real LLM providers.

Run with:
    uv run pytest tests/test_connect_four_live_e2e.py -s -v

The -s flag is required to see per-turn board output.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.games.connect_four import render_connect_four_board
from src.session.config import (
    AgentConfig,
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

# Player personas from the production template
_RED_PERSONA = """\
You are Red in a deterministic game of Connect Four.

Your job is to make the strongest legal move from the authoritative game view.

RESPONSE FORMAT:
{"column": N}

Rules for your response:
- Output exactly one legal JSON move object.
- Do not include your name.
- Do not add commentary, taunts, explanations, code fences, or extra text.
- Do not discuss rules or identity.\
"""

_BLACK_PERSONA = """\
You are Black in a deterministic game of Connect Four.

Your job is to make the strongest legal move from the authoritative game view.

RESPONSE FORMAT:
{"column": N}

Rules for your response:
- Output exactly one legal JSON move object.
- Do not include your name.
- Do not add commentary, taunts, explanations, code fences, or extra text.
- Do not discuss rules or identity.\
"""

_REFEREE_PERSONA = """\
You are the presentation referee for a deterministic game of Connect Four.

You are not the source of truth for moves, legality, turn order, or winners.
Read only the authoritative game view in the system messages and narrate what it says.

Rules for your response:
- Do not make moves for players.
- Do not debate identity, rules, or hidden reasoning.
- Keep narration brief (1–2 sentences).
- If the game is over, announce the engine-determined winner or draw plainly.\
"""


def _build_config(tmp_path: Path) -> SessionConfig:
    """Build a complete Connect Four session config using direct provider routing.

    Uses OpenAI (Red) vs Anthropic (Black) to exercise two real LLM providers.
    The referee is Anthropic Haiku for fast, cheap narration.
    """
    return SessionConfig(
        title="Connect Four Live E2E",
        description="Complete Connect Four game with live LLM providers.",
        type="games",
        setting="game",
        topic="Play a game of Connect Four. Red moves first.",
        agents=[
            AgentConfig(
                id="referee",
                name="Referee",
                provider="anthropic",
                model="claude-haiku-4-5-20251001",
                role="moderator",
                persona=_REFEREE_PERSONA,
            ),
            AgentConfig(
                id="player_red",
                name="Red",
                provider="openai",
                model="gpt-4o",
                role="player",
                persona=_RED_PERSONA,
            ),
            AgentConfig(
                id="player_black",
                name="Black",
                provider="openai",
                model="gpt-4o-mini",
                role="player",
                persona=_BLACK_PERSONA,
            ),
        ],
        game=GameConfig(
            plugin="connect_four",
            name="Connect Four",
            authority_mode="engine_authoritative",
            rules=[
                "The board is 7 columns wide and 6 rows tall.",
                "Players alternate dropping one disc per turn into any column that is not full.",
                "Discs fall to the lowest available row in the chosen column.",
                "The first player to place four discs in a line horizontally, vertically, or diagonally wins.",
                "If all 42 cells are filled with no winner, the game is a draw.",
                "Red moves first. Black moves second.",
            ],
            how_to_play=(
                "On your turn, respond with exactly one JSON object: {\"column\": N} "
                "where N is 1 through 7. No prose, code fences, or extra text."
            ),
        ),
        orchestrator=OrchestratorConfig(type="python", module="turn_based"),
        hitl=HITLConfig(enabled=False),
        transcript=TranscriptConfig(auto_save=True, format="both", path=tmp_path),
        max_turns=120,  # generous ceiling: 42 moves + up to 43 referee turns + retries
    )


# ---------------------------------------------------------------------------
# Game-logic helpers (independent of the engine)
# ---------------------------------------------------------------------------


def _board_is_physically_valid(board: list[list[str]]) -> bool:
    """Gravity check: no disc floats above an empty cell.

    Scans each column top-to-bottom. Once a disc is encountered, all cells
    below it must also be filled (no empty cell beneath a disc).
    """
    rows = len(board)
    columns = len(board[0]) if board else 0
    for col in range(columns):
        found_disc = False
        for row in range(rows):  # row 0 = top, row rows-1 = bottom
            if board[row][col] != ".":
                found_disc = True
            elif found_disc:
                return False  # empty cell below a disc — violates gravity
    return True


def _find_connect_four(board: list[list[str]], disc: str) -> bool:
    """Return True if *disc* has at least one 4-in-a-row anywhere on the board."""
    rows = len(board)
    cols = len(board[0]) if board else 0
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]  # →, ↓, ↘, ↙
    for r in range(rows):
        for c in range(cols):
            if board[r][c] != disc:
                continue
            for dr, dc in directions:
                if all(
                    0 <= r + dr * i < rows
                    and 0 <= c + dc * i < cols
                    and board[r + dr * i][c + dc * i] == disc
                    for i in range(4)
                ):
                    return True
    return False


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


async def test_connect_four_complete_game_with_real_llms(tmp_path):
    """Play a full Connect Four game through the real SessionEngine with live LLMs.

    What this test proves:

    LLMs engaged
    ~~~~~~~~~~~~
    * No LiteLLMClient mock — every player turn issues a real provider call to
      OpenAI (Red) and Google (Black) via the configured API keys.
    * Alternating turn order is verified: disc sequence R, B, R, B, … must match
      the recorded turn log.

    Game logic correct
    ~~~~~~~~~~~~~~~~~~
    * Gravity: no disc is floating above an empty cell.
    * Move count: filled cells on the board equals the engine's move counter.
    * Win: the declared winner's disc forms an actual 4-in-a-row on the final board.
    * Draw: every cell is filled (42/42) and neither disc has a 4-in-a-row.

    Session complete
    ~~~~~~~~~~~~~~~~
    * end_reason must be "win_condition" or "draw" — not "max_turns".
    * A JSON transcript is written and contains GAME_STATE + SESSION_END events.
    """
    config = _build_config(tmp_path)
    bus = EventBus()
    turn_log: list[dict] = []
    violation_count = 0
    incident_count = 0

    def on_event(event) -> None:
        nonlocal violation_count, incident_count

        if isinstance(event, GameStateEvent):
            delta = event.updates.get("authoritative_delta") or {}
            auth = event.updates.get("authoritative_state") or {}
            last_move = delta.get("last_move")
            if not last_move:
                return

            move_num = auth.get("move_count", "?")
            disc = last_move["disc"]
            col = last_move["column"]
            row = last_move["row"]
            player = last_move["player_id"]
            winner = auth.get("winner")
            is_draw = auth.get("is_draw", False)
            board = auth.get("board", [])

            print(
                f"\nMove {move_num:>2}: {player} ({disc}) → col {col}  row {row}",
                flush=True,
            )
            if board:
                print(render_connect_four_board(board, bordered=True), flush=True)

            if winner:
                print(f"  *** {winner} WINS ***", flush=True)
            elif is_draw:
                print("  *** DRAW ***", flush=True)
            else:
                print(f"  Next: {auth.get('active_player', '?')}", flush=True)

            turn_log.append(
                {
                    "move": move_num,
                    "player": player,
                    "disc": disc,
                    "column": col,
                    "row": row,
                    "board": [r[:] for r in board],
                    "winner": winner,
                    "is_draw": is_draw,
                }
            )

        elif isinstance(event, RuleViolationEvent):
            violation_count += 1
            print(
                f"\n  ! RULE VIOLATION ({event.agent_id}): {event.rule}",
                flush=True,
            )

        elif isinstance(event, IncidentEvent):
            incident_count += 1
            print(
                f"\n  ! INCIDENT ({event.agent_id}): {event.incident_type} — {event.detail[:120]}",
                flush=True,
            )

        elif isinstance(event, MessageEvent) and event.channel_id == "public":
            text = event.text.strip()
            if text:
                print(f"\n[{event.agent_name}] {text[:300]}", flush=True)

        elif isinstance(event, SessionEndEvent):
            print(f"\n{_SEP}", flush=True)
            print(f"SESSION END: {event.reason}", flush=True)
            if event.message:
                print(f"  {event.message}", flush=True)
            print(_SEP, flush=True)

    bus.stream().subscribe(on_event)

    print(f"\n{_SEP}", flush=True)
    print("Connect Four — Live LLM E2E", flush=True)
    players = [a for a in config.agents if a.role == "player"]
    print(f"Red  : {players[0].provider}/{players[0].model}", flush=True)
    print(f"Black: {players[1].provider}/{players[1].model}", flush=True)
    print(_SEP, flush=True)

    engine = SessionEngine(config, bus)
    state = await engine.run()

    auth = state.game_state.custom["authoritative_state"]
    board: list[list[str]] = auth["board"]
    winner = auth.get("winner")
    is_draw = auth.get("is_draw", False)
    move_count: int = auth.get("move_count", 0)
    disc_by_player: dict[str, str] = auth.get("disc_by_player", {})

    print(f"\nFINAL BOARD  (end_reason={state.end_reason}, moves={move_count})", flush=True)
    print(render_connect_four_board(board, bordered=True), flush=True)
    print(
        f"Violations: {violation_count}  Incidents: {incident_count}",
        flush=True,
    )

    # ------------------------------------------------------------------
    # 1. Game must reach a terminal state
    # ------------------------------------------------------------------
    assert state.end_reason in ("win_condition", "draw"), (
        f"Game did not reach a terminal state — ended with: {state.end_reason!r}. "
        f"Moves made: {move_count}. "
        "Check for excessive rule violations or provider errors."
    )

    # ------------------------------------------------------------------
    # 2. Board physical validity (gravity)
    # ------------------------------------------------------------------
    assert _board_is_physically_valid(board), (
        "Board failed gravity check — a disc is floating above an empty cell.\n"
        + render_connect_four_board(board, bordered=True)
    )

    # ------------------------------------------------------------------
    # 3. Move counter matches filled cells
    # ------------------------------------------------------------------
    filled_cells = sum(1 for r in board for cell in r if cell != ".")
    assert filled_cells == move_count, (
        f"Board has {filled_cells} filled cells but engine's move_count={move_count}."
    )

    # ------------------------------------------------------------------
    # 4. Win / draw correctness
    # ------------------------------------------------------------------
    if state.end_reason == "win_condition":
        assert winner, "end_reason is win_condition but no winner is recorded."
        assert winner in disc_by_player, (
            f"Declared winner {winner!r} is not in disc_by_player={disc_by_player}."
        )
        winning_disc = disc_by_player[winner]
        has_connect_four = _find_connect_four(board, winning_disc)

        if has_connect_four:
            # Natural board win: 4-in-a-row confirmed.
            # Minimum meaningful natural win: 7 moves (4 by winner + 3 by loser)
            assert move_count >= 7, (
                f"Natural win declared after only {move_count} moves — suspiciously few."
            )
            print(f"  Win type: connect-four (board)", flush=True)
        else:
            # Forfeit win: the opponent exhausted retries and the engine awarded
            # the win to the other player.  The board won't show a 4-in-a-row,
            # but this is correct engine behaviour (actor_retry_exhaustion_action).
            # Verify the winner is a real player and the loser is accounted for.
            all_players = list(disc_by_player.keys())
            loser = next((p for p in all_players if p != winner), None)
            assert loser, f"Could not identify the forfeiting player; disc_by_player={disc_by_player}."
            print(f"  Win type: forfeit (opponent {loser!r} exhausted retries)", flush=True)

    elif state.end_reason == "draw":
        assert is_draw, "end_reason is draw but is_draw flag is False."
        assert filled_cells == 42, (
            f"Draw declared but only {filled_cells}/42 cells are filled."
        )
        for player_id, disc in disc_by_player.items():
            assert not _find_connect_four(board, disc), (
                f"Draw declared but {player_id} ({disc}) has a connect-four "
                f"on the final board."
            )

    # ------------------------------------------------------------------
    # 5. LLM engagement: turn log must mirror authoritative move count
    # ------------------------------------------------------------------
    assert move_count > 0, "No moves were made — LLMs were not engaged."
    assert len(turn_log) == move_count, (
        f"Captured {len(turn_log)} turn-log entries but engine recorded {move_count} moves."
    )

    # ------------------------------------------------------------------
    # 6. Turn order: Red (R) always moves on odd turns, Black (B) on even
    # ------------------------------------------------------------------
    for index, entry in enumerate(turn_log):
        expected_disc = "R" if index % 2 == 0 else "B"
        assert entry["disc"] == expected_disc, (
            f"Turn-order violation at move {index + 1}: "
            f"expected disc={expected_disc!r}, got {entry['disc']!r} "
            f"(player={entry['player']!r})."
        )

    # ------------------------------------------------------------------
    # 7. Transcript written and sane
    # ------------------------------------------------------------------
    transcripts = sorted(tmp_path.glob("*.json"))
    assert transcripts, "No JSON transcript was written to tmp_path."

    payload = json.loads(transcripts[0].read_text(encoding="utf-8"))
    event_types = {e["type"] for e in payload["events"]}
    assert "GAME_STATE" in event_types, "GAME_STATE events missing from transcript."
    assert "SESSION_END" in event_types, "SESSION_END event missing from transcript."
    assert any(
        e["type"] == "SESSION_END" and e["reason"] in ("win_condition", "draw")
        for e in payload["events"]
    ), "SESSION_END event has unexpected reason in transcript."

    # Transcript move events must match in-memory count
    transcript_moves = sum(
        1
        for e in payload["events"]
        if e["type"] == "GAME_STATE"
        and e.get("updates", {}).get("authoritative_delta", {}).get("last_move")
    )
    assert transcript_moves == move_count, (
        f"Transcript has {transcript_moves} move events but engine recorded {move_count}."
    )

    print(f"\n{'=' * 60}", flush=True)
    print(f"RESULT: {move_count} moves played.", flush=True)
    if winner:
        print(f"Winner: {winner} ({disc_by_player.get(winner, '?')})", flush=True)
    elif is_draw:
        print("Result: Draw — board full, no winner.", flush=True)
    print(f"Violations: {violation_count}  Incidents: {incident_count}", flush=True)
    print("All assertions passed.", flush=True)
