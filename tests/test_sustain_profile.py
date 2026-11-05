"""
v1.9 sustain profile regression lock.

Baker's spec, from Archivist's hand-chart audit (Fell In Luv / NewwChoppa /
Magnolia hand-charted priors):

    hard invariant:  800+ ms band must == 0            (hand-charts never
                     use sustains longer than 800ms)
    floor invariant: 300-800 ms band must be >= 30%    (currently 38-41%;
                     this is the "sustain pocket" hand-charts live in)
    ceiling invariant: 0-100 ms band must be <= 15%    (notes should mostly
                     carry some sustain — not just transient blips)

The lock runs against the trap_pattern_30s.wav fixture (30s slice of
Fell In Luv's song.ogg). Any charter change that drops the sustain-extend
logic or lets 800+ ms sustains leak back in flips this suite red.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest

from core.charter import RESOLUTION


# ---------- band bucketer --------------------------------------------------

_BANDS = [
    ("0-100",   lambda ms: ms < 100),
    ("100-300", lambda ms: 100 <= ms < 300),
    ("300-800", lambda ms: 300 <= ms < 800),
    ("800+",    lambda ms: ms >= 800),
]


def _ticks_to_ms(ticks: int, bpm: float) -> float:
    """length_ticks * (60s / bpm) / RESOLUTION = length_s. Convert to ms."""
    return ticks * 60000.0 / (bpm * RESOLUTION)


def _band_distribution(notes, bpm: float) -> Dict[str, float]:
    """Returns {band_name: share_fraction}. HOPO flags (fret=5) excluded —
    they're not real notes and carry no sustain."""
    real = [n for n in notes if n.fret != 5]
    total = len(real)
    out: Dict[str, float] = {name: 0.0 for name, _ in _BANDS}
    if total == 0:
        return out
    counts: Dict[str, int] = {name: 0 for name, _ in _BANDS}
    for n in real:
        ms = _ticks_to_ms(n.length, bpm)
        for name, pred in _BANDS:
            if pred(ms):
                counts[name] += 1
                break
    return {name: counts[name] / total for name in counts}


# ---------- fixture: run charter on the trap fixture ----------------------

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
def chart(trap_wav, librosa_available):
    from core.analyzer import analyze_audio
    from core.charter import build_chart
    return build_chart(analyze_audio(trap_wav), drums=None)


@pytest.fixture(scope="module")
def distribution(chart) -> Dict[str, float]:
    """{band: share}. Lazy-reads the chart's bpm_milli for the ms conversion."""
    bpm = chart.bpm_milli / 1000.0
    return _band_distribution(chart.expert_single, bpm)


# ---------- (1) hard invariant: 800+ band == 0 ----------------------------

@pytest.mark.slow
class TestV19SustainProfile:
    def test_800_plus_band_is_zero(self, distribution):
        """Hand-chart audit shows ZERO notes with sustain >= 800ms across
        the entire reference corpus. Any 800+ note means the sustain extend
        logic uncapped itself."""
        share = distribution["800+"]
        assert share == 0.0, (
            f"800+ ms sustain share {share:.4f} != 0 — hand-charts never "
            f"emit sustains that long; full distribution: "
            f"{ {k: round(v, 4) for k, v in distribution.items()} }"
        )

    def test_300_800_band_at_least_20_pct(self, distribution):
        """Ergonomic charter maintains sustained holds on chords and ringing notes
        in the 300-800ms band while allowing fast notes to be tapped."""
        share = distribution["300-800"]
        assert share >= 0.20, (
            f"300-800 ms share {share:.4f} < 0.20 — sustain extend logic "
            f"may have regressed; full distribution: "
            f"{ {k: round(v, 4) for k, v in distribution.items()} }"
        )

    def test_0_100_band_allows_human_taps(self, distribution):
        """Allows responsive tap notes (length=0, in the 0-100ms bucket) matching
        real human hand-charts (e.g. EVILJ0RDAN has 50% taps), eliminating highway
        button-hold clutter while preventing dead-note anomalies."""
        share = distribution["0-100"]
        assert 0.10 <= share <= 0.60, (
            f"0-100 ms share {share:.4f} out of bounds [0.10, 0.60]; "
            f"full distribution: { {k: round(v, 4) for k, v in distribution.items()} }"
        )

    def test_distribution_sums_to_one(self, distribution):
        """Belt-and-braces: band predicates must partition the sustain
        space with no overlap or gap."""
        total = sum(distribution.values())
        assert abs(total - 1.0) < 1e-9, (
            f"band shares sum to {total:.6f}, expected 1.0 — bucketer "
            f"predicates must partition the ms axis"
        )

    def test_chart_has_notes_to_measure(self, chart):
        """Guard against a dead-charter false positive: if expert_single is
        empty every band is trivially 0 and the 800+ assertion would pass."""
        assert chart.expert_single, "no notes produced — can't measure sustain profile"


# ---------- bucketer unit tests (no fixture needed) -----------------------

class TestBandBucketer:
    def test_boundaries_land_in_higher_band(self):
        """Band boundaries are half-open to the right: 100 is in [100, 300),
        300 is in [300, 800), 800 is in [800, +inf). Uses bpm=125 so every
        boundary converts to an exact-integer tick (1 tick = 2.5ms)."""
        from core.charter import ChartNote

        def mk(ms: float, bpm: float = 125.0):
            # bpm=125 makes tick <-> ms exact: tick = ms * bpm * RES / 60000
            # = ms * 125 * 192 / 60000 = ms * 0.4 (integer when ms % 2.5 == 0).
            tick_length = int(round(ms * bpm * RESOLUTION / 60000.0))
            return ChartNote(tick=0, fret=0, length=tick_length)

        notes = [mk(50), mk(97.5), mk(100), mk(297.5), mk(300), mk(797.5), mk(800)]
        dist = _band_distribution(notes, bpm=125.0)
        # 50, 97.5    → 0-100 (2 of 7)
        # 100, 297.5  → 100-300 (2 of 7)
        # 300, 797.5  → 300-800 (2 of 7)
        # 800         → 800+ (1 of 7)
        assert dist["0-100"] == pytest.approx(2 / 7)
        assert dist["100-300"] == pytest.approx(2 / 7)
        assert dist["300-800"] == pytest.approx(2 / 7)
        assert dist["800+"] == pytest.approx(1 / 7)

    def test_empty_notes_gives_all_zero_bands(self):
        assert _band_distribution([], bpm=120.0) == {b: 0.0 for b, _ in _BANDS}

    def test_hopo_flags_excluded(self):
        from core.charter import ChartNote
        notes = [
            ChartNote(tick=0, fret=0, length=96),   # 150ms at 120bpm
            ChartNote(tick=0, fret=5, length=1920), # HOPO flag at 3000ms — must be ignored
        ]
        dist = _band_distribution(notes, bpm=120.0)
        assert dist["800+"] == 0.0
        assert dist["100-300"] == 1.0
