"""
tests/test_genre_profiles.py — Unit tests for v2.0 genre profiles and HOPO forcing.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.analyzer import Analysis, HOP, SR
from core.config import Settings
from core.genre_profiles import GENRE_PROFILES, detect_genre, get_genre_profile
from core.charter import RESOLUTION, build_chart


def _mk_synth_analysis(bpm: float = 140.0, duration_s: float = 20.0) -> Analysis:
    beat_s = 60.0 / bpm
    n_beats = max(2, int(duration_s / beat_s))
    beats = np.array([i * beat_s for i in range(n_beats)])
    downbeats = beats[::4]

    # Create rapid onsets with varying pitches (tight consecutive notes)
    onsets = []
    onsets_per_beat = 2  # 8th notes
    for b in beats:
        onsets.append(b)
        onsets.append(b + beat_s / onsets_per_beat)
    onsets_arr = np.array(onsets)

    frames = max(2, int(duration_s * SR / HOP))
    pitch_hz = np.linspace(200.0, 500.0, frames)
    rms = np.linspace(0.2, 0.8, frames)
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


class TestGenreProfilesStructure:
    def test_required_profiles_exist(self):
        for expected in ["trap", "rock", "pop", "metal", "default"]:
            assert expected in GENRE_PROFILES, f"missing profile {expected}"

    def test_profile_fields(self):
        for name, p in GENRE_PROFILES.items():
            assert "chord_rate_target" in p, f"{name} missing chord_rate_target"
            assert "hopo_rate_target" in p, f"{name} missing hopo_rate_target"
            assert "npb_cap_by_bpm" in p, f"{name} missing npb_cap_by_bpm"
            assert "sustain_default_ms" in p, f"{name} missing sustain_default_ms"
            assert p["chord_rate_target"][0] <= p["chord_rate_target"][1]
            assert p["hopo_rate_target"][0] <= p["hopo_rate_target"][1]

    def test_get_genre_profile_fallback(self):
        assert get_genre_profile(None) == GENRE_PROFILES["default"]
        assert get_genre_profile("unknown_xyz") == GENRE_PROFILES["default"]
        assert get_genre_profile("ROCK") == GENRE_PROFILES["rock"]

    def test_detect_genre_keywords(self):
        assert detect_genre("Arctic Monkeys", "Brianstorm") == "rock"
        assert detect_genre("Metallica", "Master of Puppets") == "metal"
        assert detect_genre("Playboi Carti", "Magnolia") == "trap"
        assert detect_genre("Dua Lipa", "Levitating") == "pop"
        assert detect_genre("Unknown Artist", "Unknown Song") == "default"


class TestGenreCharterIntegration:
    def test_trap_profile_emits_no_hopo(self):
        a = _mk_synth_analysis()
        cd = build_chart(a, drums=None, genre_profile="trap")
        hopos = [n for n in cd.expert_single if n.fret == 5]
        assert len(hopos) == 0, f"trap profile should have 0 HOPO forces, got {len(hopos)}"

    def test_rock_profile_emits_hopos(self):
        a = _mk_synth_analysis()
        cd = build_chart(a, drums=None, genre_profile="rock")
        hopos = [n for n in cd.expert_single if n.fret == 5]
        assert len(hopos) > 0, "rock profile must emit HOPO forcing flags"

    def test_downcharts_strip_hopos_in_rock(self):
        a = _mk_synth_analysis()
        cd = build_chart(a, drums=None, genre_profile="rock")
        assert any(n.fret == 5 for n in cd.expert_single), "expert should have HOPO flags"
        assert not any(n.fret == 5 for n in cd.hard_single), "hard must strip HOPO flags"
        assert not any(n.fret == 5 for n in cd.medium_single), "medium must strip HOPO flags"
        assert not any(n.fret == 5 for n in cd.easy_single), "easy must strip HOPO flags"


class TestSettingsGenreFields:
    def test_settings_has_genre_and_manual_bpm(self):
        s = Settings()
        assert s.genre_profile == "auto"
        assert s.manual_bpm == 0.0


# ============================================================================
# Fase 13: v2.0 genre-profile isolation lock
# ============================================================================
#
# Baker spec on top of the existing structural + synthetic tests:
#   (1) Profile loading paths — including auto resolution with spotify
#       metadata and the auto fallback when no metadata is present.
#   (2) Isolation on R.I.P. — regen with trap vs rock, note count must
#       differ by >= 50 (profiles produce materially different outputs).
#   (3) HOPO return thresholds — rock >= 2% fret=5, trap == 0.
#
# The R.I.P. song.ogg is baker's named fixture; tests skip when it isn't
# present (CI without the Songs library) so the suite stays green there.

from pathlib import Path as _Path

_RIP_PATH = _Path(
    r"C:/Users/Kayky/Documents/Clone Hero/Songs/"
    r"Playboi Carti - Playboi Carti - R.I.P. (auto)/song.ogg"
)


@pytest.fixture(scope="module")
def _librosa_available():
    try:
        import librosa  # noqa: F401
    except ImportError:
        pytest.skip("librosa not installed in this environment")


@pytest.fixture(scope="module")
def _rip_analysis(_librosa_available):
    if not _RIP_PATH.exists():
        pytest.skip(f"R.I.P. song.ogg not available at {_RIP_PATH}")
    from core.analyzer import analyze_audio
    return analyze_audio(_RIP_PATH)


@pytest.fixture(scope="module")
def _rip_trap_chart(_rip_analysis):
    return build_chart(_rip_analysis, drums=None, genre_profile="trap")


@pytest.fixture(scope="module")
def _rip_rock_chart(_rip_analysis):
    return build_chart(_rip_analysis, drums=None, genre_profile="rock")


class TestProfileLoadingPaths:
    """Baker (1): the 4 ways Settings.genre_profile resolves to a profile."""

    def test_trap_setting_uses_trap_targets(self):
        p = get_genre_profile("trap")
        assert p["chord_rate_target"] == (0.20, 0.35)
        assert p["hopo_rate_target"] == (0.00, 0.02)
        assert p["sustain_default_ms"] == 300

    def test_rock_setting_uses_rock_targets(self):
        p = get_genre_profile("rock")
        assert p["chord_rate_target"] == (0.15, 0.35)
        assert p["hopo_rate_target"] == (0.03, 0.12)
        assert p["sustain_default_ms"] == 200

    def test_auto_with_spotify_rock_resolves_to_rock(self):
        # Pipeline's `auto` branch calls detect_genre; a Spotify genres list
        # containing 'rock' maps to the rock profile.
        assert detect_genre(artist="Someone",
                            title="Thing",
                            spotify_genres=["rock"]) == "rock"

    def test_auto_without_metadata_resolves_to_default(self):
        assert detect_genre(artist="", title="", spotify_genres=None) == "default"
        assert detect_genre() == "default"

    def test_rock_npb_caps_strictly_denser_than_trap(self):
        rock = get_genre_profile("rock")["npb_cap_by_bpm"]
        trap = get_genre_profile("trap")["npb_cap_by_bpm"]
        for band, cap_t in trap.items():
            assert rock[band] > cap_t, (
                f"band {band}: rock cap {rock[band]} not > trap cap {cap_t}"
            )


@pytest.mark.slow
class TestFase13IsolationOnRIP:
    """Baker (2): trap vs rock on R.I.P. must produce >= 50-note divergence."""

    def test_trap_vs_rock_note_count_differs_by_at_least_50(
        self, _rip_trap_chart, _rip_rock_chart
    ):
        t = len(_rip_trap_chart.expert_single)
        r = len(_rip_rock_chart.expert_single)
        diff = abs(t - r)
        assert diff >= 50, (
            f"R.I.P. trap={t} vs rock={r} expert_single — diff {diff} < 50; "
            f"v2.0 profile system is not producing materially different outputs"
        )


@pytest.mark.slow
class TestFase13HopoReturn:
    """Baker (3): on R.I.P. — rock emits >= 2% HOPO, trap emits 0."""

    def test_trap_hopo_count_is_zero_on_rip(self, _rip_trap_chart):
        hopo = sum(1 for n in _rip_trap_chart.expert_single if n.fret == 5)
        assert hopo == 0, (
            f"trap profile emitted {hopo} HOPO (fret=5) notes on R.I.P.; must be 0"
        )

    def test_rock_hopo_share_at_least_two_percent_on_rip(self, _rip_rock_chart):
        total = len(_rip_rock_chart.expert_single)
        hopo = sum(1 for n in _rip_rock_chart.expert_single if n.fret == 5)
        share = hopo / max(1, total)
        assert share >= 0.02, (
            f"rock profile emitted {hopo}/{total} = {share:.4f} HOPO share "
            f"< 0.02 — HOPO expression collapsed under the rock profile on R.I.P."
        )


# ============================================================================
# Fase 15: v2.2 beat_fill isolation lock
# ============================================================================
#
# Baker spec: with beat_fill wired in v2.2, rock profile must drive
# materially denser output than trap on the same audio:
#   (1) rock npb_avg >= 2 * trap npb_avg
#   (2) rock SP phrase count > trap SP phrase count
# Both on the same R.I.P. song.ogg analysis so the only variable is the
# profile choice.

from collections import Counter as _Counter


def _notes_per_bar_avg(cd) -> float:
    """Average notes per bar across every bar that carries at least one note."""
    bar_ticks = RESOLUTION * 4
    per_bar = _Counter(n.tick // bar_ticks for n in cd.expert_single)
    return len(cd.expert_single) / max(1, len(per_bar))


@pytest.mark.slow
class TestFase15BeatFillIsolation:
    """v2.2 beat_fill differentiator — on the SAME audio, rock must emit
    at least 2x the per-bar density of trap, and more star-power phrases."""

    def test_rock_npb_at_least_2x_trap_npb(self, _rip_trap_chart, _rip_rock_chart):
        trap_npb = _notes_per_bar_avg(_rip_trap_chart)
        rock_npb = _notes_per_bar_avg(_rip_rock_chart)
        ratio = rock_npb / max(1e-9, trap_npb)
        assert ratio >= 2.0, (
            f"rock npb {rock_npb:.3f} not >= 2x trap npb {trap_npb:.3f} "
            f"(ratio {ratio:.3f}); v2.2 beat_fill isolation failed"
        )

    def test_rock_sp_phrases_strictly_more_than_trap(
        self, _rip_trap_chart, _rip_rock_chart
    ):
        trap_sp = len(_rip_trap_chart.sp_guitar)
        rock_sp = len(_rip_rock_chart.sp_guitar)
        assert rock_sp > trap_sp, (
            f"rock SP phrases {rock_sp} not > trap SP phrases {trap_sp} — "
            f"v2.2 beat_fill failed to lift rock SP density"
        )

    def test_trap_npb_in_hand_charted_band(self, _rip_trap_chart):
        """Belt-and-braces: trap stays near hand-chart baseline (~3 npb)
        so the 2x assertion isn't trivially met by rock dropping to zero
        or trap exploding. Hand-chart reference range: 2.0-5.0 npb."""
        trap_npb = _notes_per_bar_avg(_rip_trap_chart)
        assert 2.0 <= trap_npb <= 5.0, (
            f"trap npb {trap_npb:.3f} outside hand-chart baseline [2.0, 5.0]; "
            f"2x assertion could pass trivially"
        )


# ============================================================================
# Fase 16: v2.3 beat_fill shape lock
# ============================================================================
#
# Baker spec — v2.3 shape fixes on the rock-profile output:
#   (1) chroma-informed beat_fill → all 5 colored frets each >= 5% share
#   (2) chord recovery → chord_rate >= 0.15 (regress from v2.2's 0.067)
#   (3) HOPO throttle → hopo_rate in [0.03, 0.11]
# Measured against R.I.P. song.ogg rock chart.


def _chord_rate_rock(notes) -> float:
    groups: dict = {}
    for n in notes:
        if n.fret == 5:
            continue
        groups[n.tick] = groups.get(n.tick, 0) + 1
    if not groups:
        return 0.0
    multi = sum(1 for c in groups.values() if c >= 2)
    return multi / len(groups)


def _fret_shares(notes) -> dict:
    """Share-of-total-REAL-notes for each colored fret 0..4. HOPO flags excluded."""
    real = [n for n in notes if n.fret != 5]
    total = len(real)
    counts = {}
    for n in real:
        counts[n.fret] = counts.get(n.fret, 0) + 1
    return {f: counts.get(f, 0) / max(1, total) for f in [0, 1, 2, 3, 4]}


@pytest.mark.slow
class TestFase16BeatFillShape:
    """v2.3 restored chord shape + chroma-informed fret spread + HOPO throttle."""

    def test_rock_all_five_frets_at_least_five_pct_on_rip(self, _rip_rock_chart):
        """Chroma-informed beat_fill must spread notes across the whole
        board — no colored fret drops below 5% share in the rock output."""
        shares = _fret_shares(_rip_rock_chart.expert_single)
        under = {f: round(s, 4) for f, s in shares.items() if s < 0.05}
        assert not under, (
            f"rock profile frets below 5% share on R.I.P.: {under}; "
            f"full distribution: { {f: round(s, 4) for f, s in shares.items()} }"
        )

    def test_rock_chord_rate_recovered_above_015(self, _rip_rock_chart):
        """v2.2 beat_fill emitted single notes → chord_rate collapsed to
        0.067. v2.3 recovered shape; chord_rate must be >= 0.15 to prove
        the beat_fill now carries chord structure, not just density."""
        rate = _chord_rate_rock(_rip_rock_chart.expert_single)
        assert rate >= 0.15, (
            f"rock chord_rate {rate:.4f} < 0.15 on R.I.P. — v2.3 chord "
            f"shape recovery regressed back to v2.2 single-note dilution"
        )

    def test_rock_hopo_rate_in_throttle_band(self, _rip_rock_chart):
        """Rock profile target band for HOPO rate is [0.03, 0.11]. v2.3
        throttle should keep output inside that band on R.I.P. (currently
        ~0.087)."""
        total = len(_rip_rock_chart.expert_single)
        hopo = sum(1 for n in _rip_rock_chart.expert_single if n.fret == 5)
        rate = hopo / max(1, total)
        assert 0.03 <= rate <= 0.11, (
            f"rock hopo_rate {rate:.4f} outside throttle band [0.03, 0.11] "
            f"on R.I.P. ({hopo}/{total} HOPO flags)"
        )
