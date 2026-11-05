from collections import Counter
from dataclasses import dataclass, field
import random
from typing import Dict, List, Optional, Tuple

import numpy as np

from core.analyzer import Analysis, HOP, SR
from core.drum_transcriber import DrumEvents
from core.genre_profiles import get_genre_profile
from core.lyrics import LyricsData, analyze_structure


RESOLUTION = 192


@dataclass
class ChartNote:
    tick: int
    fret: int
    length: int = 0


@dataclass
class StarPhrase:
    tick: int
    length: int


@dataclass
class SectionEvent:
    tick: int
    label: str


@dataclass
class ChartData:
    bpm_milli: int
    sync: List[tuple] = field(default_factory=list)       # (tick, "B"|"TS", value)
    expert_single: List[ChartNote] = field(default_factory=list)
    hard_single: List[ChartNote] = field(default_factory=list)
    medium_single: List[ChartNote] = field(default_factory=list)
    easy_single: List[ChartNote] = field(default_factory=list)
    expert_drums: List[ChartNote] = field(default_factory=list)
    sp_guitar: List[StarPhrase] = field(default_factory=list)
    sp_drums: List[StarPhrase] = field(default_factory=list)
    events: List[SectionEvent] = field(default_factory=list)
    # (tick, event_payload) — payload is the quoted body for `E "..."` lines in
    # [Events], e.g. "phrase_start", "lyric Yo,", "phrase_end". Fed by _lyrics_events.
    lyrics_events: List[Tuple[int, str]] = field(default_factory=list)


def _time_to_tick(t: float, bpm: float) -> int:
    return int(round(t * (bpm / 60.0) * RESOLUTION))


def _quantize(tick: int, grid_div: int = 4) -> int:
    step = max(1, RESOLUTION // grid_div)
    return int(round(tick / step) * step)


def _frame_index(t: float) -> int:
    return int(t * SR / HOP)


def _median_smooth(xs: List[int], window: int = 3) -> List[int]:
    if window < 2 or len(xs) < window:
        return xs
    out = list(xs)
    half = window // 2
    for i in range(half, len(xs) - half):
        chunk = xs[i - half: i + half + 1]
        out[i] = int(np.median(chunk))
    return out


_LAST_PITCH_CUTS: List[float] = []


def _pitch_to_fret_contour(f0_sequence: List[float]) -> List[int]:
    """
    v1.5: TRIM-MEAN range with percentile normalize pass.
    Discards top/bottom 5% pitch outliers so subsonic bass hits don't collapse
    the range into bin 0. If any fret 0..4 has <10% distribution, redistributes
    via 10/30/50/70/90 percentiles.
    v2.3: Supports single-note/sub-sequence queries via cached song cuts.
    """
    global _LAST_PITCH_CUTS
    if not f0_sequence:
        return []
    valid = sorted(f for f in f0_sequence if f > 0)
    if len(valid) < 5:
        if _LAST_PITCH_CUTS:
            out_frets = []
            for f in f0_sequence:
                if f <= 0:
                    out_frets.append(2)
                else:
                    assigned = 4
                    for i, c in enumerate(_LAST_PITCH_CUTS):
                        if f <= c:
                            assigned = i
                            break
                    out_frets.append(assigned)
            return out_frets
        return [2] * len(f0_sequence)

    # Discard top/bottom 5% pitch outliers (TRIM MEAN range)
    p05 = int(len(valid) * 0.05)
    p95 = int(len(valid) * 0.95)
    trimmed = valid[p05:p95] if p95 > p05 else valid
    lo = trimmed[0]
    hi = trimmed[-1]
    step = (hi - lo) / 5.0 if hi > lo else 1.0
    cuts = [lo + (i + 1) * step for i in range(4)]
    _LAST_PITCH_CUTS = list(cuts)

    def _fret_for(f: float) -> int:
        if f <= 0:
            return -1
        for i, c in enumerate(cuts):
            if f <= c:
                return i
        return 4

    frets: List[int] = []
    streak = 0
    for f in f0_sequence:
        fret = _fret_for(f)
        if fret < 0:
            # unknown pitch — alternate up/down 1 fret from prev
            fret = (frets[-1] + 1) % 5 if frets else 2
        if frets and fret == frets[-1]:
            streak += 1
            if streak >= 2:
                # 3rd in a row same fret: step to a neighbor to break monotony
                fret = (fret + (1 if fret < 4 else -1)) % 5
                streak = 0
        else:
            streak = 0
        frets.append(fret)

    # (3) Normalize pass: if any fret 0..4 has <10% of distribution,
    # redistribute by rebinning using percentiles 10/30/50/70/90.
    total = len(frets) or 1
    counts = [frets.count(k) for k in range(5)]
    if any(c / total < 0.10 for c in counts):
        order = sorted(range(len(f0_sequence)), key=lambda i: f0_sequence[i])
        n = len(f0_sequence)
        rebin_frets = [0] * n
        for rank, idx in enumerate(order):
            if f0_sequence[idx] <= 0:
                continue
            pct = rank / n
            if pct < 0.10:
                rebin_frets[idx] = 0
            elif pct < 0.30:
                rebin_frets[idx] = 1
            elif pct < 0.50:
                rebin_frets[idx] = 2
            elif pct < 0.70:
                rebin_frets[idx] = 3
            else:
                rebin_frets[idx] = 4

        streak = 0
        frets = []
        for i, f in enumerate(f0_sequence):
            fret = rebin_frets[i] if f > 0 else ((frets[-1] + 1) % 5 if frets else 2)
            if frets and fret == frets[-1]:
                streak += 1
                if streak >= 2:
                    fret = (fret + (1 if fret < 4 else -1)) % 5
                    streak = 0
            else:
                streak = 0
            frets.append(fret)

    return frets


def _beat_locked_tick(t: float, beats, bpm: float, subdiv: int = 4) -> int:
    """
    Snap time t to the nearest subdiv-th of the ACTUAL beat positions.
    Beat N (by index) sits at tick N*RESOLUTION; subdivisions are
    N*RESOLUTION + k*(RESOLUTION/subdiv).

    This bypasses tempo drift accumulation. If librosa drifts 0.2 BPM over
    3 minutes, the time-based formula puts the last note ~400ms off; with
    beat-locked snapping, each note is anchored to its real detected beat.
    """
    import bisect
    if beats is None or len(beats) == 0:
        return _quantize(_time_to_tick(t, bpm), subdiv)

    beats_list = list(beats)
    idx = bisect.bisect_left(beats_list, t)
    if idx == 0:
        # before first beat — use first beat spacing to extrapolate
        if len(beats_list) >= 2:
            beat_dur = beats_list[1] - beats_list[0]
        else:
            beat_dur = 60.0 / max(bpm, 1e-3)
        beat_idx = 0
        beat_t = beats_list[0]
        frac = (t - beat_t) / beat_dur
    elif idx >= len(beats_list):
        if len(beats_list) >= 2:
            beat_dur = beats_list[-1] - beats_list[-2]
        else:
            beat_dur = 60.0 / max(bpm, 1e-3)
        beat_idx = len(beats_list) - 1
        beat_t = beats_list[-1]
        frac = (t - beat_t) / beat_dur
    else:
        # t is between beats[idx-1] and beats[idx]
        beat_idx = idx - 1
        beat_t = beats_list[idx - 1]
        beat_dur = beats_list[idx] - beat_t
        frac = (t - beat_t) / beat_dur if beat_dur > 1e-6 else 0.0

    # Snap frac with musical grid gravity: quarter notes (0.0/1.0) and eighth notes (0.50)
    # have higher gravity than 16th subdivisions, locking notes tightly to the rhythm.
    if frac < 0.14:
        snapped_frac = 0.0
    elif frac > 0.86:
        snapped_frac = 1.0
    elif 0.38 <= frac <= 0.62:
        snapped_frac = 0.50
    elif 0.14 <= frac < 0.38:
        snapped_frac = 0.25
    else:
        snapped_frac = 0.75
    tick_in_beat = int(round(snapped_frac * RESOLUTION))
    return max(0, beat_idx * RESOLUTION + tick_in_beat)


def _voice_chord(root_fret: int, chord_kind: str = "5", is_high_energy: bool = False) -> List[int]:
    """
    Ergonomic Clone Hero chord voicing (feel 30% -> 80%):
    1. Power chords ("5" / default): standard 5ths (0-2, 1-3, 2-4, 1-3, 2-4).
       Never wraps around disjointedly (e.g. Orange + Red is eliminated).
    2. Minor chords ("min"): compact adjacent dyads (0-1, 1-2, 2-3, 2-3, 3-4) for darker tension.
    3. Major chords ("maj"): resonant open/skip dyads (0-2, 1-3, 2-4, 1-3, 2-4).
    4. High energy (RMS >= p80): full 3-note power chord shapes ([0, 2, 3], [1, 3, 4], [1, 2, 4], etc.).
    """
    r = max(0, min(4, root_fret))

    if is_high_energy:
        triples = {
            0: [0, 2, 3],
            1: [1, 3, 4],
            2: [1, 2, 4],
            3: [1, 2, 3],
            4: [2, 3, 4],
        }
        return triples.get(r, [0, 2, 3])

    if chord_kind == "min":
        min_dyads = {
            0: [0, 1],
            1: [1, 2],
            2: [2, 3],
            3: [2, 3],
            4: [3, 4],
        }
        return min_dyads.get(r, [r, min(4, r + 1)])
    elif chord_kind == "maj":
        maj_dyads = {
            0: [0, 2],
            1: [1, 3],
            2: [2, 4],
            3: [1, 3],
            4: [2, 4],
        }
        return maj_dyads.get(r, [r, min(4, r + 2)])
    else:
        power_dyads = {
            0: [0, 2],
            1: [1, 3],
            2: [2, 4],
            3: [1, 3],
            4: [2, 4],
        }
        return power_dyads.get(r, [r, min(4, r + 2)])


def _get_chord_at(a: Analysis, t: float) -> Tuple[str, float]:
    """Return (chord_kind, confidence) for time t."""
    if hasattr(a, "chords") and a.chords:
        fi = min(_frame_index(t), len(a.chords) - 1)
        if fi >= 0:
            ev = a.chords[fi]
            return ev.kind, ev.confidence
    return "5", 0.0


def _median_fret_around(a: Analysis, tick: int, out_notes: Optional[List[ChartNote]] = None) -> int:
    """
    Derive a sensible fret (0..4) around a tick using running chroma average and nearby notes.
    """
    t = (tick / RESOLUTION) * (60.0 / max(a.bpm, 1e-3))
    chroma_fret = 2
    if len(a.chroma) and a.chroma.shape[1] > 0:
        fi = min(_frame_index(t), a.chroma.shape[1] - 1)
        w0 = max(0, fi - 4)
        w1 = min(a.chroma.shape[1], fi + 5)
        chroma_win = a.chroma[:, w0:w1].mean(axis=1)
        top_pc = int(np.argmax(chroma_win))
        chroma_fret = int(round((top_pc / 11.0) * 4))

    if out_notes:
        nearby = [n.fret for n in out_notes if 0 <= n.fret <= 4 and abs(n.tick - tick) <= RESOLUTION * 2]
        if nearby:
            med_fret = int(round(float(np.median(nearby))))
            return max(0, min(4, int(round((med_fret + chroma_fret) / 2))))

    return max(0, min(4, chroma_fret))


def pitch_at(a: Analysis, t: float) -> float:
    """
    Get dominant pitch at time t (from YIN f0 series, fallback to chroma).
    """
    f0 = a.pitch_hz
    if len(f0) > 0:
        idx = min(_frame_index(t), len(f0) - 1)
        w0 = max(0, idx - 2)
        w1 = min(len(f0), idx + 3)
        window = f0[w0:w1]
        pos = window[window > 0]
        if len(pos):
            return float(np.median(pos))
    if len(a.chroma) and a.chroma.shape[1] > 0:
        fi = min(_frame_index(t), a.chroma.shape[1] - 1)
        top_pc = int(np.argmax(a.chroma[:, fi]))
        return float(100.0 + top_pc * 30.0)
    return 0.0


_pitch_at = pitch_at


def _guitar_notes(a: Analysis, events: Optional[List[SectionEvent]] = None,
                  genre_profile: str = "default") -> List[ChartNote]:
    if len(a.onsets) == 0:
        return []

    profile = get_genre_profile(genre_profile)
    chord_lo, chord_hi = profile.get("chord_rate_target", (0.20, 0.35))
    hopo_lo, hopo_hi = profile.get("hopo_rate_target", (0.00, 0.02))
    bpm_cap = profile.get("npb_cap_by_bpm", {
        (0, 90):    14,
        (90, 120):  18,
        (120, 150): 24,
        (150, 999): 30,
    })
    sustain_default_ms = profile.get("sustain_default_ms", 300)
    hopo_force_streak = profile.get("hopo_force_streak", 0)
    hopo_trigger_gap_ticks = profile.get("hopo_trigger_gap_ticks", 48)

    # Load profile-aware guards (Watchdog v2.1 supplement)
    dg = profile.get("density_guards", {})
    min_gap_ticks = dg.get("min_gap_ticks", 48)        # trap=48 keep, rock=32 (more notes)
    onset_percentile_floor = dg.get("onset_percentile_floor", 20)  # trap=20, rock=10
    per_bar_hard_cap = dg.get("per_bar_hard_cap", 24)  # trap=24, rock=32

    strengths = a.onset_strength
    if len(strengths):
        thresh = np.percentile(strengths, onset_percentile_floor)
        kept = [(t, s) for t, s in zip(a.onsets, strengths) if s >= thresh]
    else:
        kept = [(t, 1.0) for t in a.onsets]
    if not kept:
        return []

    # Get dominant pitch at each onset time (from YIN f0 series, fallback to chroma).
    pitch_at_onset: List[float] = [pitch_at(a, t) for t, _ in kept]
    frets = _pitch_to_fret_contour(pitch_at_onset)
    if genre_profile in ("trap", "default"):
        frets = _median_smooth(frets, 3)

    # v1.10: Section-aware chord density setup.
    # Pre-compute section intervals from cd.events.
    if events is None:
        events = _events(a, None)
    ev_sorted = sorted(events, key=lambda e: e.tick) if events else []

    def _sec_at_tick(tk: int) -> str:
        s = "intro"
        for ev in ev_sorted:
            if tk >= ev.tick:
                s = ev.label.lower()
            else:
                break
        if "chorus" in s:
            return "chorus"
        elif "verse" in s:
            return "verse"
        elif "bridge" in s:
            return "bridge"
        elif "intro" in s:
            return "intro"
        elif "outro" in s:
            return "outro"
        return "default"

    def _sec_target(sec: str) -> float:
        if sec == "chorus":
            return chord_hi
        elif sec == "verse":
            return min(0.18, chord_lo)
        elif sec == "bridge":
            return round((chord_lo + chord_hi) / 2, 2)
        return round((chord_lo + chord_hi) / 2, 2)

    # Ticks for guitar: BEAT-LOCKED to detected beats (no BPM-drift accumulation).
    # Downbeat snap set uses the same locked grid.
    out: List[ChartNote] = []
    seen_ticks: Dict[int, int] = {}
    bar_ticks = RESOLUTION * 4
    per_bar: Dict[int, int] = {}
    beat_tick_set = {_beat_locked_tick(float(b), a.beats, a.bpm, subdiv=4) for b in a.beats}

    times_frets = list(zip([t for t, _ in kept], frets))
    overrides: Dict[int, int] = {}

    # Pre-compute set of ticks within chorus, verse, and bridge ranges
    chorus_ticks: set = set()
    verse_ticks: set = set()
    bridge_ticks: set = set()
    for i, (t, _) in enumerate(times_frets):
        tk = overrides.get(i, _beat_locked_tick(t, a.beats, a.bpm, subdiv=4))
        sec = _sec_at_tick(tk)
        if sec == "chorus":
            chorus_ticks.add(tk)
        elif sec == "verse":
            verse_ticks.add(tk)
        elif sec == "bridge":
            bridge_ticks.add(tk)

    sec_chord_counts: Dict[str, int] = {"chorus": 0, "verse": 0, "bridge": 0, "default": 0}
    sec_note_counts: Dict[str, int] = {"chorus": 0, "verse": 0, "bridge": 0, "default": 0}

    for i, (t, fret) in enumerate(times_frets):
        tick = overrides.get(i, _beat_locked_tick(t, a.beats, a.bpm, subdiv=4))
        bar = tick // bar_ticks

        # v1.8 / v2.0 / v2.1: Genre-aware density scaling.
        cap = next((v for (lo, hi), v in bpm_cap.items() if lo <= a.bpm < hi), per_bar_hard_cap)
        bar_limit = max(cap, per_bar_hard_cap) if genre_profile == "rock" else cap

        if genre_profile in ("trap", "default"):
            bpm_subdiv = {
                (0, 90):    1,   # quarter note gap (192 ticks)
                (90, 120):  1,   # slow trap gap (192 ticks)
                (120, 150): 4,   # standard 16th gap (48 ticks)
                (150, 999): 4,   # fast 16th gap (48 ticks)
            }
            subdiv_by_bpm = next((v for (lo, hi), v in bpm_subdiv.items() if lo <= a.bpm < hi), 4)
            min_gap = max(min_gap_ticks, int(RESOLUTION / subdiv_by_bpm))
        else:
            min_gap = min_gap_ticks

        if seen_ticks and tick - max(seen_ticks) < min_gap:
            continue
        if per_bar.get(bar, 0) >= bar_limit:
            continue

        # v1.9.1 / v2.0: default sustain per genre profile
        length = 0
        if i + 1 < len(times_frets):
            next_t = times_frets[i + 1][0]
            # (1) Gap snap: if gap to next onset is 150-220 ticks, snap to 192;
            # if 350-410, snap to 384. Pulls interval distribution into hand-charted anchor peaks.
            raw_gap = _time_to_tick(next_t, a.bpm) - _time_to_tick(t, a.bpm)
            if 150 <= raw_gap <= 220:
                overrides[i + 1] = tick + 192
            elif 350 <= raw_gap <= 410:
                overrides[i + 1] = tick + 384

            gap_ms = (times_frets[i + 1][0] - t) * 1000
            # Notes in running sequences (gap < 240ms) are crisp TAPS (length = 0),
            # giving a punchy, responsive rhythm feel matching human charting (EVILJ0RDAN, GH3).
            # Notes with room to breathe (gap >= 240ms) carry an ergonomic sustain
            # with a 48-tick release margin so buttons are released cleanly before the next hit.
            if gap_ms >= 240:
                detach_ms = (48 / RESOLUTION) * (60000.0 / a.bpm)
                sustain_cap = 500 if genre_profile in ("trap", "default") else sustain_default_ms
                sustain_ms = min(gap_ms - detach_ms, sustain_cap)
                if sustain_ms >= 120:
                    length = int(sustain_ms * (a.bpm / 60) * RESOLUTION / 1000)
                    length = min(length, int(0.8 * (a.bpm / 60) * RESOLUTION))
                else:
                    length = 0
            else:
                length = 0

        out.append(ChartNote(tick=tick, fret=fret, length=length))
        seen_ticks[tick] = seen_ticks.get(tick, 0) + 1

        sec = "chorus" if tick in chorus_ticks else ("verse" if tick in verse_ticks else ("bridge" if tick in bridge_ticks else "default"))
        sec_note_counts[sec] += 1
        target = _sec_target(sec)
        allow_chord = (sec_chord_counts[sec] / max(1, sec_note_counts[sec])) < target

        # Chord injection — on EVERY beat (Archivist round-2 fix), subject to section target rate.
        # v1.10: chorus cap target 0.35, verse cap target 0.18, bridge/intro/outro 0.26.
        # Diversified root via secondary chroma peak. Power-chord adds a third note on top-20% RMS beats.
        # v1.4: replace root with round-robin fret_r when primary - secondary chroma peak < 0.1.
        if tick in beat_tick_set and fret != 7 and allow_chord:
            bar_idx = tick // bar_ticks
            beat_idx = (tick % bar_ticks) // RESOLUTION
            fret_r = (bar_idx * 7 + beat_idx) % 5

            root_fret = fret
            chord_kind, conf = _get_chord_at(a, t)
            try:
                fi_chroma = min(_frame_index(t), a.chroma.shape[1] - 1)
                col = a.chroma[:, fi_chroma].copy()
                primary_pc = int(np.argmax(col))
                primary_val = float(col[primary_pc])
                col[primary_pc] = -1.0
                secondary_pc = int(np.argmax(col))
                secondary_val = float(col[secondary_pc])
                chroma_diff = primary_val - secondary_val

                if chroma_diff < 0.1 or conf < 0.35:
                    root_fret = fret_r
            except Exception:
                pass

            is_high_rms = False
            if len(a.rms) and (a.rms.max() - a.rms.min() > 1e-4):
                fi = min(_frame_index(t), len(a.rms) - 1)
                if a.rms[fi] >= np.percentile(a.rms, 80):
                    is_high_rms = True

            voicings = _voice_chord(root_fret, chord_kind=chord_kind, is_high_energy=is_high_rms)
            out[-1].fret = voicings[0]
            for extra in voicings[1:]:
                out.append(ChartNote(tick=tick, fret=extra, length=length))
                seen_ticks[tick] += 1
            sec_chord_counts[sec] += 1

        # NOTE: HOPO forcing (fret=5 injection) was REMOVED per Archivist findings —
        # hand-charted refs have hopo_rate=0; our v1.2 was emitting 0.086 (out-of-band).
        # Natural HOPOs still fire from CH's parser based on tick spacing + fret diff.

        # Open note (fret 7) on sub-bass hits — cap 1 per TWO bars (less noisy).
        if len(a.centroid) and len(a.rms):
            fi = min(_frame_index(t), len(a.centroid) - 1)
            cen_rng = max(1.0, a.centroid.max() - a.centroid.min())
            rms_rng = max(1e-9, a.rms.max() - a.rms.min())
            cen_pct = (a.centroid[fi] - a.centroid.min()) / cen_rng
            rms_pct = (a.rms[fi] - a.rms.min()) / rms_rng
            if cen_pct < 0.15 and rms_pct > 0.70:
                two_bar = bar // 2
                if not any(n.tick // (bar_ticks * 2) == two_bar and n.fret == 7 for n in out):
                    out.append(ChartNote(tick=tick, fret=7, length=0))

        per_bar[bar] = per_bar.get(bar, 0) + 1

    # Passo extra APOS o loop principal (v1.4 / v1.10):
    # Para cada onset top-30% RMS que NAO caiu em beat_tick_set, insira um chord no
    # proprio tick raw (nao snap no beat) se o headroom do section chord_rate permitir.
    if len(a.rms) and (a.rms.max() - a.rms.min() > 1e-4):
        rms_p70 = np.percentile(a.rms, 70)
        rms_p80 = np.percentile(a.rms, 80)
        step1_ticks = set()
        for i, (t, _) in enumerate(times_frets):
            tick = overrides.get(i, _beat_locked_tick(t, a.beats, a.bpm, subdiv=4))
            bar = tick // bar_ticks
            if per_bar.get(bar, 0) >= bar_limit:
                continue
            if tick in beat_tick_set or tick in step1_ticks:
                continue
            fi = min(_frame_index(t), len(a.rms) - 1)
            if a.rms[fi] < rms_p70:
                continue
            step1_ticks.add(tick)

            sec = "chorus" if tick in chorus_ticks else ("verse" if tick in verse_ticks else ("bridge" if tick in bridge_ticks else "default"))
            target = _sec_target(sec)
            allow_chord = (sec_chord_counts[sec] / max(1, sec_note_counts[sec])) < target
            if not allow_chord:
                continue

            bar_idx = tick // bar_ticks
            beat_idx = (tick % bar_ticks) // RESOLUTION
            fret_r = (bar_idx * 7 + beat_idx) % 5

            root_fret = fret_r
            chord_kind, conf = _get_chord_at(a, t)
            is_high_rms = (a.rms[fi] >= rms_p80)
            voicings = _voice_chord(root_fret, chord_kind=chord_kind, is_high_energy=is_high_rms)

            existing = [n for n in out if n.tick == tick and n.fret != 7]
            if existing:
                existing[0].fret = voicings[0]
                length = existing[0].length
                for extra in voicings[1:]:
                    out.append(ChartNote(tick=tick, fret=extra, length=length))
                    seen_ticks[tick] = seen_ticks.get(tick, 0) + 1
                sec_chord_counts[sec] += 1
                per_bar[bar] = per_bar.get(bar, 0) + (len(voicings) - 1)
            elif not any(n.tick == tick and n.fret == 7 for n in out):
                for f in voicings:
                    out.append(ChartNote(tick=tick, fret=f, length=0))
                seen_ticks[tick] = seen_ticks.get(tick, 0) + len(voicings)
                sec_chord_counts[sec] += 1
                sec_note_counts[sec] += 1
                per_bar[bar] = per_bar.get(bar, 0) + len(voicings)

    # v1.10: section-aware chord dampening.
    # For each section (chorus: 0.35, verse: 0.18, bridge/intro/outro: 0.26),
    # if actual chord rate > target, strip chord secondaries randomly down to target.
    tick_counts = Counter(n.tick for n in out)
    if tick_counts:
        chord_ticks = [t for t, c in tick_counts.items() if c >= 2]
        all_unique_ticks = set(tick_counts.keys())
        rng = random.Random(42)
        ticks_to_strip = set()

        verse_t = min(0.18, chord_lo)
        bridge_t = round((chord_lo + chord_hi) / 2, 2)
        for s, s_target in [("chorus", chord_hi), ("verse", verse_t), ("bridge", bridge_t), ("default", bridge_t)]:
            s_ticks = [t for t in all_unique_ticks if _sec_at_tick(t) == s]
            s_chords = [t for t in chord_ticks if _sec_at_tick(t) == s]
            if not s_ticks or not s_chords:
                continue
            s_rate = len(s_chords) / len(s_ticks)
            if s_rate > s_target:
                remove_frac = (s_rate - s_target) / s_rate
                k = int(round(len(s_chords) * remove_frac))
                if k > 0:
                    ticks_to_strip.update(rng.sample(s_chords, min(k, len(s_chords))))

        if ticks_to_strip:
            filtered_out = []
            seen_stripped = set()
            for n in out:
                if n.tick in ticks_to_strip:
                    if n.tick not in seen_stripped:
                        seen_stripped.add(n.tick)
                        filtered_out.append(n)
                else:
                    filtered_out.append(n)
            out = filtered_out

        # v1.7 / v2.0 guard: overall chart chord rate must never exceed upper bound
        overall_limit = max(0.28, chord_hi - 0.03)
        curr_counts = Counter(n.tick for n in out)
        curr_chords = [t for t, c in curr_counts.items() if c >= 2]
        if curr_counts and len(curr_chords) / len(curr_counts) > overall_limit:
            rate = len(curr_chords) / len(curr_counts)
            target_rate = round((chord_lo + chord_hi) / 2, 2)
            remove_frac = (rate - target_rate) / rate
            k = int(round(len(curr_chords) * remove_frac))
            if k > 0:
                strip_more = set(rng.sample(curr_chords, min(k, len(curr_chords))))
                filtered_out = []
                seen_stripped = set()
                for n in out:
                    if n.tick in strip_more:
                        if n.tick not in seen_stripped:
                            seen_stripped.add(n.tick)
                            filtered_out.append(n)
                    else:
                        filtered_out.append(n)
    # v2.2: beat_fill notes for rock (arpeggios/hammer-ons on beat grid)
    beat_fill_notes = profile.get("beat_fill_notes", 0)  # 0 for trap, 2 for rock
    if beat_fill_notes > 0 and len(a.beats) > 0:
        bar_ticks = RESOLUTION * 4
        beat_dur = 60.0 / max(a.bpm, 1e-3)
        rms_floor = np.percentile(a.rms, 10) if len(a.rms) else 0.0

        for i, bt in enumerate(a.beats):
            t_beat = float(bt)
            fi = min(_frame_index(t_beat), len(a.rms) - 1) if len(a.rms) else 0
            if len(a.rms) and a.rms[fi] < rms_floor:
                continue

            slots = [t_beat]
            if beat_fill_notes >= 2:
                if i + 1 < len(a.beats):
                    next_bt = float(a.beats[i + 1])
                    slots.append((t_beat + next_bt) / 2.0)
                else:
                    slots.append(t_beat + 0.5 * beat_dur)

            for slot_t in slots:
                tick = _beat_locked_tick(slot_t, a.beats, a.bpm, subdiv=4)
                bar = tick // bar_ticks
                if per_bar.get(bar, 0) >= bar_limit:
                    continue
                if tick not in seen_ticks:
                    fret = _pitch_to_fret_contour([pitch_at(a, slot_t)])[0]
                    out.append(ChartNote(tick=tick, fret=fret, length=0))
                    seen_ticks[tick] = seen_ticks.get(tick, 0) + 1
                    per_bar[bar] = per_bar.get(bar, 0) + 1

                    # v2.3: Chord injection extend to beat_fill
                    sec = "chorus" if tick in chorus_ticks else ("verse" if tick in verse_ticks else ("bridge" if tick in bridge_ticks else "default"))
                    sec_note_counts[sec] = sec_note_counts.get(sec, 0) + 1
                    target = _sec_target(sec)
                    allow_chord = (sec_chord_counts.get(sec, 0) / max(1, sec_note_counts[sec])) < target

                    if tick in beat_tick_set and fret != 7 and allow_chord:
                        bar_idx = tick // bar_ticks
                        beat_idx = (tick % bar_ticks) // RESOLUTION
                        fret_r = (bar_idx * 7 + beat_idx) % 5

                        root_fret = fret
                        chord_kind, conf = _get_chord_at(a, slot_t)
                        try:
                            fi_chroma = min(_frame_index(slot_t), a.chroma.shape[1] - 1)
                            col = a.chroma[:, fi_chroma].copy()
                            primary_pc = int(np.argmax(col))
                            primary_val = float(col[primary_pc])
                            col[primary_pc] = -1.0
                            secondary_pc = int(np.argmax(col))
                            secondary_val = float(col[secondary_pc])
                            chroma_diff = primary_val - secondary_val

                            if chroma_diff < 0.1 or conf < 0.35:
                                root_fret = fret_r
                        except Exception:
                            pass

                        is_high_rms = False
                        if len(a.rms) and (a.rms.max() - a.rms.min() > 1e-4):
                            fi_s = min(_frame_index(slot_t), len(a.rms) - 1)
                            if a.rms[fi_s] >= np.percentile(a.rms, 80):
                                is_high_rms = True

                        voicings = _voice_chord(root_fret, chord_kind=chord_kind, is_high_energy=is_high_rms)
                        out[-1].fret = voicings[0]
                        for extra in voicings[1:]:
                            out.append(ChartNote(tick=tick, fret=extra, length=0))
                            seen_ticks[tick] = seen_ticks.get(tick, 0) + 1
                        sec_chord_counts[sec] += 1

    # v2.0 / v2.1: Genre profile HOPO forcing
    # Reactivate if profile hopo_rate_target max > 0.02: emit HOPO forcing (fret=5) on tight+different consecutive onsets
    if hopo_hi > 0.02:
        tick_map: Dict[int, List[ChartNote]] = {}
        for n in out:
            tick_map.setdefault(n.tick, []).append(n)

        sorted_ticks = sorted(tick_map.keys())
        total_groups = max(1, len(sorted_ticks))
        forced_ticks: set = set()

        # Step 1: Detect streaks of fast transitions and tight transitions
        streak = 0
        streak_candidates: List[int] = []

        for idx in range(1, len(sorted_ticks)):
            prev_t = sorted_ticks[idx - 1]
            curr_t = sorted_ticks[idx]

            prev_frets = [n.fret for n in tick_map[prev_t] if 0 <= n.fret <= 4]
            curr_frets = [n.fret for n in tick_map[curr_t] if 0 <= n.fret <= 4]

            # Single notes only (not chords)
            if len(prev_frets) == 1 and len(curr_frets) == 1:
                f_prev = prev_frets[0]
                f_curr = curr_frets[0]
                gap_ticks = curr_t - prev_t

                # Fast transition: different frets and gap <= 96 ticks
                if f_curr != f_prev and gap_ticks <= 96:
                    streak += 1
                    if streak >= (hopo_force_streak or 2) or gap_ticks <= hopo_trigger_gap_ticks:
                        streak_candidates.append(curr_t)
                else:
                    streak = 0
            else:
                streak = 0

        # Emit fret=5 for streak notes up to hopo_hi
        for tk in streak_candidates:
            if (len(forced_ticks) + 1) / total_groups <= hopo_hi:
                forced_ticks.add(tk)

        # Step 2: If hopo_rate still < target_floor, emit additional HOPO forcings on fast single-note transitions
        target_floor = min(hopo_hi, max(0.04, (hopo_lo + hopo_hi) / 2))
        if len(forced_ticks) / total_groups < target_floor:
            # Gather other eligible transitions (tight transitions gap <= 96, diff fret)
            additional_candidates: List[Tuple[int, int]] = []  # (gap_ticks, tick)
            for idx in range(1, len(sorted_ticks)):
                prev_t = sorted_ticks[idx - 1]
                curr_t = sorted_ticks[idx]
                if curr_t in forced_ticks:
                    continue

                prev_frets = [n.fret for n in tick_map[prev_t] if 0 <= n.fret <= 4]
                curr_frets = [n.fret for n in tick_map[curr_t] if 0 <= n.fret <= 4]

                if len(prev_frets) == 1 and len(curr_frets) == 1:
                    f_prev = prev_frets[0]
                    f_curr = curr_frets[0]
                    gap_ticks = curr_t - prev_t
                    if f_curr != f_prev and gap_ticks <= 96:
                        additional_candidates.append((gap_ticks, curr_t))

            # Prioritize tighter gaps first (fastest transitions are most natural HOPOs)
            additional_candidates.sort(key=lambda x: x[0])
            for _, tk in additional_candidates:
                if len(forced_ticks) / total_groups >= target_floor:
                    break
                if (len(forced_ticks) + 1) / total_groups <= hopo_hi:
                    forced_ticks.add(tk)

        # Append all forced HOPOs
        for tk in sorted(forced_ticks):
            out.append(ChartNote(tick=tk, fret=5, length=0))

    out = _clean_sustains(out)
    return out


def _clean_sustains(notes: List[ChartNote]) -> List[ChartNote]:
    """
    Clean sustain tails and prevent note collisions:
    1. Anti-collision per fret: a sustain on fret F must end before the next note
       on fret F (leaves a clean 8-tick detach margin so notes never render on top of holds).
    2. Matching chord lengths: all fretted notes in a chord share the same length
       (eliminates the visual bug of one note tail sticking out while another stops).
    """
    if not notes:
        return []

    notes = [n for n in notes if n.tick >= 0]
    notes = sorted(notes, key=lambda n: (n.tick, n.fret))

    # 1. Anti-collision per fret: fret F sustain cannot overlap next note on fret F
    for i, n1 in enumerate(notes):
        if n1.length > 0:
            for j in range(i + 1, len(notes)):
                n2 = notes[j]
                if n2.fret == n1.fret and n2.tick > n1.tick:
                    max_len = max(0, n2.tick - n1.tick - 8)
                    if n1.length > max_len:
                        n1.length = max_len
                    break

    # 2. Match chord lengths at same tick: fretted notes in a chord share the same length
    by_tick: Dict[int, List[ChartNote]] = {}
    for n in notes:
        by_tick.setdefault(n.tick, []).append(n)

    for t, nlist in by_tick.items():
        if len(nlist) > 1:
            fretted = [n for n in nlist if n.fret != 7]
            if fretted:
                common = min(n.length for n in fretted)
                for n in fretted:
                    n.length = common

    out: List[ChartNote] = []
    for t in sorted(by_tick.keys()):
        out.extend(by_tick[t])
    out.sort(key=lambda n: (n.tick, n.fret))
    return out


def _drum_notes(a: Analysis, d: DrumEvents) -> List[ChartNote]:
    """
    Template-first drum generator.

    Spine of a hip-hop/trap beat:
        beat 1 of each bar: kick + hat
        beat 2:             snare + hat
        beat 3:             kick + hat
        beat 4:             snare + hat
        off-beats (8ths):   hat
    On top: percussive-onset ACCENTS fill ghost notes, hat rolls, extra kicks.
    This yields a chart that reads like a real drum pattern rather than raw
    onset vomit from the heuristic transcriber.
    """
    out: List[ChartNote] = []
    seen: set = set()

    def _emit(tick: int, fret: int, with_kick_flag: bool = False) -> None:
        if tick < 0:
            return
        key = (tick, fret)
        if key in seen:
            return
        seen.add(key)
        out.append(ChartNote(tick=tick, fret=fret, length=0))
        if with_kick_flag and fret == 0:
            k2 = (tick, 66)
            if k2 not in seen:
                seen.add(k2)
                out.append(ChartNote(tick=tick, fret=66, length=0))

    beats = list(a.beats)
    if not beats:
        return out

    # Spine on detected beats — ticks come from _beat_locked_tick so they anchor
    # to the real audio timeline (no BPM-drift accumulation).
    for i, bt in enumerate(beats):
        tick = _beat_locked_tick(float(bt), a.beats, a.bpm, subdiv=8)
        pos = i % 4
        if pos == 0 or pos == 2:
            _emit(tick, fret=0, with_kick_flag=True)
        else:
            _emit(tick, fret=1)
        _emit(tick, fret=2)

        # Off-beat 8th between this beat and the next: hat. Guard: skip if the
        # upcoming accent pass is likely to flood this bar already (checked below).
        if i + 1 < len(beats):
            mid_t = (float(bt) + float(beats[i + 1])) / 2
            mid_tick = _beat_locked_tick(mid_t, a.beats, a.bpm, subdiv=8)
            _emit(mid_tick, fret=2)

    # Accent pass — percussive onsets become ghost notes / syncopation.
    for t in d.kicks:
        tick = _beat_locked_tick(float(t), a.beats, a.bpm, subdiv=16)
        _emit(tick, fret=0, with_kick_flag=True)
    for t in d.snares[::2]:
        tick = _beat_locked_tick(float(t), a.beats, a.bpm, subdiv=16)
        _emit(tick, fret=1)
    # Cymbal crash on bar 1 of each 8-bar block.
    bar_ticks = RESOLUTION * 4
    total_bars = max(1, int(a.duration_s * a.bpm / 60 / 4))
    for b in range(0, total_bars, 8):
        _emit(b * bar_ticks, fret=4)

    # Density guard: cap at 48 notes per bar.
    per_bar: Dict[int, int] = {}
    filtered: List[ChartNote] = []
    for n in sorted(out, key=lambda x: (x.tick, x.fret)):
        bar = n.tick // bar_ticks
        if n.fret != 66 and per_bar.get(bar, 0) >= 48:
            continue
        filtered.append(n)
        if n.fret != 66:
            per_bar[bar] = per_bar.get(bar, 0) + 1
    return filtered


def _star_power(a: Analysis, genre_profile: str = "default") -> List[StarPhrase]:
    if a.bpm <= 0 or len(a.rms) == 0:
        return []
    profile = get_genre_profile(genre_profile)
    sp_phrase_divisor = profile.get("sp_phrase_divisor", 8)

    bar_s = (60.0 / a.bpm) * 4
    bar_count = max(1, int(a.duration_s / bar_s))
    bar_rms = []
    for i in range(bar_count):
        f0 = _frame_index(i * bar_s)
        f1 = _frame_index((i + 1) * bar_s)
        f1 = min(f1, len(a.rms))
        if f1 > f0:
            bar_rms.append((i, float(a.rms[f0:f1].mean())))
    if not bar_rms:
        return []
    bar_rms.sort(key=lambda x: x[1], reverse=True)
    hot_fraction = 3 if sp_phrase_divisor <= 4 else 5
    hot_bars = {b for b, _ in bar_rms[: max(4, len(bar_rms) // hot_fraction)]}
    bar_ticks = RESOLUTION * 4
    phrases = []
    for i in range(0, bar_count, sp_phrase_divisor):
        if i in hot_bars:
            phrases.append(StarPhrase(tick=i * bar_ticks, length=bar_ticks))
    max_phrases = 16 if sp_phrase_divisor <= 4 else 8
    return phrases[:max_phrases]


def _events(a: Analysis, lyrics: Optional[LyricsData] = None) -> List[SectionEvent]:
    """
    Build section events.

    When lyrics are available, lyric-derived chorus/verse labels take priority
    at their timestamps. MFCC-derived boundaries fill in anywhere the lyrics
    don't already cover, giving the chart intermediate markers for instrumental
    sections (intro, bridge, outro) that have no vocals.

    Collision rule: an MFCC event within TOLERANCE_S of a lyric event is
    dropped (the lyric label is more trustworthy). An MFCC event outside that
    window is kept with its original label.
    """
    TOLERANCE_S = 2.0

    lyric_events: List[Tuple[float, str]] = []
    if lyrics is not None:
        try:
            lyric_events = analyze_structure(lyrics)
        except Exception:
            lyric_events = []

    # Lyrics first — they drive the chorus/verse labeling.
    merged: List[Tuple[float, str, int]] = []  # (time_s, label, priority) ; higher priority wins
    for t, label in lyric_events:
        merged.append((float(t), label, 2))

    # MFCC fills gaps. Drop any MFCC event that lands within TOLERANCE_S of a
    # lyric event — the lyric label is semantically truer.
    lyric_times = [t for t, _, _ in merged]
    for t, label in a.sections:
        tf = float(t)
        if any(abs(tf - lt) <= TOLERANCE_S for lt in lyric_times):
            continue
        merged.append((tf, label, 1))

    # Sort by time, then by descending priority so if two events ever share a
    # tick after quantization the higher-priority label is kept.
    merged.sort(key=lambda e: (e[0], -e[2]))

    out: List[SectionEvent] = []
    seen_ticks: set = set()
    for t_s, label, _prio in merged:
        tick = _quantize(_time_to_tick(t_s, a.bpm), 4)
        if tick in seen_ticks:
            continue
        seen_ticks.add(tick)
        out.append(SectionEvent(tick=tick, label=label))
    return out


def _lyrics_events(a: Analysis, lyrics: Optional[LyricsData]) -> List[Tuple[int, str]]:
    """
    Convert LRCLIB line-level timing into Clone Hero's word-level [Events] stream.

    Format emitted (matches hand-charted convention — see Magnolia.chart):
        tick = E "phrase_start"
        tick = E "lyric Yo,"
        tick = E "lyric guess"
        tick = E "lyric who's"
        ...
        tick = E "phrase_end"

    LRCLIB only gives per-line timing, so word times are interpolated linearly
    from the line's start to the next line's start (or +2s for the final line).
    All ticks are snapped via _beat_locked_tick so lyrics ride the same real-audio
    grid as the notes — no BPM-drift accumulation.
    """
    if lyrics is None or not lyrics.lines or not lyrics.times_s:
        return []

    beats = a.beats
    bpm = a.bpm
    duration = a.duration_s
    out: List[Tuple[int, str]] = []

    # Pre-sanitize: strip empties and keep line/time parity, which fetch_synced_lyrics
    # already guarantees — the belt-and-suspenders check costs nothing.
    pairs = [(float(t), s) for t, s in zip(lyrics.times_s, lyrics.lines) if s.strip()]
    pairs.sort(key=lambda p: p[0])
    if not pairs:
        return []

    for i, (line_t, line_text) in enumerate(pairs):
        # End time = next line start, or +2s past this line as a tail buffer for the last.
        if i + 1 < len(pairs):
            end_t = pairs[i + 1][0]
        else:
            end_t = min(duration, line_t + 2.0)
        if end_t <= line_t:
            end_t = line_t + 0.5

        words = line_text.split()
        if not words:
            continue

        phrase_start_tick = _beat_locked_tick(line_t, beats, bpm, subdiv=8)
        phrase_end_tick = _beat_locked_tick(end_t, beats, bpm, subdiv=8)
        if phrase_end_tick <= phrase_start_tick:
            phrase_end_tick = phrase_start_tick + max(1, RESOLUTION // 4)

        out.append((phrase_start_tick, "phrase_start"))

        # Linear word distribution across the line's time span. Snap each to a
        # 16th-note on the beat-locked grid so word onsets feel musical, not
        # arithmetic. Duplicate-tick collisions after snapping are kept — Clone
        # Hero's lyric parser handles stacked events at the same tick.
        span = end_t - line_t
        n = len(words)
        for wi, word in enumerate(words):
            # First word anchors to the line start exactly; later words interpolate.
            frac = wi / n if n > 1 else 0.0
            wt = line_t + span * frac
            wtick = _beat_locked_tick(wt, beats, bpm, subdiv=16)
            # Keep words bounded inside the phrase window.
            wtick = max(phrase_start_tick, min(phrase_end_tick - 1, wtick))
            # Clone Hero's lyric event payload is the literal word; escape embedded
            # quotes so the chart stays well-formed.
            safe = word.replace('"', "'")
            out.append((wtick, f"lyric {safe}"))

        out.append((phrase_end_tick, "phrase_end"))

    # Preserve emission order; chart_writer re-sorts by tick while keeping stable
    # ordering so phrase_start / lyric / phrase_end at colliding ticks stay in sequence.
    return out


def gen_downcharts(expert: List[ChartNote]) -> Dict[str, List[ChartNote]]:
    """
    v1.6: auto-difficulty downcharting from ExpertSingle.
    - ExpertSingle: keep as-is.
    - HardSingle: strip HOPO (fret 5) + chord secondaries (single root note per tick),
      stride-sampling 1 in 2 notes per bar (notes[::2]).
    - MediumSingle: strip opens (fret 7) + chords + HOPO,
      stride-sampling 1 in 3 notes per bar (notes[::3]).
    - EasySingle: strip opens + chords + HOPO,
      only beat 1 and 3 of each bar (beats 0 and 2 in 4/4), at most 1 note per beat.
    """
    if not expert:
        return {
            "ExpertSingle": [],
            "HardSingle": [],
            "MediumSingle": [],
            "EasySingle": [],
        }

    bar_ticks = RESOLUTION * 4

    # Group expert by tick to strip HOPO and chord secondaries (keep root only)
    by_tick: Dict[int, List[ChartNote]] = {}
    for n in expert:
        if n.fret == 5 or n.fret == 6:
            continue
        by_tick.setdefault(n.tick, []).append(n)

    # Root note per unique tick (first note emitted / lowest fret)
    singles: List[ChartNote] = [notes[0] for _, notes in sorted(by_tick.items())]

    # Group by bar
    bars_singles: Dict[int, List[ChartNote]] = {}
    for n in singles:
        bars_singles.setdefault(n.tick // bar_ticks, []).append(n)

    # HardSingle: keep every 2 notes per bar (stride 2)
    hard: List[ChartNote] = []
    for b in sorted(bars_singles):
        bar_notes = bars_singles[b]
        hard.extend(bar_notes[::2])

    # MediumSingle: strip opens (fret 7), keep 1 in 3 notes per bar (stride 3).
    # Clamp frets to 0..3 (Green, Red, Yellow, Blue only — no Orange on Medium).
    med: List[ChartNote] = []
    for b in sorted(bars_singles):
        bar_notes = [n for n in bars_singles[b] if n.fret != 7]
        for n in bar_notes[::3]:
            med.append(ChartNote(tick=n.tick, fret=min(3, n.fret), length=n.length))

    # EasySingle: strip opens, only beat 1 and 3 of each bar (beats 0 and 2 in 4/4).
    # Clamp frets to 0..2 (Green, Red, Yellow only — no Blue/Orange on Easy).
    easy: List[ChartNote] = []
    for b in sorted(bars_singles):
        bar_notes = [n for n in bars_singles[b] if n.fret != 7]
        seen_beats = set()
        for n in bar_notes:
            tib = n.tick % bar_ticks
            beat = tib // RESOLUTION
            if beat in (0, 2) and beat not in seen_beats:
                seen_beats.add(beat)
                easy.append(ChartNote(tick=n.tick, fret=min(2, n.fret), length=n.length))

    hard = _clean_sustains(hard)
    med = _clean_sustains(med)
    easy = _clean_sustains(easy)

    return {
        "ExpertSingle": list(expert),
        "HardSingle": hard,
        "MediumSingle": med,
        "EasySingle": easy,
    }


def build_chart(a: Analysis, drums: Optional[DrumEvents] = None,
                lyrics: Optional[LyricsData] = None,
                genre_profile: str = "default") -> ChartData:
    """
    v1.2: emits a MULTI-TEMPO SyncTrack — one B marker per detected beat (with
    adjacent markers collapsed when BPM drift is < 0.5). Keeps Clone Hero's
    playback clock locked to the real audio timeline instead of a single average.
    """
    bpm_milli = int(round(a.bpm * 1000))
    cd = ChartData(bpm_milli=bpm_milli)
    cd.sync.append((0, "TS", 4))

    beats = list(a.beats)
    if len(beats) < 2:
        cd.sync.append((0, "B", bpm_milli))
    else:
        # Check tempo stability: for studio-recorded tracks (trap, hip-hop, pop, rock),
        # tempo is fixed to a DAW grid/metronome. Discrete frame quantization (~23ms)
        # creates artificial micro-jitter between frames that flutters the highway speed
        # if emitted as per-beat tempo changes.
        beat_durs = [float(beats[i + 1]) - float(beats[i]) for i in range(len(beats) - 1)]
        durs = [d for d in beat_durs if d > 1e-6]
        dur_std = float(np.std(durs)) if len(durs) > 1 else 0.0

        if dur_std < 0.035:
            # Steady metronomic track: single rock-solid BPM, silky smooth highway
            cd.sync.append((0, "B", bpm_milli))
            bar_ticks = RESOLUTION * 4
            for b in range(1, 8):
                cd.sync.append((b * bar_ticks, "B", bpm_milli))
        else:
            # Genuine live tempo drift: filter micro-jitter and emit only significant shifts (>= 2.0 BPM)
            last_emitted_bpm = -1.0
            for i in range(len(beats) - 1):
                beat_dur = float(beats[i + 1]) - float(beats[i])
                if beat_dur <= 1e-6:
                    continue
                local_bpm = 60.0 / beat_dur
                local_bpm = max(40.0, min(300.0, local_bpm))
                if last_emitted_bpm > 0 and abs(local_bpm - last_emitted_bpm) < 2.0:
                    continue
                tick = i * RESOLUTION
                cd.sync.append((tick, "B", int(round(local_bpm * 1000))))
                last_emitted_bpm = local_bpm
            if not any(s[1] == "B" for s in cd.sync):
                cd.sync.append((0, "B", bpm_milli))

    cd.events = _events(a, lyrics)
    cd.lyrics_events = _lyrics_events(a, lyrics)
    cd.expert_single = _guitar_notes(a, events=cd.events, genre_profile=genre_profile)
    down = gen_downcharts(cd.expert_single)
    cd.hard_single = down.get("HardSingle", [])
    cd.medium_single = down.get("MediumSingle", [])
    cd.easy_single = down.get("EasySingle", [])
    if drums is not None:
        cd.expert_drums = _drum_notes(a, drums)
    sp = _star_power(a, genre_profile=genre_profile)
    cd.sp_guitar = sp
    cd.sp_drums = list(sp)
    return cd
