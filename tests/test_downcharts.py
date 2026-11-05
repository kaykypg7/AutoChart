"""
gen_downcharts (charter v1.6) — auto-difficulty from ExpertSingle.

Locks per baker's spec:
    (1) expert > hard > medium > easy      — strict count ordering
    (2) hard   in [25%, 75%] of expert
    (3) medium in [20%, 50%] of expert
    (4) easy   in [10%, 30%] of expert
    (5) downcharts preserve timing          — every tick ⊆ expert ticks
    (6) no downchart emits HOPO forcing     — fret 5 absent on hard/medium/easy
"""
from __future__ import annotations

from typing import Dict, List, Set

import pytest

from core.charter import ChartNote, gen_downcharts


# ---------- fixture: a dense Expert track ----------------------------------

def _dense_expert(bars: int = 20, notes_per_bar: int = 12) -> List[ChartNote]:
    """
    Synthetic ExpertSingle with varied frets, open notes, chord-secondaries,
    and HOPO flags. 20 bars x 12 notes/bar = 240 raw N events plus some
    chord/HOPO extras, bar-aligned so down-samplers can split cleanly.
    """
    RESOLUTION = 192
    bar_ticks = RESOLUTION * 4
    out: List[ChartNote] = []
    frets_cycle = [0, 1, 2, 3, 4, 7]
    for bar in range(bars):
        for i in range(notes_per_bar):
            tick = bar * bar_ticks + i * (bar_ticks // notes_per_bar)
            fret = frets_cycle[(bar + i) % len(frets_cycle)]
            out.append(ChartNote(tick=tick, fret=fret, length=0))
            # Every 3rd beat → add a chord secondary at the same tick.
            if i % 3 == 0 and fret != 7:
                out.append(ChartNote(tick=tick, fret=(fret + 2) % 5, length=0))
            # Every 5th beat → HOPO force flag at same tick (fret 5).
            if i % 5 == 0:
                out.append(ChartNote(tick=tick, fret=5, length=0))
    return out


@pytest.fixture(scope="module")
def expert() -> List[ChartNote]:
    return _dense_expert()


@pytest.fixture(scope="module")
def charts(expert) -> Dict[str, List[ChartNote]]:
    return gen_downcharts(expert)


# ---------- (1) count ordering --------------------------------------------

class TestCountOrdering:
    def test_strict_descending_counts(self, charts):
        e = len(charts["ExpertSingle"])
        h = len(charts["HardSingle"])
        m = len(charts["MediumSingle"])
        easy = len(charts["EasySingle"])
        assert e > h > m > easy, (
            f"not strictly descending: expert={e}, hard={h}, medium={m}, easy={easy}"
        )


# ---------- (2)(3)(4) count percentages ------------------------------------

class TestCountPercentages:
    def test_hard_in_25_to_75_of_expert(self, charts):
        e = len(charts["ExpertSingle"])
        h = len(charts["HardSingle"])
        ratio = h / e
        assert 0.25 <= ratio <= 0.75, f"hard/expert = {ratio:.3f} outside [0.25, 0.75]"

    def test_medium_in_20_to_50_of_expert(self, charts):
        e = len(charts["ExpertSingle"])
        m = len(charts["MediumSingle"])
        ratio = m / e
        assert 0.20 <= ratio <= 0.50, f"medium/expert = {ratio:.3f} outside [0.20, 0.50]"

    def test_easy_in_10_to_30_of_expert(self, charts):
        e = len(charts["ExpertSingle"])
        easy = len(charts["EasySingle"])
        ratio = easy / e
        assert 0.10 <= ratio <= 0.30, f"easy/expert = {ratio:.3f} outside [0.10, 0.30]"


# ---------- (5) tick timing preserved -------------------------------------

class TestTickTimingPreserved:
    def _expert_ticks(self, charts) -> Set[int]:
        return {n.tick for n in charts["ExpertSingle"]}

    def test_hard_ticks_subset_of_expert(self, charts):
        expert_ticks = self._expert_ticks(charts)
        hard_ticks = {n.tick for n in charts["HardSingle"]}
        extras = hard_ticks - expert_ticks
        assert not extras, f"HardSingle has ticks not in Expert: {sorted(extras)[:10]}"

    def test_medium_ticks_subset_of_expert(self, charts):
        expert_ticks = self._expert_ticks(charts)
        med_ticks = {n.tick for n in charts["MediumSingle"]}
        extras = med_ticks - expert_ticks
        assert not extras, f"MediumSingle has ticks not in Expert: {sorted(extras)[:10]}"

    def test_easy_ticks_subset_of_expert(self, charts):
        expert_ticks = self._expert_ticks(charts)
        easy_ticks = {n.tick for n in charts["EasySingle"]}
        extras = easy_ticks - expert_ticks
        assert not extras, f"EasySingle has ticks not in Expert: {sorted(extras)[:10]}"

    def test_all_downcharts_sorted_by_tick(self, charts):
        for name in ("HardSingle", "MediumSingle", "EasySingle"):
            ticks = [n.tick for n in charts[name]]
            assert ticks == sorted(ticks), f"{name} not tick-sorted"


# ---------- (6) HOPO forcing absent from downcharts -----------------------

class TestNoHopoInDowncharts:
    @pytest.mark.parametrize("difficulty", ["HardSingle", "MediumSingle", "EasySingle"])
    def test_no_fret_5_in_downchart(self, charts, difficulty):
        hopo = [n for n in charts[difficulty] if n.fret == 5]
        assert hopo == [], (
            f"{difficulty} contains {len(hopo)} HOPO-force (fret=5) events — "
            f"first few: {[(n.tick) for n in hopo[:5]]}"
        )

    @pytest.mark.parametrize("difficulty", ["MediumSingle", "EasySingle"])
    def test_no_fret_7_opens_in_medium_easy(self, charts, difficulty):
        """Medium and Easy strip open notes (fret 7) per the spec."""
        opens = [n for n in charts[difficulty] if n.fret == 7]
        assert opens == [], (
            f"{difficulty} contains {len(opens)} open notes — must be stripped"
        )

    def test_medium_frets_clamped_to_max_3(self, charts):
        """Medium fret range is Green (0), Red (1), Yellow (2), Blue (3) only."""
        frets = [n.fret for n in charts["MediumSingle"]]
        assert all(0 <= f <= 3 for f in frets), f"Medium frets exceed 3: {[f for f in frets if f > 3]}"

    def test_easy_frets_clamped_to_max_2(self, charts):
        """Easy fret range is Green (0), Red (1), Yellow (2) only."""
        frets = [n.fret for n in charts["EasySingle"]]
        assert all(0 <= f <= 2 for f in frets), f"Easy frets exceed 2: {[f for f in frets if f > 2]}"

    def test_hard_frets_in_range_0_to_4_or_7(self, charts):
        """Hard fret range is 0..4 (Green to Orange) plus 7 (open note)."""
        frets = [n.fret for n in charts["HardSingle"]]
        assert all(f in (0, 1, 2, 3, 4, 7) for f in frets), (
            f"Hard frets invalid: {[f for f in frets if f not in (0, 1, 2, 3, 4, 7)]}"
        )


# ---------- edge cases -----------------------------------------------------

class TestEdgeCases:
    def test_empty_expert_returns_empty_downcharts(self):
        out = gen_downcharts([])
        assert out == {
            "ExpertSingle": [],
            "HardSingle": [],
            "MediumSingle": [],
            "EasySingle": [],
        }

    def test_single_note_expert_easy_may_be_empty(self):
        """One note that happens to land off beats 0/2 of a bar means Easy
        is legitimately empty — just assert all tracks are well-typed."""
        out = gen_downcharts([ChartNote(tick=288, fret=1, length=0)])
        for name in ("ExpertSingle", "HardSingle", "MediumSingle", "EasySingle"):
            assert isinstance(out[name], list)

    def test_easy_only_from_beat_0_and_2(self):
        """EasySingle rule: only beat 1 and 3 of each bar (zero-indexed beats
        0 and 2 in 4/4). Verify no notes land on beats 1 or 3."""
        RESOLUTION = 192
        bar_ticks = RESOLUTION * 4
        notes = []
        # One note per beat for 4 bars (16 notes total).
        for bar in range(4):
            for beat in range(4):
                tick = bar * bar_ticks + beat * RESOLUTION
                notes.append(ChartNote(tick=tick, fret=beat % 5, length=0))
        out = gen_downcharts(notes)
        easy = out["EasySingle"]
        for n in easy:
            beat = (n.tick % bar_ticks) // RESOLUTION
            assert beat in (0, 2), f"Easy note on beat {beat} (tick {n.tick}) — must be 0 or 2"
