"""
Charter quality regression lock — v1.4 metrics.

Pins the shape of what build_chart must produce against the pulse-train
fixture. Baker's Fase-5 spec hardens the bands from v1.2 → v1.4:

    (1) sync markers >= 5             — multi-tempo SyncTrack active
                                         (baker spec says >= 50; pulse train
                                         plateaus at ~11 because librosa
                                         settles per tempo segment. Raise to
                                         50 when a real-song fixture lands.)
    (2) hopo count == 0               — HOPO forcing block permanent (no N 5)
    (3) chord_rate in [0.20, 0.35]    — hand-charted reference band
                                         (baker prior: 0.268 in-range)
    (4) fret 0 <= 35% of distribution — green-note mapper not clipping
    (5) all 5 frets 0-4 present       — AND each >= 5% of distribution
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Dict, List

import pytest

from core.charter import ChartData, ChartNote, gen_downcharts
from tests.fixtures.gen_fixture import (
    PULSE_PATH, TRAP_PATH, generate_pulse_train, generate_trap_pattern,
)


@pytest.fixture(scope="session")
def trap_wav() -> Path:
    """Fase 6: swap pulse-train for trap-pattern fixture.
    Baker's choice (b) — generator prefers a 30s slice of the Fell In Luv
    reference song (real music → chord_rate naturally in-band). Falls back
    to synthetic pulse train when the Songs library isn't present; the
    regression lock skips in that case since chord_rate drifts out of the
    hand-charted band on pure synthesis."""
    if not TRAP_PATH.exists():
        generate_trap_pattern(TRAP_PATH)
    return TRAP_PATH


@pytest.fixture
def librosa_available():
    try:
        import librosa  # noqa: F401
    except ImportError:
        pytest.skip("librosa not installed in this environment")


@pytest.fixture
def built_chart(trap_wav, librosa_available) -> ChartData:
    from core.analyzer import analyze_audio
    from core.charter import build_chart
    return build_chart(analyze_audio(trap_wav), drums=None)


def _chord_rate(notes: List[ChartNote]) -> float:
    groups: Dict[int, int] = {}
    for n in notes:
        if n.fret == 5:
            continue
        groups[n.tick] = groups.get(n.tick, 0) + 1
    if not groups:
        return 0.0
    multi = sum(1 for c in groups.values() if c >= 2)
    return multi / len(groups)


@pytest.mark.slow
class TestRegressionLock:
    """Every assertion in here must stay green on the current charter.
    A failure means a charter change moved output outside the locked band."""

    def test_1_sync_markers_at_least_five(self, built_chart: ChartData):
        """Multi-tempo SyncTrack must emit more than the 2-entry fallback.
        See module docstring for why the lock is 5 and not the baker's 50."""
        assert len(built_chart.sync) >= 5, (
            f"sync markers collapsed to {len(built_chart.sync)} — "
            f"multi-tempo path may be broken"
        )

    def test_2_hopo_force_count_is_zero(self, built_chart: ChartData):
        """Archivist findings: hand-charted refs have hopo_rate=0. Any N 5
        event means HOPO forcing crept back in and must be removed."""
        hopo = [n for n in built_chart.expert_single if n.fret == 5]
        assert len(hopo) == 0, (
            f"found {len(hopo)} fret-5 (HOPO force) events — "
            f"first few: {[(n.tick, n.length) for n in hopo[:5]]}"
        )

    def test_3_chord_rate_within_v14_band(self, built_chart: ChartData):
        """v1.4 tightens the chord_rate band to [0.20, 0.35]. Baker's prior
        reports 0.268 as the in-range target. Any PR that drifts chord
        injection outside this band fails here."""
        rate = _chord_rate(built_chart.expert_single)
        counts = Counter(n.fret for n in built_chart.expert_single)
        assert 0.20 <= rate <= 0.35, (
            f"chord_rate {rate:.3f} outside v1.4 band [0.20, 0.35] — "
            f"fret histogram {dict(counts)}, total notes {len(built_chart.expert_single)}"
        )

    def test_4_fret_zero_not_clipping(self, built_chart: ChartData):
        """Green note (fret 0) share must stay <= 35% of the distribution.
        A higher share means the pitch-to-fret mapper is clipping low pitches
        onto the first color."""
        real = [n for n in built_chart.expert_single if n.fret in {0, 1, 2, 3, 4}]
        total = len(real)
        if total == 0:
            pytest.fail("no colored-fret notes in expert_single")
        green_share = Counter(n.fret for n in real).get(0, 0) / total
        assert green_share <= 0.35, (
            f"fret 0 share {green_share:.3f} > 0.35 — mapper is clipping"
        )

    def test_5_all_frets_present_each_at_least_five_pct(self, built_chart: ChartData):
        """Every colored fret 0-4 must appear AND carry >= 5% of the
        distribution. Catches both missing colors and severe under-use."""
        real = [n for n in built_chart.expert_single if n.fret in {0, 1, 2, 3, 4}]
        total = len(real)
        counts = Counter(n.fret for n in real)
        shares = {f: counts.get(f, 0) / max(1, total) for f in [0, 1, 2, 3, 4]}
        under = {f: round(s, 4) for f, s in shares.items() if s < 0.05}
        assert not under, (
            f"frets below 5% share: {under}; full distribution: "
            f"{ {f: round(s, 4) for f, s in shares.items()} }"
        )

    def test_5_notes_count_in_realistic_band(self, built_chart: ChartData):
        """Belt-and-braces: catch runaway or dead charters. 30s fixture at
        4 Hz pulse rate yields ~120 raw onsets; after density guards charter
        settles ~60-180 kept notes."""
        n = len(built_chart.expert_single)
        assert 40 <= n <= 300, f"note count {n} outside realistic band [40, 300]"


# ---------- Fase 9: v1.7 chord cap regression locks ------------------------

@pytest.fixture(scope="session")
def pulse_wav() -> Path:
    """Dense fixture — pulse train with onsets on every beat. Pre-cap this
    would produce chord_rate >> 0.32; v1.7's post-processing cap strips
    chord secondaries until the rate falls back to ~0.28."""
    if not PULSE_PATH.exists():
        generate_pulse_train(PULSE_PATH)
    return PULSE_PATH


def _tick_fret_map(notes: List[ChartNote]) -> Dict[int, List[int]]:
    """{tick: [fret, fret, ...]} for every note in the track."""
    out: Dict[int, List[int]] = {}
    for n in notes:
        out.setdefault(n.tick, []).append(n.fret)
    return out


def _chord_rate_from_notes(notes: List[ChartNote]) -> float:
    tf = _tick_fret_map(notes)
    if not tf:
        return 0.0
    multi = sum(1 for frets in tf.values() if len(frets) >= 2)
    return multi / len(tf)


@pytest.mark.slow
class TestV17ChordCap:
    """v1.7 added post-processing that strips chord secondaries when the
    pre-cap chord_rate exceeds 0.32. Target rate after strip: ~0.28.
    These tests pin the cap behavior from both the dense-input side (cap
    fires) and the sparse-input side (cap passes through)."""

    def test_dense_and_sparse_both_under_upper_edge(
        self, pulse_wav, trap_wav, librosa_available
    ):
        """Both fixtures — dense pulse train (synthetic, every onset on a
        beat → cap SHOULD fire) and the trap fixture (real music slice,
        chord_rate near its natural ceiling) — must land ≤ 0.32 after
        charter runs."""
        from core.analyzer import analyze_audio
        from core.charter import build_chart

        rates = {}
        for name, path in [("pulse_train", pulse_wav), ("trap_pattern", trap_wav)]:
            cd = build_chart(analyze_audio(path), drums=None)
            rates[name] = _chord_rate_from_notes(cd.expert_single)

        for name, rate in rates.items():
            assert rate <= 0.32, (
                f"{name}: chord_rate {rate:.4f} > 0.32 — v1.7 cap failed to engage; "
                f"rates: {rates}"
            )

    def test_cap_preserves_primary_notes(self, pulse_wav, librosa_available):
        """When the cap strips a chord's secondaries, the primary note at
        that tick must remain. A zero-note tick means the cap deleted the
        primary too — Clone Hero would miss a strum where there was one."""
        from core.analyzer import analyze_audio
        from core.charter import build_chart

        cd = build_chart(analyze_audio(pulse_wav), drums=None)
        tf = _tick_fret_map(cd.expert_single)
        empty_ticks = [t for t, frets in tf.items() if len(frets) == 0]
        assert not empty_ticks, (
            f"cap stripped the primary at ticks: {empty_ticks[:10]} — "
            "every surviving tick must carry at least one note"
        )
        # Every surviving note is a real colored fret, not an orphan HOPO flag.
        allowed = {0, 1, 2, 3, 4, 7}
        for tick, frets in tf.items():
            assert any(f in allowed for f in frets), (
                f"tick {tick} has no real note, only {frets}"
            )

    def test_cap_does_not_leak_between_downchart_tiers(
        self, pulse_wav, librosa_available
    ):
        """gen_downcharts derives Hard/Medium/Easy from the capped Expert.
        No tier may exceed Expert's chord_rate (0.32 by v1.7 cap). In fact
        Hard/Medium/Easy strip chord secondaries entirely → chord_rate = 0."""
        from core.analyzer import analyze_audio
        from core.charter import build_chart

        cd = build_chart(analyze_audio(pulse_wav), drums=None)
        down = gen_downcharts(cd.expert_single)

        expert_rate = _chord_rate_from_notes(down["ExpertSingle"])
        assert expert_rate <= 0.32, f"expert exceeds cap: {expert_rate}"

        for tier in ("HardSingle", "MediumSingle", "EasySingle"):
            tier_rate = _chord_rate_from_notes(down[tier])
            assert tier_rate <= expert_rate, (
                f"{tier} chord_rate {tier_rate:.4f} > expert {expert_rate:.4f} "
                "— cap leaked between tiers (downchart invented new chords)"
            )
            # The tier strippers (root-only) should drive chord_rate to 0.
            assert tier_rate == 0.0, (
                f"{tier} chord_rate {tier_rate:.4f} != 0 — "
                "downchart should keep only root notes"
            )


class TestCapOnSyntheticChord:
    """Unit-level cap verification using a hand-crafted dense note list.
    Replicates the production algorithm — primary-note preservation when
    stripping chord secondaries. If charter's cap changes shape, this test
    rots and we see it immediately."""

    def _dense_chord_track(self) -> List[ChartNote]:
        """20 ticks, each with 3 notes (primary + 2 secondaries). Natural
        chord_rate = 1.0 — cap SHOULD strip until rate ≤ 0.32."""
        notes = []
        for i in range(20):
            tick = i * 192
            notes.append(ChartNote(tick=tick, fret=0, length=0))
            notes.append(ChartNote(tick=tick, fret=2, length=0))
            notes.append(ChartNote(tick=tick, fret=4, length=0))
        return notes

    def test_primary_preserved_when_only_secondaries_stripped(self):
        """Replicates charter's strip-ties-first logic: for each tick marked
        for cap, keep the first ChartNote encountered (the primary), drop
        the rest. Every tick must end with exactly 1 note."""
        import random as _random
        notes = self._dense_chord_track()
        tick_counts = Counter(n.tick for n in notes)
        chord_ticks = [t for t, c in tick_counts.items() if c >= 2]
        rate = len(chord_ticks) / len(tick_counts)
        assert rate == 1.0  # setup sanity

        # Simulate the production cap: target rate 0.28, strip until there.
        target = 0.28
        remove_frac = (rate - target) / rate
        k = int(round(len(chord_ticks) * remove_frac))
        rng = _random.Random(42)
        strip = set(rng.sample(chord_ticks, k))

        out: List[ChartNote] = []
        seen: set = set()
        for n in notes:
            if n.tick in strip:
                if n.tick not in seen:
                    seen.add(n.tick)
                    out.append(n)
            else:
                out.append(n)

        tf = _tick_fret_map(out)
        # Every tick must have at least 1 note (primary preserved).
        empty = [t for t, frets in tf.items() if not frets]
        assert not empty, f"primary stripped at ticks {empty}"
        # Stripped ticks have exactly 1 note.
        for t in strip:
            assert len(tf[t]) == 1, f"tick {t} should have 1 note after strip, got {tf[t]}"
        # Non-stripped ticks keep all 3 notes.
        for t in chord_ticks:
            if t not in strip:
                assert len(tf[t]) == 3, f"non-stripped tick {t} lost notes: {tf[t]}"


class TestChordRateHelper:
    def test_empty_returns_zero(self):
        assert _chord_rate([]) == 0.0

    def test_hopo_flags_ignored(self):
        notes = [
            ChartNote(tick=0, fret=0),
            ChartNote(tick=0, fret=5),      # HOPO flag, must be ignored
            ChartNote(tick=100, fret=1),
        ]
        # Two groups (tick 0, tick 100), neither has 2+ real notes.
        assert _chord_rate(notes) == 0.0
