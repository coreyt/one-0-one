"""
Tests for src/tts/script_compiler.py (TDD — written before implementation).

Rules under test:
  1. JSON-only MESSAGE events are dropped.
  2. game_engine MESSAGE events are dropped.
  3. MONOLOGUE from Narrator/moderator role agents is dropped.
  4. MONOLOGUE from player agents is kept as "aside", first sentence only.
  5. MONOLOGUE that overlaps >55% with the next same-agent public MESSAGE is dropped.
  6. Public MESSAGE events produce "speech" lines with residual tags stripped.
  7. Private MESSAGE events are dropped (not in public script).
  8. Phase transitions detected from GAME_STATE events produce "narration" headers.
  9. _overlap_ratio computes token overlap correctly.
  10. _clean_monologue strips <thinking> and <feeling> tags.
  11. Consecutive same-phase GAME_STATE events do NOT produce duplicate headers.
  12. RadioLine dataclass has expected fields.
  13. tts_text is passed through from MESSAGE events.
  14. voice_settings is passed through from MESSAGE events.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers — minimal event dicts matching transcript schema
# ---------------------------------------------------------------------------

_SESSION_ID = "test_session"


def _msg(
    agent_id: str = "villager_1",
    agent_name: str = "Rosa Fields",
    text: str = "Hello world.",
    channel_id: str = "public",
    tts_text: str | None = None,
    tts_voice_settings: dict | None = None,
    turn_number: int = 1,
    role: str = "villager",
) -> dict:
    return {
        "type": "MESSAGE",
        "timestamp": "2026-01-01T00:00:00Z",
        "session_id": _SESSION_ID,
        "turn_number": turn_number,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "model": "test-model",
        "channel_id": channel_id,
        "recipient_id": None,
        "text": text,
        "tts_text": tts_text,
        "tts_voice_settings": tts_voice_settings or {},
        "is_parallel": False,
        "_role": role,  # extra metadata — compiler ignores unknown keys
    }


def _mono(
    agent_id: str = "villager_1",
    agent_name: str = "Rosa Fields",
    text: str = "I wonder if he's mafia.",
    turn_number: int = 1,
    role: str = "villager",
) -> dict:
    return {
        "type": "MONOLOGUE",
        "timestamp": "2026-01-01T00:00:00Z",
        "session_id": _SESSION_ID,
        "turn_number": turn_number,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "text": text,
        "_role": role,
    }


def _game_state(phase: str = "day_discussion", round_number: int = 1) -> dict:
    return {
        "type": "GAME_STATE",
        "timestamp": "2026-01-01T00:00:00Z",
        "session_id": _SESSION_ID,
        "turn_number": 1,
        "updates": {"phase": phase},
        "full_state": {
            "round": round_number,
            "scores": {},
            "winner": None,
            "is_over": False,
            "eliminated": [],
            "custom": {
                "authoritative_state": {
                    "phase": phase,
                    "round_number": round_number,
                }
            },
        },
    }


# ---------------------------------------------------------------------------
# Imports (will fail until src/tts/script_compiler.py exists)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _import_compiler():
    """Fail fast if the module does not exist yet."""
    import importlib
    global compile_radio_script, RadioLine, _overlap_ratio, _clean_monologue
    mod = importlib.import_module("src.tts.script_compiler")
    compile_radio_script = mod.compile_radio_script
    RadioLine = mod.RadioLine
    _overlap_ratio = mod._overlap_ratio
    _clean_monologue = mod._clean_monologue


# ---------------------------------------------------------------------------
# Unit: RadioLine dataclass
# ---------------------------------------------------------------------------


class TestRadioLine:
    def test_fields(self):
        line = RadioLine(
            speaker="Rosa Fields",
            agent_id="villager_1",
            delivery="speech",
            text="Hello.",
            tts_text=None,
            voice_settings={},
        )
        assert line.speaker == "Rosa Fields"
        assert line.agent_id == "villager_1"
        assert line.delivery == "speech"
        assert line.text == "Hello."
        assert line.tts_text is None
        assert line.voice_settings == {}

    def test_delivery_values(self):
        for delivery in ("narration", "aside", "speech"):
            line = RadioLine(
                speaker="X",
                agent_id="x",
                delivery=delivery,
                text=".",
                tts_text=None,
                voice_settings={},
            )
            assert line.delivery == delivery


# ---------------------------------------------------------------------------
# Unit: _overlap_ratio
# ---------------------------------------------------------------------------


class TestOverlapRatio:
    def test_identical(self):
        assert _overlap_ratio("the cat sat on the mat", "the cat sat on the mat") == pytest.approx(1.0)

    def test_no_overlap(self):
        assert _overlap_ratio("hello world", "goodbye moon") == pytest.approx(0.0)

    def test_partial(self):
        # "the cat sat" vs "the dog sat on the mat"
        # words_a = {the, cat, sat} (3 unique)
        # words_b = {the, dog, sat, on, mat} (5 unique)
        # overlap = {the, sat} = 2
        # ratio = 2/3 ≈ 0.667
        ratio = _overlap_ratio("the cat sat", "the dog sat on the mat")
        assert ratio == pytest.approx(2 / 3)

    def test_empty_a(self):
        assert _overlap_ratio("", "hello world") == pytest.approx(0.0)

    def test_empty_both(self):
        assert _overlap_ratio("", "") == pytest.approx(0.0)

    def test_above_threshold(self):
        # Should be above 0.55 to trigger bleed detection
        a = "The detective is the threat she must go"
        b = "The detective is the real threat she must go tonight"
        assert _overlap_ratio(a, b) > 0.55

    def test_below_threshold(self):
        # Should be below 0.55 — distinct content
        a = "He is suspicious"
        b = "Rosa makes a good point but I disagree"
        assert _overlap_ratio(a, b) < 0.55


# ---------------------------------------------------------------------------
# Unit: _clean_monologue
# ---------------------------------------------------------------------------


class TestCleanMonologue:
    def test_strips_thinking_tags(self):
        raw = "<thinking>Some deep thought.</thinking> Extra text."
        result = _clean_monologue(raw)
        assert "<thinking>" not in result
        assert "Some deep thought." in result

    def test_strips_feeling_tags(self):
        raw = "<feeling>calculating</feeling>I should wait."
        result = _clean_monologue(raw)
        assert "<feeling>" not in result
        assert "calculating" not in result
        assert "I should wait." in result

    def test_strips_both(self):
        raw = "<thinking><feeling>paranoid</feeling>He's watching me.</thinking>"
        result = _clean_monologue(raw)
        assert "<thinking>" not in result
        assert "<feeling>" not in result
        assert "He's watching me." in result

    def test_no_tags(self):
        raw = "Simple text."
        assert _clean_monologue(raw) == "Simple text."

    def test_multiline_thinking(self):
        raw = "<thinking>\nLine one.\nLine two.\n</thinking>"
        result = _clean_monologue(raw)
        assert "Line one." in result
        assert "<thinking>" not in result


# ---------------------------------------------------------------------------
# Integration: compile_radio_script
# ---------------------------------------------------------------------------


class TestCompileRadioScript:

    # ── Rule 1: JSON-only messages are dropped ────────────────────────────

    def test_drops_json_only_message(self):
        events = [_msg(text='{"target": "detective"}')]
        lines = compile_radio_script({"events": events, "agents": []})
        assert not any(l.delivery == "speech" for l in lines)

    def test_drops_json_with_whitespace(self):
        events = [_msg(text='  {"vote": "villager_1"}  ')]
        lines = compile_radio_script({"events": events, "agents": []})
        assert not any(l.delivery == "speech" for l in lines)

    def test_keeps_non_json_message(self):
        events = [_msg(text="I think it's Rosa.")]
        lines = compile_radio_script({"events": events, "agents": []})
        assert any(l.delivery == "speech" for l in lines)

    # ── Rule 2: game_engine messages are dropped ──────────────────────────

    def test_drops_game_engine_message(self):
        events = [_msg(agent_id="game_engine", agent_name="game_engine", text="Night 1 result: no one died.")]
        lines = compile_radio_script({"events": events, "agents": []})
        assert not any(l.delivery == "speech" for l in lines)

    def test_keeps_narrator_public_message(self):
        events = [_msg(agent_id="narrator", agent_name="Narrator", text="The village sleeps.")]
        lines = compile_radio_script({"events": events, "agents": []})
        assert any(l.delivery == "speech" for l in lines)

    # ── Rule 3: Narrator/moderator MONOLOGUE dropped ─────────────────────

    def test_drops_narrator_monologue(self):
        events = [_mono(agent_id="narrator", agent_name="Narrator", text="Let me plan the narration.")]
        lines = compile_radio_script({"events": events, "agents": []})
        assert not any(l.delivery == "aside" for l in lines)

    def test_drops_moderator_monologue(self):
        events = [_mono(agent_id="moderator", agent_name="Moderator", text="I will adjudicate this.")]
        lines = compile_radio_script({"events": events, "agents": []})
        assert not any(l.delivery == "aside" for l in lines)

    def test_keeps_player_monologue(self):
        events = [_mono(agent_id="villager_1", agent_name="Rosa Fields", text="He's definitely mafia.")]
        lines = compile_radio_script({"events": events, "agents": []})
        assert any(l.delivery == "aside" for l in lines)

    # ── Rule 4: Player MONOLOGUE → aside, first sentence only ────────────

    def test_aside_first_sentence_only(self):
        text = "First sentence. Second sentence. Third sentence."
        events = [_mono(text=text)]
        lines = compile_radio_script({"events": events, "agents": []})
        asides = [l for l in lines if l.delivery == "aside"]
        assert len(asides) == 1
        # Should contain first sentence and not second
        assert "First sentence" in asides[0].text
        assert "Second sentence" not in asides[0].text

    def test_aside_single_sentence(self):
        text = "Only one thought."
        events = [_mono(text=text)]
        lines = compile_radio_script({"events": events, "agents": []})
        asides = [l for l in lines if l.delivery == "aside"]
        assert asides[0].text == "Only one thought."

    def test_aside_strips_feeling_tags(self):
        text = "<feeling>calculating</feeling>He is the target."
        events = [_mono(text=text)]
        lines = compile_radio_script({"events": events, "agents": []})
        asides = [l for l in lines if l.delivery == "aside"]
        assert "<feeling>" not in asides[0].text
        assert "calculating" not in asides[0].text

    def test_aside_strips_thinking_tags(self):
        text = "<thinking>Deep thought here.</thinking>"
        events = [_mono(text=text)]
        lines = compile_radio_script({"events": events, "agents": []})
        asides = [l for l in lines if l.delivery == "aside"]
        assert "<thinking>" not in asides[0].text
        assert "Deep thought here." in asides[0].text

    # ── Rule 5: Bleed detection — aside dropped if >55% overlap with speech ──

    def test_drops_aside_when_bleed_detected(self):
        # Monologue and speech share almost the same words
        mono_text = "The detective is the threat she must go."
        speech_text = "The detective is the real threat, she must go."
        events = [
            _mono(agent_id="mafia_don", agent_name="Don Corvo", text=mono_text, turn_number=1),
            _msg(agent_id="mafia_don", agent_name="Don Corvo", text=speech_text, turn_number=2),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        asides = [l for l in lines if l.delivery == "aside"]
        assert len(asides) == 0

    def test_keeps_aside_when_no_bleed(self):
        # Monologue and speech are distinct
        mono_text = "He is my prime suspect."
        speech_text = "I think Rosa makes a compelling case, but I'm not so sure."
        events = [
            _mono(agent_id="mafia_don", agent_name="Don Corvo", text=mono_text, turn_number=1),
            _msg(agent_id="mafia_don", agent_name="Don Corvo", text=speech_text, turn_number=2),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        asides = [l for l in lines if l.delivery == "aside"]
        assert len(asides) == 1

    def test_bleed_only_checks_same_agent(self):
        # Monologue from agent A, speech from agent B — no bleed suppression
        mono_text = "I think it's Rosa."
        speech_text = "I think it's Rosa, she's been quiet all game."
        events = [
            _mono(agent_id="mafia_don", agent_name="Don Corvo", text=mono_text, turn_number=1),
            _msg(agent_id="villager_1", agent_name="Rosa Fields", text=speech_text, turn_number=2),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        asides = [l for l in lines if l.delivery == "aside"]
        assert len(asides) == 1  # No bleed — different agents

    # ── Rule 6: Public MESSAGE → speech, residual tags stripped ──────────

    def test_speech_strips_thinking_tags(self):
        events = [_msg(text="<thinking>Private thought.</thinking>Public words.")]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert len(speeches) == 1
        assert "<thinking>" not in speeches[0].text
        assert "Public words." in speeches[0].text

    def test_speech_strips_feeling_tags(self):
        events = [_msg(text="<feeling>nervous</feeling>I'm not so sure.")]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert "<feeling>" not in speeches[0].text

    def test_speech_preserves_text(self):
        events = [_msg(text="Hello, how are you today?")]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert speeches[0].text == "Hello, how are you today?"

    # ── Rule 7: Private messages are excluded ─────────────────────────────

    def test_drops_private_message(self):
        events = [_msg(channel_id="private", text="Just between us.")]
        lines = compile_radio_script({"events": events, "agents": []})
        assert not any(l.delivery == "speech" for l in lines)

    def test_drops_team_message(self):
        events = [_msg(channel_id="mafia", text="Kill the detective.")]
        lines = compile_radio_script({"events": events, "agents": []})
        assert not any(l.delivery == "speech" for l in lines)

    # ── Rule 8: Phase transitions produce narration headers ───────────────

    def test_phase_transition_produces_narration(self):
        events = [
            _game_state(phase="night_mafia_discussion", round_number=1),
            _game_state(phase="day_discussion", round_number=1),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        narrations = [l for l in lines if l.delivery == "narration"]
        assert len(narrations) >= 1

    def test_narration_mentions_phase(self):
        events = [
            _game_state(phase="day_discussion", round_number=1),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        narrations = [l for l in lines if l.delivery == "narration"]
        # At least one header should mention "day" or "morning" or "round"
        text_lower = " ".join(l.text.lower() for l in narrations)
        assert any(word in text_lower for word in ("day", "morning", "dawn", "round", "1", "one"))

    def test_night_narration_mentions_night(self):
        events = [
            _game_state(phase="night_mafia_discussion", round_number=1),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        narrations = [l for l in lines if l.delivery == "narration"]
        text_lower = " ".join(l.text.lower() for l in narrations)
        assert any(word in text_lower for word in ("night", "dark", "shadow", "1", "one"))

    # ── Rule 9: No duplicate headers for same phase ───────────────────────

    def test_no_duplicate_headers_same_phase(self):
        events = [
            _game_state(phase="day_discussion", round_number=1),
            _game_state(phase="day_discussion", round_number=1),
            _game_state(phase="day_discussion", round_number=1),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        narrations = [l for l in lines if l.delivery == "narration"]
        # Should only emit one header for the first occurrence
        assert len(narrations) == 1

    def test_new_phase_gets_new_header(self):
        events = [
            _game_state(phase="night_mafia_discussion", round_number=1),
            _msg(text="Who do we target?"),
            _game_state(phase="day_discussion", round_number=1),
            _msg(text="Good morning everyone."),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        narrations = [l for l in lines if l.delivery == "narration"]
        assert len(narrations) == 2

    # ── Rule 13: tts_text passed through ─────────────────────────────────

    def test_tts_text_passed_through(self):
        events = [_msg(text="Hello.", tts_text="[excited]Hello.")]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert speeches[0].tts_text == "[excited]Hello."

    def test_tts_text_none_when_absent(self):
        events = [_msg(text="Hello.", tts_text=None)]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert speeches[0].tts_text is None

    # ── Rule 14: voice_settings passed through ────────────────────────────

    def test_voice_settings_passed_through(self):
        events = [_msg(text="Hello.", tts_voice_settings={"stability": 0.3, "style": 0.7})]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert speeches[0].voice_settings == {"stability": 0.3, "style": 0.7}

    def test_voice_settings_empty_when_absent(self):
        events = [_msg(text="Hello.", tts_voice_settings={})]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert speeches[0].voice_settings == {}

    # ── Ordering ──────────────────────────────────────────────────────────

    def test_ordering_preserved(self):
        events = [
            _game_state(phase="night_mafia_discussion", round_number=1),
            _mono(agent_id="mafia_don", agent_name="Don Corvo", text="I'll target her."),
            _msg(agent_id="mafia_don", agent_name="Don Corvo", text="Let's vote for the detective."),
        ]
        lines = compile_radio_script({"events": events, "agents": []})
        deliveries = [l.delivery for l in lines]
        # narration before aside before speech
        assert deliveries.index("narration") < deliveries.index("aside")
        assert deliveries.index("aside") < deliveries.index("speech")

    def test_speaker_populated(self):
        events = [_msg(agent_name="Rosa Fields", text="Hello.")]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert speeches[0].speaker == "Rosa Fields"

    def test_agent_id_populated(self):
        events = [_msg(agent_id="villager_1", text="Hello.")]
        lines = compile_radio_script({"events": events, "agents": []})
        speeches = [l for l in lines if l.delivery == "speech"]
        assert speeches[0].agent_id == "villager_1"
