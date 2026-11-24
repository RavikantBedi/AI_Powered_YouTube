"""Tests for the deterministic pipeline logic — the parts that must be correct
regardless of what any model returns."""
from __future__ import annotations

from itertools import pairwise

import pytest

from app.agents.critic import ScriptCriticAgent
from app.agents.image_qa import png_dimensions
from app.agents.planner import words_for_duration
from app.agents.script import clean_script, count_words
from app.agents.storyboard import StoryboardAgent, split_sentences
from app.agents.subtitles import _split_cue_text, _timestamp, build_cues, render_srt, render_vtt
from app.agents.voice import clean_for_tts
from app.providers.anthropic_llm import extract_json
from app.schemas.core import Scene, Script, Storyboard

# ── script hygiene ──────────────────────────────────────────────────────

def test_clean_script_strips_non_narration_markup():
    raw = (
        "## Section One\n"
        "**Narrator:**\n"
        "यह कहानी 1971 की है। [MUSIC] सर्दियों की रात थी।\n"
        "SCENE 2: कुछ और\n"
        "```\ncode\n```\n"
        "[TENSION PEAK]\n"
        "अंत में सब बदल गया।"
    )
    cleaned = clean_script(raw)
    assert "##" not in cleaned
    assert "[MUSIC]" not in cleaned
    assert "SCENE 2" not in cleaned
    assert "```" not in cleaned
    # The one permitted marker survives — image alignment depends on it.
    assert "[TENSION PEAK]" in cleaned
    assert "यह कहानी 1971 की है।" in cleaned


def test_count_words_handles_devanagari():
    assert count_words("यह एक परीक्षण है") == 4
    assert count_words("  spaced   out  words ") == 3
    assert count_words("") == 0


def test_clean_for_tts_removes_markers():
    assert "[TENSION PEAK]" not in clean_for_tts("पहला [TENSION PEAK] दूसरा")
    assert clean_for_tts("a  *b*  #c") == "a b c"


# ── duration/word budgeting ─────────────────────────────────────────────

def test_words_for_duration_is_language_aware():
    # 12 minutes of Hindi narration at the documentary rate.
    assert words_for_duration(720, "hi-IN") == 1560
    # English is spoken faster, so the same runtime needs more words.
    assert words_for_duration(720, "en-US") > words_for_duration(720, "hi-IN")


# ── scene decomposition ─────────────────────────────────────────────────

def test_split_sentences_handles_danda_and_periods():
    parts = split_sentences("पहला वाक्य। दूसरा वाक्य। Third sentence. Fourth one.")
    assert len(parts) == 4
    assert parts[0] == "पहला वाक्य।"


@pytest.mark.asyncio
async def test_storyboard_scenes_are_bounded_and_ordered(store):
    from app.agents.base import AgentContext

    text = " ".join(f"यह वाक्य संख्या {i} है और इसमें कुछ शब्द हैं।" for i in range(1, 41))
    ctx = AgentContext(project_id="test-project", channel_id="anidoc", dna={}, store=store)
    board = await StoryboardAgent().execute(ctx, script=Script(text=text, word_count=count_words(text)))

    assert board.scenes, "expected scenes to be produced"
    # Every scene must be long enough to show and short enough to hold attention.
    assert all(s.duration_seconds >= 3.0 for s in board.scenes)
    # Scene ids and indices must be contiguous and ordered.
    assert [s.index for s in board.scenes] == list(range(len(board.scenes)))
    # Start times must be monotonically increasing and consistent with durations.
    for prev, nxt in pairwise(board.scenes):
        assert nxt.start_seconds == pytest.approx(prev.start_seconds + prev.duration_seconds,
                                                  abs=0.02)
    # No narration may be lost in the split.
    joined = " ".join(s.narration_text for s in board.scenes)
    assert count_words(joined) == count_words(text)


@pytest.mark.asyncio
async def test_storyboard_marks_tension_peaks(store):
    from app.agents.base import AgentContext

    text = "पहला वाक्य है यहाँ। [TENSION PEAK] दूसरा वाक्य आता है अब।"
    ctx = AgentContext(project_id="test-project", channel_id="anidoc", dna={}, store=store)
    board = await StoryboardAgent().execute(ctx, script=Script(text=text, word_count=9))
    assert any(s.mood == "tense" for s in board.scenes)
    assert all("[TENSION PEAK]" not in s.narration_text for s in board.scenes)


# ── subtitles ───────────────────────────────────────────────────────────

def test_timestamp_formats():
    assert _timestamp(0) == "00:00:00,000"
    assert _timestamp(3661.5) == "01:01:01,500"
    assert _timestamp(3661.5, vtt=True) == "01:01:01.500"


def test_split_cue_text_respects_width():
    long_text = "यह एक बहुत लंबा वाक्य है, " * 8
    for chunk in _split_cue_text(long_text):
        assert len(chunk) <= 84


def _board_with_audio() -> Storyboard:
    scenes = [
        Scene(scene_id="S0001", index=0, narration_text="पहला वाक्य यहाँ है।",
              start_seconds=0.0, duration_seconds=4.0, audio_path="a.mp3"),
        Scene(scene_id="S0002", index=1, narration_text="दूसरा वाक्य अब आता है।",
              start_seconds=4.0, duration_seconds=5.0, audio_path="b.mp3"),
    ]
    return Storyboard(scenes=scenes, total_duration=9.0)


def test_cues_are_sequential_and_non_overlapping():
    cues = build_cues(_board_with_audio())
    assert cues
    for prev, nxt in pairwise(cues):
        assert prev["end"] <= nxt["start"] + 1e-6, "subtitle cues must not overlap"
        assert nxt["start"] >= prev["start"]
    # The final cue must not run past the film.
    assert cues[-1]["end"] <= 9.0 + 1e-6


def test_cues_skip_scenes_without_audio():
    board = _board_with_audio()
    board.scenes[1].audio_path = ""
    assert all(c["scene_id"] == "S0001" for c in build_cues(board))


def test_srt_and_vtt_render_valid_structure():
    cues = build_cues(_board_with_audio())
    srt = render_srt(cues)
    assert srt.startswith("1\n")
    assert " --> " in srt
    vtt = render_vtt(cues)
    assert vtt.startswith("WEBVTT")
    assert "." in vtt.split("-->")[0]


# ── critic structural checks ────────────────────────────────────────────

def _ctx(store):
    from app.agents.base import AgentContext
    return AgentContext(project_id="test-project", channel_id="anidoc",
                        dna={"script": {"word_count_target": {"tolerance_pct": 5}}},
                        store=store)


def test_critic_flags_length_drift(store):
    script = Script(text="शब्द " * 100, word_count=100)
    result = ScriptCriticAgent()._structural_checks(script, 1000, _ctx(store))
    assert any("Length off target" in p for p in result["problems"])
    assert result["penalties"].get("brand_alignment", 0) > 0


def test_critic_flags_markup_and_missing_peaks(store):
    script = Script(text="## Heading\n**bold** [MUSIC] text here", word_count=6)
    result = ScriptCriticAgent()._structural_checks(script, 6, _ctx(store))
    problems = " ".join(result["problems"])
    assert "markup" in problems
    assert "tension peaks" in problems


def test_critic_accepts_clean_script(store):
    text = " ".join(["साफ़ वाक्य यहाँ पर है।"] * 50) + " [TENSION PEAK] " + \
           " ".join(["अलग शुरुआत वाला वाक्य।"] * 10)
    script = Script(text=text, word_count=count_words(text))
    result = ScriptCriticAgent()._structural_checks(script, script.word_count, _ctx(store))
    assert not any("markup" in p for p in result["problems"])


# ── JSON extraction ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here is the result:\n```\n{"a": 1}\n```\nHope that helps!', {"a": 1}),
    ('Sure! {"a": 1}', {"a": 1}),
    ('[{"a": 1}]', [{"a": 1}]),
])
def test_extract_json_variants(raw, expected):
    assert extract_json(raw) == expected


def test_extract_json_rejects_garbage():
    with pytest.raises(ValueError):
        extract_json("there is no json here at all")


# ── image dimension probing ─────────────────────────────────────────────

def test_png_dimensions_reads_header(tmp_path):
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n"
                    + chunk(b"IHDR", struct.pack(">IIBBBBB", 1536, 864, 8, 6, 0, 0, 0)))
    assert png_dimensions(png) == (1536, 864)


def test_png_dimensions_returns_none_for_missing(tmp_path):
    assert png_dimensions(tmp_path / "nope.png") is None
