"""
Charter-lyrics integration contract test.

Pins the contract Deadbolt is wiring up between core.lyrics and core.charter:
when synced lyrics are passed into build_chart, ChartData.lyrics_events gets
populated AND the written .chart's [Events] section emits the Clone Hero
vocal convention:

    tick = E "phrase_start"
    tick = E "lyric <word_or_syllable>"
    tick = E "phrase_end"

The surface doesn't exist yet (build_chart has no lyrics kwarg; ChartData
has no lyrics_events field). This file auto-skips those tests until the
integration lands, then starts biting. Any shape mismatch when Deadbolt
ships flips the PR red.

What's NOT skipped: the shape-only helper tests that encode the Clone Hero
format itself. Those pass today and lock the writer contract regardless of
when the pipeline wiring arrives.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

import pytest

from core.charter import ChartData, build_chart
from core.chart_writer import ChartMeta, write_chart
from core.lyrics import LyricsData


# ---------- feature-presence detection -------------------------------------

HAS_LYRICS_EVENTS = "lyrics_events" in ChartData.__dataclass_fields__

try:
    import inspect
    _BUILD_SIG = inspect.signature(build_chart)
    HAS_LYRICS_KWARG = "lyrics" in _BUILD_SIG.parameters
except (TypeError, ValueError):
    HAS_LYRICS_KWARG = False


_PHRASE_START_RE = re.compile(r'^\s*(\d+)\s*=\s*E\s+"phrase_start"\s*$', re.MULTILINE)
_PHRASE_END_RE   = re.compile(r'^\s*(\d+)\s*=\s*E\s+"phrase_end"\s*$', re.MULTILINE)
_LYRIC_RE        = re.compile(r'^\s*(\d+)\s*=\s*E\s+"lyric\s+(.+?)"\s*$', re.MULTILINE)


def _events_body(text: str) -> str:
    """Extract the body of the [Events] block from a serialized .chart."""
    m = re.search(r"\[Events\]\s*\{(.*?)\}", text, re.DOTALL)
    assert m, "Events section missing from chart output"
    return m.group(1)


# ---------- forward-looking contract tests ---------------------------------

pytestmark_integration = pytest.mark.skipif(
    not (HAS_LYRICS_EVENTS and HAS_LYRICS_KWARG),
    reason="charter lyrics integration not landed yet "
           f"(lyrics_events={HAS_LYRICS_EVENTS}, lyrics kwarg={HAS_LYRICS_KWARG})",
)


@pytest.fixture
def librosa_available():
    try:
        import librosa  # noqa: F401
    except ImportError:
        pytest.skip("librosa not installed in this environment")


@pytest.fixture
def analysis(librosa_available):
    from core.analyzer import analyze_audio
    from tests.fixtures.gen_fixture import PULSE_PATH, generate_pulse_train
    if not PULSE_PATH.exists():
        generate_pulse_train(PULSE_PATH)
    return analyze_audio(PULSE_PATH)


@pytest.fixture
def sample_lyrics() -> LyricsData:
    lines = ["First line", "Second line", "Third line here", "Fourth"]
    times = [1.5, 4.0, 7.25, 12.0]
    return LyricsData(lines=lines, times_s=times)


@pytestmark_integration
class TestChartDataLyricsField:
    def test_lyrics_events_populated_when_lyrics_passed(self, analysis, sample_lyrics):
        cd = build_chart(analysis, drums=None, lyrics=sample_lyrics)
        assert cd.lyrics_events, "lyrics_events should be populated"
        # Shape heuristic: at least as many events as lyric lines (phrase_start
        # + lyric + phrase_end per line → 3× would be the richest form).
        assert len(cd.lyrics_events) >= len(sample_lyrics.times_s)

    def test_lyrics_absent_leaves_field_empty(self, analysis):
        cd = build_chart(analysis, drums=None)
        assert cd.lyrics_events == [] or cd.lyrics_events is None


@pytestmark_integration
class TestChartEventsOutput:
    def test_events_contains_phrase_start_lyric_phrase_end(
        self, analysis, sample_lyrics, tmp_path: Path
    ):
        cd = build_chart(analysis, drums=None, lyrics=sample_lyrics)
        out = write_chart(tmp_path / "notes.chart", cd, ChartMeta(name="x", artist="y"))
        body = _events_body(out.read_text(encoding="utf-8"))

        starts = _PHRASE_START_RE.findall(body)
        ends = _PHRASE_END_RE.findall(body)
        found_lyrics = _LYRIC_RE.findall(body)

        assert len(starts) >= 1, "no phrase_start events emitted"
        assert len(ends) >= 1, "no phrase_end events emitted"
        assert len(found_lyrics) >= len(sample_lyrics.times_s), \
            "lyric event count below input line count"

    def test_phrase_start_before_matching_phrase_end(
        self, analysis, sample_lyrics, tmp_path: Path
    ):
        """Starts must precede ends tick-wise so Clone Hero's vocal track
        parser doesn't drop the whole phrase."""
        cd = build_chart(analysis, drums=None, lyrics=sample_lyrics)
        out = write_chart(tmp_path / "notes.chart", cd, ChartMeta(name="x", artist="y"))
        body = _events_body(out.read_text(encoding="utf-8"))

        starts = sorted(int(t) for t in _PHRASE_START_RE.findall(body))
        ends = sorted(int(t) for t in _PHRASE_END_RE.findall(body))
        # Pair-wise: nth start < nth end.
        for s, e in zip(starts, ends):
            assert s < e, f"phrase_start {s} not before phrase_end {e}"

    def test_lyric_text_preserved(self, analysis, sample_lyrics, tmp_path: Path):
        cd = build_chart(analysis, drums=None, lyrics=sample_lyrics)
        out = write_chart(tmp_path / "notes.chart", cd, ChartMeta(name="x", artist="y"))
        body = _events_body(out.read_text(encoding="utf-8"))

        lyric_bodies = [payload for _, payload in _LYRIC_RE.findall(body)]
        joined = " ".join(lyric_bodies).lower()
        for word in ["first", "second", "third", "fourth"]:
            assert word in joined, f"lyric text '{word}' missing from Events body"


# ---------- writer-contract tests that pass TODAY --------------------------

class TestEventsVocabularyParsing:
    """Lock the regex vocabulary the Clone Hero vocal track uses, so if
    chart_writer changes its Events quoting style we catch it immediately."""

    def test_phrase_start_regex_matches_spec(self):
        assert _PHRASE_START_RE.match('  960 = E "phrase_start"')

    def test_phrase_end_regex_matches_spec(self):
        assert _PHRASE_END_RE.match('  1920 = E "phrase_end"')

    def test_lyric_regex_captures_single_word(self):
        m = _LYRIC_RE.match('  1024 = E "lyric hello"')
        assert m and m.group(2) == "hello"

    def test_lyric_regex_captures_syllable_with_dash(self):
        m = _LYRIC_RE.match('  1024 = E "lyric syl-"')
        assert m and m.group(2) == "syl-"

    def test_lyric_regex_captures_multi_word(self):
        m = _LYRIC_RE.match('  1024 = E "lyric hello world"')
        assert m and m.group(2) == "hello world"

    def test_section_event_does_not_match_lyric_pattern(self):
        """`E "section intro"` must not get picked up as a lyric event."""
        assert not _PHRASE_START_RE.match('  0 = E "section intro"')
        assert not _PHRASE_END_RE.match('  0 = E "section intro"')
        assert not _LYRIC_RE.match('  0 = E "section intro"')


class TestIntegrationFeatureDetection:
    """Belt-and-braces: if the detection flags lose fidelity to the real
    module shape, the forward-looking tests above silently skip forever.
    This test fires loudly if the detection code itself drifts."""

    def test_detection_reflects_module_state(self):
        import inspect
        actual_lyrics_field = "lyrics_events" in ChartData.__dataclass_fields__
        actual_lyrics_kwarg = "lyrics" in inspect.signature(build_chart).parameters
        assert HAS_LYRICS_EVENTS == actual_lyrics_field
        assert HAS_LYRICS_KWARG == actual_lyrics_kwarg
