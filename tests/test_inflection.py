"""
TDD tests for src/tts/inflection.py — feeling registry and text processor.

Run with: uv run pytest tests/test_inflection.py -v
"""

import pytest

from src.tts.inflection import (
    EXTERNAL_FEELINGS,
    INTERNAL_FEELINGS,
    REGISTRY,
    ProcessedText,
    feeling_instructions,
    process_text,
    strip_feeling_tags,
)


# ---------------------------------------------------------------------------
# strip_feeling_tags
# ---------------------------------------------------------------------------


def test_strip_removes_single_tag():
    assert strip_feeling_tags("Hello <feeling>nervous</feeling> world") == "Hello world"


def test_strip_removes_multiple_tags():
    result = strip_feeling_tags(
        "<feeling>angry</feeling> Stop that. <feeling>resigned</feeling> Fine."
    )
    assert result == "Stop that. Fine."


def test_strip_preserves_plain_text():
    assert strip_feeling_tags("no tags here") == "no tags here"


def test_strip_empty_string():
    assert strip_feeling_tags("") == ""


def test_strip_case_insensitive():
    result = strip_feeling_tags("Hi <FEELING>excited</FEELING> there")
    assert result == "Hi there"


def test_strip_collapses_extra_spaces():
    result = strip_feeling_tags("A <feeling>sad</feeling>  B")
    assert "  " not in result
    assert "A" in result and "B" in result


def test_strip_tag_at_start_of_string():
    result = strip_feeling_tags("<feeling>nervous</feeling>I'm scared.")
    assert "<feeling>" not in result
    assert "I'm scared." in result


def test_strip_tag_at_end_of_string():
    result = strip_feeling_tags("This is hard. <feeling>resigned</feeling>")
    assert result.strip() == "This is hard."


# ---------------------------------------------------------------------------
# process_text — ProcessedText fields
# ---------------------------------------------------------------------------


def test_process_text_returns_processed_text_instance():
    result = process_text("hello")
    assert isinstance(result, ProcessedText)


def test_process_text_plain_text_unchanged():
    pt = process_text("no feelings here")
    assert pt.clean == "no feelings here"
    assert pt.v3_annotated == "no feelings here"
    assert pt.voice_settings == {}


def test_process_text_clean_strips_tag():
    pt = process_text("Hello <feeling>nervous</feeling> world")
    assert pt.clean == "Hello world"
    assert "<feeling>" not in pt.clean


def test_process_text_v3_annotated_has_no_feeling_tags():
    pt = process_text("Hello <feeling>nervous</feeling> world")
    assert "<feeling>" not in pt.v3_annotated


def test_process_text_known_feeling_with_v3_tag_inserts_bracket_tag():
    # nervous maps to a v3_tag — v3_annotated should contain [something]
    pt = process_text("<feeling>nervous</feeling> I think we should wait.")
    assert "[" in pt.v3_annotated and "]" in pt.v3_annotated


def test_process_text_known_feeling_without_v3_tag_leaves_no_bracket():
    # confident has no v3_tag — tag is stripped, no bracket annotation inserted
    pt = process_text("<feeling>confident</feeling> I am sure of this.")
    assert "<feeling>" not in pt.v3_annotated
    # The word 'confident' is not inserted as literal text
    assert "[confident]" not in pt.v3_annotated


def test_process_text_v3_tag_appears_at_feeling_position():
    # The v3 bracket should appear inline, not always at the very start
    pt = process_text("First sentence. <feeling>nervous</feeling> Second sentence.")
    assert pt.v3_annotated.startswith("First sentence.")


def test_process_text_unknown_feeling_stripped_from_clean():
    pt = process_text("<feeling>blarg</feeling> Hello")
    assert pt.clean == "Hello"
    assert "<feeling>" not in pt.clean


def test_process_text_unknown_feeling_stripped_from_v3():
    pt = process_text("<feeling>blarg</feeling> Hello")
    assert "<feeling>" not in pt.v3_annotated
    assert "[blarg]" not in pt.v3_annotated


def test_process_text_unknown_feeling_no_voice_settings():
    pt = process_text("<feeling>blarg</feeling> Hello")
    assert pt.voice_settings == {}


def test_process_text_known_feeling_populates_voice_settings():
    pt = process_text("<feeling>angry</feeling> Stop!")
    assert pt.voice_settings  # non-empty
    assert "stability" in pt.voice_settings or "style" in pt.voice_settings


def test_process_text_multiple_feelings_min_stability():
    # angry stability < confident stability — min wins (most expressive)
    pt_angry = process_text("<feeling>angry</feeling> A.")
    pt_conf = process_text("<feeling>confident</feeling> A.")
    pt_both = process_text("<feeling>angry</feeling> A. <feeling>confident</feeling> B.")
    if "stability" in pt_angry.voice_settings and "stability" in pt_conf.voice_settings:
        assert pt_both.voice_settings["stability"] == min(
            pt_angry.voice_settings["stability"],
            pt_conf.voice_settings["stability"],
        )


def test_process_text_multiple_feelings_max_style():
    # angry style > sad style (by registry definition) — max wins
    pt_angry = process_text("<feeling>angry</feeling> A.")
    pt_sad = process_text("<feeling>sad</feeling> A.")
    pt_both = process_text("<feeling>angry</feeling> A. <feeling>sad</feeling> B.")
    if "style" in pt_angry.voice_settings and "style" in pt_sad.voice_settings:
        assert pt_both.voice_settings["style"] == max(
            pt_angry.voice_settings["style"],
            pt_sad.voice_settings["style"],
        )


def test_process_text_no_double_spaces_in_clean():
    pt = process_text("A  <feeling>angry</feeling>  B")
    assert "  " not in pt.clean


def test_process_text_no_double_spaces_in_v3():
    pt = process_text("A  <feeling>angry</feeling>  B")
    assert "  " not in pt.v3_annotated


def test_process_text_multiple_tags_all_converted():
    pt = process_text(
        "<feeling>excited</feeling> Great news! <feeling>nervous</feeling> But I'm worried."
    )
    assert "<feeling>" not in pt.clean
    assert "<feeling>" not in pt.v3_annotated


def test_process_text_preserves_text_around_tag():
    pt = process_text("Before. <feeling>angry</feeling> After.")
    assert "Before." in pt.clean
    assert "After." in pt.clean


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------


def test_registry_is_nonempty():
    assert len(REGISTRY) > 0


def test_all_registry_entries_have_label():
    for name, inf in REGISTRY.items():
        assert inf.label, f"Inflection {name!r} missing label"


def test_all_registry_entries_have_valid_scope():
    for name, inf in REGISTRY.items():
        assert inf.scope in ("external", "internal", "both"), (
            f"Invalid scope {inf.scope!r} for {name!r}"
        )


def test_registry_has_at_least_five_external_feelings():
    externals = [n for n, inf in REGISTRY.items() if inf.scope in ("external", "both")]
    assert len(externals) >= 5, f"Only {len(externals)} external feelings defined"


def test_registry_has_at_least_four_internal_feelings():
    internals = [n for n, inf in REGISTRY.items() if inf.scope in ("internal", "both")]
    assert len(internals) >= 4, f"Only {len(internals)} internal feelings defined"


def test_registry_voice_settings_in_valid_range():
    for name, inf in REGISTRY.items():
        if inf.stability is not None:
            assert 0.0 <= inf.stability <= 1.0, f"{name!r} stability out of range"
        if inf.style is not None:
            assert 0.0 <= inf.style <= 1.0, f"{name!r} style out of range"


def test_registry_v3_tags_are_strings_or_none():
    for name, inf in REGISTRY.items():
        assert inf.v3_tag is None or isinstance(inf.v3_tag, str), (
            f"{name!r} v3_tag must be str or None"
        )


# ---------------------------------------------------------------------------
# EXTERNAL_FEELINGS / INTERNAL_FEELINGS convenience lists
# ---------------------------------------------------------------------------


def test_external_feelings_list_nonempty():
    assert len(EXTERNAL_FEELINGS) >= 5


def test_internal_feelings_list_nonempty():
    assert len(INTERNAL_FEELINGS) >= 4


def test_external_feelings_all_in_registry():
    for name in EXTERNAL_FEELINGS:
        assert name in REGISTRY, f"{name!r} in EXTERNAL_FEELINGS but not in REGISTRY"


def test_internal_feelings_all_in_registry():
    for name in INTERNAL_FEELINGS:
        assert name in REGISTRY, f"{name!r} in INTERNAL_FEELINGS but not in REGISTRY"


def test_external_feelings_have_external_or_both_scope():
    for name in EXTERNAL_FEELINGS:
        assert REGISTRY[name].scope in ("external", "both"), (
            f"{name!r} listed in EXTERNAL_FEELINGS but scope={REGISTRY[name].scope!r}"
        )


def test_internal_feelings_have_internal_or_both_scope():
    for name in INTERNAL_FEELINGS:
        assert REGISTRY[name].scope in ("internal", "both"), (
            f"{name!r} listed in INTERNAL_FEELINGS but scope={REGISTRY[name].scope!r}"
        )


# ---------------------------------------------------------------------------
# feeling_instructions
# ---------------------------------------------------------------------------


def test_feeling_instructions_returns_string():
    assert isinstance(feeling_instructions(), str)


def test_feeling_instructions_nonempty():
    assert len(feeling_instructions()) > 100


def test_feeling_instructions_mentions_each_external_feeling():
    instr = feeling_instructions()
    for name in EXTERNAL_FEELINGS:
        assert name in instr, f"External feeling {name!r} missing from instructions"


def test_feeling_instructions_mentions_each_internal_feeling():
    instr = feeling_instructions()
    for name in INTERNAL_FEELINGS:
        assert name in instr, f"Internal feeling {name!r} missing from instructions"


def test_feeling_instructions_shows_xml_tag_format():
    instr = feeling_instructions()
    assert "<feeling>" in instr


def test_feeling_instructions_shows_closing_tag():
    instr = feeling_instructions()
    assert "</feeling>" in instr
