"""
CHARTER-v12 contract: on the 30s sine fixture, the pipeline must produce a
ChartData whose expert_single track obeys the v12 invariants:

    (a) sync markers > 1          — tempo map is populated (TS + at least one B)
    (b) 0 notes with fret == 5    — HOPO-force flags were removed from v12
    (c) chord_rate >= 0.15        — at least 15% of tick groups are chords

These are regression guards. If a future charter rewrite drops the tempo
map, re-enables HOPO forcing, or collapses chord injection, this suite
turns the PR red before merge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest

from core.charter import ChartData, ChartNote
from tests.fixtures.gen_fixture import PULSE_PATH, generate_pulse_train


@pytest.fixture(scope="session")
def pulse_wav() -> Path:
    """Tempo-varied pulse train — librosa finds real onsets on this, so the
    v12 invariants (and especially chord_rate) actually bite in CI instead of
    skipping the way they would on a pure sine."""
    if not PULSE_PATH.exists():
        generate_pulse_train(PULSE_PATH)
    return PULSE_PATH


@pytest.fixture
def librosa_available():
    try:
        import librosa  # noqa: F401
    except ImportError:
        pytest.skip("librosa not installed in this environment")


def _chord_rate(notes: List[ChartNote]) -> float:
    """Fraction of unique ticks that carry 2+ simultaneous notes.
    Follows core.reference.compute_metrics: chord group = unique tick, chord
    rate = (groups with >= 2 frets) / (total groups)."""
    groups: Dict[int, int] = {}
    for n in notes:
        if n.fret == 5:
            continue  # HOPO flags are not real notes
        groups[n.tick] = groups.get(n.tick, 0) + 1
    if not groups:
        return 0.0
    multi = sum(1 for c in groups.values() if c >= 2)
    return multi / len(groups)


def _fret_counts(notes: List[ChartNote]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for n in notes:
        out[n.fret] = out.get(n.fret, 0) + 1
    return out


@pytest.mark.slow
class TestCharterV12Invariants:
    @pytest.fixture
    def chart(self, pulse_wav, librosa_available) -> ChartData:
        from core.analyzer import analyze_audio
        from core.charter import build_chart
        analysis = analyze_audio(pulse_wav)
        return build_chart(analysis, drums=None)

    def test_sync_markers_count_greater_than_one(self, chart: ChartData):
        # (a) At minimum: one TS and one B at tick 0. v12 is free to emit
        # more (multi-tempo map); the invariant is that there are strictly
        # more than one entry so a scanner never crashes on an empty track.
        assert len(chart.sync) > 1, f"sync too thin: {chart.sync}"

    def test_sync_contains_time_signature_and_bpm(self, chart: ChartData):
        kinds = {k for _, k, _ in chart.sync}
        assert "TS" in kinds, "SyncTrack missing time signature"
        assert "B" in kinds, "SyncTrack missing BPM marker"

    def test_sync_is_sorted_by_tick(self, chart: ChartData):
        ticks = [int(t) for t, _, _ in chart.sync]
        assert ticks == sorted(ticks), f"sync not sorted: {ticks}"

    def test_zero_fret_5_notes(self, chart: ChartData):
        # (b) HOPO forcing was removed in v12 — no N 5 events survive.
        fret5 = [n for n in chart.expert_single if n.fret == 5]
        assert fret5 == [], (
            f"HOPO force notes should be absent in v12, found {len(fret5)}: "
            f"{[(n.tick, n.length) for n in fret5[:5]]}"
        )

    def test_chord_rate_above_threshold(self, chart: ChartData):
        # (c) Chord injection should land on >= 15% of groups. Pulse-train
        # fixture gives librosa real onsets (~90 notes on the 30s file), so
        # this invariant actually fires in CI instead of skipping.
        assert chart.expert_single, "pulse fixture should produce notes"
        rate = _chord_rate(chart.expert_single)
        counts = _fret_counts(chart.expert_single)
        assert rate >= 0.15, (
            f"chord_rate {rate:.3f} below 0.15 — fret histogram: {counts}, "
            f"total notes: {len(chart.expert_single)}"
        )

    def test_frets_in_allowed_set(self, chart: ChartData):
        # v12 emits only colored notes (0-4) and opens (7) — 5 and 6 forbidden.
        allowed = {0, 1, 2, 3, 4, 7}
        bad = [n.fret for n in chart.expert_single if n.fret not in allowed]
        assert not bad, f"disallowed frets emitted: {sorted(set(bad))}"

    def test_notes_sorted_by_tick(self, chart: ChartData):
        ticks = [n.tick for n in chart.expert_single]
        assert ticks == sorted(ticks), "expert_single not tick-sorted"

    def test_bpm_milli_is_positive(self, chart: ChartData):
        assert chart.bpm_milli > 0


class TestChordRateHelper:
    """Guard the helper itself so a broken metric doesn't mask real regressions."""

    def test_single_notes_only_gives_zero(self):
        notes = [ChartNote(tick=i * 100, fret=0) for i in range(10)]
        assert _chord_rate(notes) == 0.0

    def test_all_chords_gives_one(self):
        notes = []
        for i in range(10):
            notes.append(ChartNote(tick=i * 100, fret=0))
            notes.append(ChartNote(tick=i * 100, fret=2))
        assert _chord_rate(notes) == 1.0

    def test_hopo_flags_ignored_for_chord_count(self):
        # fret=5 at a tick must NOT make that tick count as a chord group.
        notes = [
            ChartNote(tick=0, fret=0),
            ChartNote(tick=0, fret=5),      # HOPO flag — not a real note
            ChartNote(tick=100, fret=1),
        ]
        assert _chord_rate(notes) == 0.0

    def test_mixed_half_chords(self):
        notes = [
            ChartNote(tick=0, fret=0), ChartNote(tick=0, fret=2),  # chord
            ChartNote(tick=100, fret=1),                           # single
            ChartNote(tick=200, fret=3), ChartNote(tick=200, fret=4),  # chord
            ChartNote(tick=300, fret=0),                           # single
        ]
        assert _chord_rate(notes) == 0.5

    def test_empty_notes_gives_zero(self):
        assert _chord_rate([]) == 0.0


class TestGenDowncharts:
    """v1.6: auto-difficulty downcharting contracts."""

    def test_empty_input_returns_empty_tiers(self):
        from core.charter import gen_downcharts
        res = gen_downcharts([])
        assert set(res.keys()) == {"ExpertSingle", "HardSingle", "MediumSingle", "EasySingle"}
        for tier_notes in res.values():
            assert tier_notes == []

    def test_tier_note_counts_descend(self):
        from core.charter import gen_downcharts, RESOLUTION
        # Generate 4 bars of notes with chords, opens, and fast subdivisions
        expert = []
        bar_ticks = RESOLUTION * 4
        for bar in range(4):
            base = bar * bar_ticks
            for i in range(8):  # 8th notes
                t = base + i * (RESOLUTION // 2)
                expert.append(ChartNote(tick=t, fret=i % 5))
                # Add chord secondary on beats 0 and 2
                if i in (0, 4):
                    expert.append(ChartNote(tick=t, fret=(i + 2) % 5))
                # Add open note
                if i == 6:
                    expert.append(ChartNote(tick=t, fret=7))

        res = gen_downcharts(expert)
        exp = res["ExpertSingle"]
        hard = res["HardSingle"]
        med = res["MediumSingle"]
        easy = res["EasySingle"]

        assert len(exp) > len(hard) > len(med) > len(easy)

        # Hard strips HOPO and chord secondaries
        assert all(n.fret != 5 for n in hard)
        hard_ticks = [n.tick for n in hard]
        assert len(hard_ticks) == len(set(hard_ticks)), "hard must strip chord secondaries"

        # Medium strips opens (fret 7) and chords
        assert all(n.fret != 7 for n in med)
        med_ticks = [n.tick for n in med]
        assert len(med_ticks) == len(set(med_ticks))

        # Easy only strong beats (beat 1 and 3 -> beats 0 and 2 in 4/4)
        assert all(n.fret != 7 for n in easy)
        for n in easy:
            tib = n.tick % bar_ticks
            beat = tib // RESOLUTION
            assert beat in (0, 2)

