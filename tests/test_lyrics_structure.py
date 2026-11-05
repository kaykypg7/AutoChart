"""
analyze_structure() — Deadbolt's verse/chorus detector.

Covers:
    (1) hook repeated 3x    → chorus count >= 2
    (2) through-composed    → chorus count == 0
    (3) gap > 2.5s          → new phrase boundary
    (4) integration (mocked analysis) → build_chart with lyrics emits a
        "chorus" marker in cd.events at a tick near a hook timestamp
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pytest

from core.lyrics import LyricsData, analyze_structure


def _labels(result: List[Tuple[float, str]]) -> List[str]:
    return [lab for _, lab in result]


def _times(result: List[Tuple[float, str]]) -> List[float]:
    return [t for t, _ in result]


# ---------- (1) hook-repeated chorus detection -----------------------------

class TestHookRepetition:
    def test_hook_repeated_three_times_marks_at_least_two_choruses(self):
        """Hook line "run it up" repeats 3x across the song. Phrase gaps
        (>= 2.5s) split hook occurrences into distinct phrases, so Signal B
        marks multiple phrases as chorus. Baker spec: >= 2 chorus
        detections."""
        lines = [
            "intro line one",
            "intro line two",
            # chorus 1
            "run it up",
            "run it up fast",
            # verse 1
            "walking down the road",
            "watching all the smoke",
            # chorus 2
            "run it up",
            "run it up fast",
            # verse 2
            "chasing all my dreams",
            "nothing like it seems",
            # chorus 3
            "run it up",
            "run it up fast",
        ]
        # Each block (intro / chorus / verse) spaced ~4s apart with 1s
        # between lines inside a block — big gaps split blocks into phrases;
        # hook-line signal fires on every phrase containing "run it up".
        times = []
        t = 0.0
        for i, _ in enumerate(lines):
            if i % 2 == 0 and i > 0:
                t += 4.0  # new phrase break
            else:
                t += 1.0
            times.append(t)
        result = analyze_structure(LyricsData(lines=lines, times_s=times))
        chorus_count = _labels(result).count("chorus")
        assert chorus_count >= 2, (
            f"expected chorus count >= 2 on 3x-repeated hook, got {chorus_count}; "
            f"labels: {_labels(result)}"
        )

    def test_phrase_level_repetition_fires_signal_a(self):
        """Two full phrase-groups with identical normalized text (gapped
        > 2.5s apart) — Signal A (phrase-level) marks both as chorus."""
        lines = ["ride the wave", "feel the sun", "ride the wave", "feel the sun"]
        times = [0.0, 1.0, 10.0, 11.0]   # 10s gap splits into two phrases
        result = analyze_structure(LyricsData(lines=lines, times_s=times))
        assert _labels(result).count("chorus") == 2

    def test_filler_line_not_hook(self):
        """A short filler ("yeah") that recurs 2x is NOT a hook; threshold
        is 3x. All phrases should stay as verse."""
        lines = ["yeah", "walking slow", "yeah", "walking fast"]
        times = [0.0, 1.0, 5.0, 6.0]
        result = analyze_structure(LyricsData(lines=lines, times_s=times))
        assert "chorus" not in _labels(result)


# ---------- (2) through-composed → no choruses -----------------------------

class TestThroughComposed:
    def test_all_unique_lines_zero_choruses(self):
        """Every line is unique → no repetition on either signal → every
        phrase is a verse."""
        lines = [
            "first unique thought",
            "second very different idea",
            "third unrelated image",
            "fourth distinct phrase",
            "fifth brand new line",
            "sixth never seen before",
        ]
        times = [i * 4.0 for i in range(len(lines))]  # 4s gaps → separate phrases
        result = analyze_structure(LyricsData(lines=lines, times_s=times))
        assert _labels(result).count("chorus") == 0
        assert all(lab == "verse" for lab in _labels(result))

    def test_empty_lyrics_returns_empty_list(self):
        assert analyze_structure(LyricsData(lines=[], times_s=[])) == []

    def test_none_lyrics_returns_empty_list(self):
        assert analyze_structure(None) == []

    def test_single_line_lyrics_returns_single_verse(self):
        result = analyze_structure(LyricsData(lines=["lonely line"], times_s=[1.0]))
        assert result == [(1.0, "verse")]


# ---------- (3) gap > 2.5s creates new phrase boundary --------------------

class TestPhraseBoundaries:
    def test_gap_at_threshold_creates_new_phrase(self):
        """Baker spec: gaps >= 2.5s open a new phrase."""
        lines = ["a", "b", "c", "d"]
        times = [0.0, 1.0, 3.5, 4.5]   # gap at 2.5s exactly between b and c
        result = analyze_structure(LyricsData(lines=lines, times_s=times))
        # Two phrases: [a, b] starting at 0.0 and [c, d] starting at 3.5.
        assert len(result) == 2
        assert _times(result) == [0.0, 3.5]

    def test_gap_below_threshold_keeps_same_phrase(self):
        lines = ["a", "b", "c"]
        times = [0.0, 1.0, 3.0]   # 2.0s gap — below 2.5s threshold
        result = analyze_structure(LyricsData(lines=lines, times_s=times))
        assert len(result) == 1
        assert _times(result) == [0.0]

    def test_multiple_gaps_create_multiple_phrases(self):
        lines = ["a", "b", "c", "d", "e", "f"]
        times = [0.0, 1.0, 5.0, 6.0, 10.0, 11.0]
        result = analyze_structure(LyricsData(lines=lines, times_s=times))
        assert len(result) == 3
        assert _times(result) == [0.0, 5.0, 10.0]

    def test_phrase_starts_at_first_line_time(self):
        """A phrase's reported timestamp is the START of its first line, not
        the midpoint or any later line."""
        lines = ["head of phrase", "tail of phrase"]
        times = [7.5, 9.0]
        result = analyze_structure(LyricsData(lines=lines, times_s=times))
        assert result == [(7.5, "verse")]


# ---------- (4) integration: build_chart surfaces chorus in cd.events -----

class TestIntegrationWithBuildChart:
    """Mocked Analysis — the only thing that matters is that _events()
    sees analyze_structure's chorus labels and emits SectionEvents with
    label == 'chorus'. No librosa needed."""

    def _mock_analysis(self):
        """Minimal Analysis stub matching what charter._events reads."""
        from core.analyzer import Analysis
        return Analysis(
            duration_s=30.0,
            bpm=120.0,
            beats=np.linspace(0.0, 30.0, 60),
            downbeats=np.linspace(0.0, 30.0, 15),
            onsets=np.array([]),
            onset_strength=np.array([]),
            perc_onsets=np.array([]),
            chroma=np.zeros((12, 10)),
            pitch_hz=np.zeros(10),
            rms=np.zeros(10),
            centroid=np.zeros(10),
            sections=[(0.0, "intro"), (20.0, "outro")],
        )

    def test_chorus_marker_merged_into_events(self):
        from core.charter import build_chart
        lines = [
            "intro verse one",
            "intro verse two",
            "hook phrase here",
            "hook phrase here again",
            "another verse mid",
            "another thought mid",
            "hook phrase here",
            "hook phrase here again",
        ]
        # Phrase gaps > 2.5s split into 4 phrases of 2 lines each.
        times = [0.0, 1.0, 5.0, 6.0, 10.0, 11.0, 15.0, 16.0]
        lyrics = LyricsData(lines=lines, times_s=times)

        cd = build_chart(self._mock_analysis(), drums=None, lyrics=lyrics)

        labels = [e.label for e in cd.events]
        assert "chorus" in labels, f"chorus marker missing from cd.events: {labels}"

    def test_chorus_label_wins_over_mfcc_near_lyric_timestamp(self):
        """TOLERANCE_S window: an MFCC section within 2s of a lyric chorus
        timestamp is dropped in favor of the lyric label."""
        from core.charter import build_chart
        lines = ["chorus top", "chorus bot", "chorus top", "chorus bot"]
        times = [1.0, 2.0, 10.0, 11.0]       # two phrase groups 10s apart
        lyrics = LyricsData(lines=lines, times_s=times)

        # Craft an Analysis whose MFCC sections sit right on top of the
        # lyric chorus timestamps — the lyric label must win.
        from core.analyzer import Analysis
        a = Analysis(
            duration_s=30.0, bpm=120.0,
            beats=np.linspace(0.0, 30.0, 60),
            downbeats=np.linspace(0.0, 30.0, 15),
            onsets=np.array([]), onset_strength=np.array([]),
            perc_onsets=np.array([]),
            chroma=np.zeros((12, 10)), pitch_hz=np.zeros(10),
            rms=np.zeros(10), centroid=np.zeros(10),
            sections=[(1.5, "verse"), (10.5, "bridge")],   # within 2s of lyric times
        )
        cd = build_chart(a, drums=None, lyrics=lyrics)
        labels = [e.label for e in cd.events]
        assert "chorus" in labels
        # MFCC "bridge" should have been dropped (collision window).
        assert "bridge" not in labels

    def test_no_chorus_marker_when_lyrics_omitted(self):
        from core.charter import build_chart
        cd = build_chart(self._mock_analysis(), drums=None, lyrics=None)
        labels = [e.label for e in cd.events]
        assert "chorus" not in labels
        # MFCC labels passed through untouched.
        assert "intro" in labels
