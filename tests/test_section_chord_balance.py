"""
v1.10 section-aware chord ordering lock.

Charter's `_sec_target` returns higher chord density for chorus (0.35) than
verse (0.18) — hand-charts spend their chord energy in choruses. This
suite partitions `cd.expert_single` by section (chorus vs verse) using
`cd.events` and locks the ordering baker's spec requires:

    (1) chorus_chord_rate > verse_chord_rate   — ordering invariant
    (2) chorus_chord_rate <= 0.40              — upper-bound sanity
    (3) |chorus - verse| >= 0.08               — meaningful differentiation,
                                                 not statistical coincidence

Fixture is the 30s Fell In Luv slice (trap_pattern_30s.wav) run against a
synthetic LyricsData that matches that slice's structural pattern (hook
line repeated at bars 0, 3, 6 / verse lines between). No LRCLIB network
call needed — the lyrics are literal rather than fetched.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Dict, List

import pytest

from core.charter import ChartNote


# ---------- section partitioner --------------------------------------------

def _section_at(tick: int, ev_sorted) -> str:
    """Replicates charter._sec_at_tick: walk events in tick order, pick the
    section whose event tick is <= query tick."""
    s = "intro"
    for ev in ev_sorted:
        if tick >= ev.tick:
            s = ev.label.lower()
        else:
            break
    for key in ("chorus", "verse", "bridge", "intro", "outro"):
        if key in s:
            return key
    return "default"


def _partition_notes_by_section(expert_single: List[ChartNote], events) -> Dict[str, List[ChartNote]]:
    ev_sorted = sorted(events, key=lambda e: e.tick)
    out: Dict[str, List[ChartNote]] = {}
    for n in expert_single:
        if n.fret == 5:
            continue   # HOPO flags excluded — not real notes
        sec = _section_at(n.tick, ev_sorted)
        out.setdefault(sec, []).append(n)
    return out


def _chord_rate(notes: List[ChartNote]) -> float:
    groups: Dict[int, int] = {}
    for n in notes:
        groups[n.tick] = groups.get(n.tick, 0) + 1
    if not groups:
        return 0.0
    multi = sum(1 for c in groups.values() if c >= 2)
    return multi / len(groups)


# ---------- fixtures: trap WAV + synthetic lyrics ------------------------

@pytest.fixture(scope="module")
def librosa_available():
    try:
        import librosa  # noqa: F401
    except ImportError:
        pytest.skip("librosa not installed in this environment")


@pytest.fixture(scope="module")
def trap_wav() -> Path:
    from tests.fixtures.gen_fixture import TRAP_PATH, generate_trap_pattern
    if not TRAP_PATH.exists():
        generate_trap_pattern(TRAP_PATH)
    return TRAP_PATH


@pytest.fixture(scope="module")
def structured_lyrics():
    """Synthetic LyricsData mirroring Fell In Luv's hook/verse pattern:
    two chorus occurrences interspersed with verse lines. Each pair of
    chorus/verse blocks separated by > 2.5s gaps so analyze_structure
    splits into distinct phrases."""
    from core.lyrics import LyricsData
    lines = [
        "fell in luv",      "fell in luv again",      # chorus block 1
        "walking lonely",   "thinking slow",           # verse block 1
        "fell in luv",      "fell in luv again",      # chorus block 2
        "different thought","another verse line",      # verse block 2
    ]
    # 1s between consecutive lines inside a block; 4s gap between blocks.
    times = [1.0, 2.5, 7.0, 8.5, 13.0, 14.5, 19.0, 20.5]
    return LyricsData(lines=lines, times_s=times)


@pytest.fixture(scope="module")
def chart(trap_wav, structured_lyrics, librosa_available):
    from core.analyzer import analyze_audio
    from core.charter import build_chart
    return build_chart(analyze_audio(trap_wav), drums=None, lyrics=structured_lyrics)


@pytest.fixture(scope="module")
def section_notes(chart):
    return _partition_notes_by_section(chart.expert_single, chart.events)


@pytest.fixture(scope="module")
def section_rates(section_notes):
    return {sec: _chord_rate(notes) for sec, notes in section_notes.items()}


# ---------- (1) ordering invariant: chorus > verse ------------------------

@pytest.mark.slow
class TestSectionChordOrdering:
    def _require_both(self, section_notes, section_rates):
        """A section with fewer than 10 note groups is statistically too
        thin to reason about. If either chorus or verse is below that
        threshold the test skips rather than firing a false result."""
        for sec in ("chorus", "verse"):
            notes = section_notes.get(sec, [])
            groups = len({n.tick for n in notes})
            if groups < 10:
                pytest.skip(
                    f"section '{sec}' has only {groups} note groups — "
                    f"too thin to measure chord rate reliably"
                )
        return section_rates["chorus"], section_rates["verse"]

    def test_chorus_chord_rate_strictly_above_verse(
        self, section_notes, section_rates
    ):
        """Hand-chart convention: chorus carries more chord density than
        verse. _sec_target in charter.py returns 0.35 for chorus and 0.18
        for verse — the ordering must survive v1.10's chord-injection
        logic end-to-end."""
        chorus_rate, verse_rate = self._require_both(section_notes, section_rates)
        assert chorus_rate > verse_rate, (
            f"chorus chord_rate {chorus_rate:.4f} not > verse {verse_rate:.4f} — "
            f"v1.10 section-aware ordering broke"
        )

    def test_chorus_chord_rate_within_upper_bound(
        self, section_notes, section_rates
    ):
        """Chorus target is 0.35; baker's ceiling at 0.40 leaves ~5pp
        margin so the v1.7 chord cap can still bite without this lock
        false-firing on noise."""
        chorus_rate, _ = self._require_both(section_notes, section_rates)
        assert chorus_rate <= 0.40, (
            f"chorus chord_rate {chorus_rate:.4f} > 0.40 — chord injection "
            f"blew past the upper bound inside the chorus"
        )

    def test_chorus_minus_verse_at_least_0_08(
        self, section_notes, section_rates
    ):
        """A gap below 0.08 means the section-aware logic is making a
        meaningless distinction. 0.08 ≈ (0.35 - 0.18)/2 — half the design
        delta, which covers variance without accepting coincidence."""
        chorus_rate, verse_rate = self._require_both(section_notes, section_rates)
        diff = chorus_rate - verse_rate
        assert diff >= 0.08, (
            f"chorus - verse diff {diff:.4f} < 0.08 — section-aware "
            f"differentiation collapsed; rates: chorus={chorus_rate:.4f}, "
            f"verse={verse_rate:.4f}"
        )

    def test_both_sections_present_in_chart(self, chart, section_notes):
        """Belt-and-braces: if partitioning never found a chorus OR a
        verse, every assertion above skipped silently via _require_both.
        This test fails loudly instead."""
        labels = {e.label.lower() for e in chart.events}
        assert any("chorus" in l for l in labels), \
            f"no chorus events in cd.events: {labels}"
        assert any("verse" in l for l in labels), \
            f"no verse events in cd.events: {labels}"


# ---------- partitioner unit tests ----------------------------------------

class TestSectionPartitioner:
    def test_section_at_before_any_event_returns_intro(self):
        from core.charter import SectionEvent
        events = [SectionEvent(tick=500, label="chorus")]
        assert _section_at(100, events) == "intro"

    def test_section_at_after_boundary_switches(self):
        from core.charter import SectionEvent
        events = [
            SectionEvent(tick=0, label="intro"),
            SectionEvent(tick=1000, label="chorus"),
            SectionEvent(tick=2000, label="verse"),
        ]
        assert _section_at(500, events) == "intro"
        assert _section_at(1500, events) == "chorus"
        assert _section_at(2500, events) == "verse"

    def test_section_at_handles_mixed_case_labels(self):
        from core.charter import SectionEvent
        events = [SectionEvent(tick=0, label="Chorus 1")]
        assert _section_at(100, events) == "chorus"

    def test_partition_excludes_hopo_fret_5(self):
        from core.charter import SectionEvent
        events = [SectionEvent(tick=0, label="chorus")]
        notes = [
            ChartNote(tick=100, fret=0, length=0),
            ChartNote(tick=100, fret=5, length=0),   # HOPO flag — must be dropped
        ]
        parts = _partition_notes_by_section(notes, events)
        chorus = parts["chorus"]
        assert len(chorus) == 1
        assert chorus[0].fret == 0

    def test_chord_rate_helper_on_mixed_track(self):
        notes = [
            ChartNote(tick=0, fret=0), ChartNote(tick=0, fret=2),      # chord
            ChartNote(tick=100, fret=1),                               # single
            ChartNote(tick=200, fret=3), ChartNote(tick=200, fret=4),  # chord
        ]
        assert _chord_rate(notes) == 2 / 3

    def test_chord_rate_helper_empty(self):
        assert _chord_rate([]) == 0.0
