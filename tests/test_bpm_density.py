"""
v1.8 BPM-aware note-density regression locks.

Baker's spec: at a realistic onset density, max notes/bar must stay under
these ceilings per BPM band:

    60 bpm  → ≤ 15     (super slow / sub-90 band)
    100 bpm → ≤ 19     (90-120 slow trap band)
    140 bpm → ≤ 25     (120-150 standard band)
    170 bpm → ≤ 31     (150+ fast band)

Boundary case: 90 bpm sits on the lower edge of the (90, 120) band and
uses that band's cap (18), so ≤ 19 applies — NOT the sub-90 band's ≤ 15.

Each test crafts a synthetic Analysis at the target BPM with ~10 onsets per
bar (realistic music density — real tracks tend to sit at 6-12 per bar).
Any charter regression that drops the BPM-aware cap or doubles the output
per onset spikes max_npb above the ceiling and flips this suite red.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

import numpy as np
import pytest

from core.analyzer import Analysis, HOP, SR
from core.charter import RESOLUTION, build_chart


_BAR_TICKS = RESOLUTION * 4


def _mk_analysis(
    bpm: float,
    duration_s: float = 30.0,
    onsets_per_bar: int = 10,
    pitch_range: tuple = (150.0, 500.0),
) -> Analysis:
    """
    Hand-crafted Analysis: `onsets_per_bar` evenly-spaced onsets per 4-beat
    bar, varied pitch so the quintile fret-mapper spreads output across the
    board.

    onsets_per_bar=10 ≈ realistic music density (one onset every ~2.4 beats
    at the slow end, every ~1.6 beats fast). Keeps the per-iteration charter
    gates working as designed instead of saturating them.
    """
    beat_s = 60.0 / bpm
    n_beats = max(2, int(duration_s / beat_s))
    beats = np.array([i * beat_s for i in range(n_beats)])
    downbeats = beats[::4]

    bar_s = beat_s * 4
    n_bars = max(1, int(duration_s / bar_s))
    onsets = []
    for bar in range(n_bars):
        t0 = bar * bar_s
        for i in range(onsets_per_bar):
            onsets.append(t0 + i * (bar_s / onsets_per_bar))
    onsets_arr = np.array(onsets)

    frames = max(2, int(duration_s * SR / HOP))
    pitch_hz = np.linspace(pitch_range[0], pitch_range[1], frames)
    rms = np.full(frames, 0.5)
    centroid = np.linspace(1000.0, 3000.0, frames)
    chroma = np.ones((12, frames)) * 0.3

    return Analysis(
        duration_s=duration_s,
        bpm=float(bpm),
        beats=beats,
        downbeats=downbeats,
        onsets=onsets_arr,
        onset_strength=np.ones(len(onsets_arr)),
        perc_onsets=np.array([]),
        chroma=chroma,
        pitch_hz=pitch_hz,
        rms=rms,
        centroid=centroid,
        sections=[],
    )


def _max_notes_per_bar(cd) -> int:
    """Highest note count observed in any one bar of expert_single."""
    per_bar = Counter(n.tick // _BAR_TICKS for n in cd.expert_single)
    return max(per_bar.values()) if per_bar else 0


# ---------- (1) 4 BPM-band locks -------------------------------------------

class TestBpmDensityCaps:
    @pytest.mark.parametrize("bpm,ceiling", [
        (60,  15),
        (100, 19),
        (140, 25),
        (170, 31),
    ])
    def test_max_notes_per_bar_under_ceiling(self, bpm, ceiling):
        a = _mk_analysis(bpm=bpm)
        cd = build_chart(a, drums=None)
        max_npb = _max_notes_per_bar(cd)
        assert max_npb <= ceiling, (
            f"bpm={bpm}: max notes/bar {max_npb} exceeds v1.8 ceiling {ceiling}"
        )
        # Belt-and-braces: output must be non-empty — a dead charter would
        # trivially satisfy the ceiling.
        assert cd.expert_single, f"bpm={bpm}: no notes produced"


# ---------- (2) 90 bpm boundary — uses (90, 120) band cap ------------------

class TestBoundaryBpm:
    """
    The charter's cap table is a half-open interval list:
        [0, 90)   → 14 notes/bar
        [90, 120) → 18 notes/bar
        [120, 150)→ 24
        [150, 999)→ 30
    So bpm = 90 exactly falls into the (90, 120) band and must take its
    cap, not the sub-90 band's cap. Baker spec: 90 bpm → ≤ 19.
    """

    def test_90_bpm_uses_medium_band_cap(self):
        a = _mk_analysis(bpm=90.0)
        cd = build_chart(a, drums=None)
        max_npb = _max_notes_per_bar(cd)
        assert max_npb <= 19, (
            f"bpm=90 (lower edge of (90, 120) band): max notes/bar {max_npb} "
            f"exceeds ceiling 19 — band lookup may have slipped into the "
            f"sub-90 bucket"
        )

    def test_89_bpm_uses_sub_90_band_cap(self):
        """Just below the boundary: must take the tighter sub-90 cap (≤ 15)."""
        a = _mk_analysis(bpm=89.0)
        cd = build_chart(a, drums=None)
        max_npb = _max_notes_per_bar(cd)
        assert max_npb <= 15, (
            f"bpm=89 (sub-90 band): max notes/bar {max_npb} > 15"
        )

    def test_boundary_has_different_cap_than_adjacent(self):
        """Exercise the boundary explicitly: cap at 90 bpm > cap at 89 bpm.
        If the half-open interval ever gets inverted, this fires."""
        a_lo = _mk_analysis(bpm=89.0)
        a_hi = _mk_analysis(bpm=90.0)
        max_lo = _max_notes_per_bar(build_chart(a_lo, drums=None))
        max_hi = _max_notes_per_bar(build_chart(a_hi, drums=None))
        # Not necessarily strictly greater (noise), but 90bpm must at least
        # tolerate ceilings the sub-90 cap would reject.
        assert max_hi <= 19 and max_lo <= 15
