from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from core.errors import AnalysisError


SR = 22050
HOP = 512


@dataclass
class ChordEvent:
    time_s: float
    root: int                          # 0..11 (0=C, 1=C#, ..., 11=B)
    kind: str                          # "5", "maj", "min", "sus"
    confidence: float


@dataclass
class Analysis:
    duration_s: float
    bpm: float
    beats: np.ndarray                 # seconds
    downbeats: np.ndarray             # seconds, estimated bar starts (every 4 beats)
    onsets: np.ndarray                # seconds (harmonic onsets, for guitar)
    onset_strength: np.ndarray        # per harmonic onset
    perc_onsets: np.ndarray           # seconds (percussive onsets, for drum accents)
    chroma: np.ndarray                # [12, frames] from harmonic component
    pitch_hz: np.ndarray              # [frames] dominant pitch Hz from harmonic
    rms: np.ndarray                   # [frames]
    centroid: np.ndarray              # [frames]
    sections: List[tuple] = field(default_factory=list)
    chords: List[ChordEvent] = field(default_factory=list)
    key: str = ""


def analyze_audio(path: Path, manual_bpm: Optional[float] = None,
                  guitar_stem_path: Optional[Path] = None) -> Analysis:
    """Analyze a mixed audio file.

    When guitar_stem_path is provided (from Demucs pre-separation), the guitar
    stem drives onset detection and pitch tracking — i.e. the chart's note
    ticks and fret contour follow the lead guitar line, not whatever is loudest
    in the full mix. Beats, tempo, RMS, and section boundaries still come from
    the full signal (those are mix-level properties).
    """
    import librosa
    try:
        y, sr = librosa.load(str(path), sr=SR, mono=True)
    except Exception as e:
        raise AnalysisError(f"Could not load audio: {e}") from e
    if len(y) < SR * 5:
        raise AnalysisError("Audio too short (< 5s).")

    duration = librosa.get_duration(y=y, sr=sr)

    # v1.12: when a guitar stem is supplied, swap it in for the harmonic-side
    # work. The stem IS the harmonic content of just the guitar track — no HPSS
    # needed on it, and percussive content is already stripped. Fall back to
    # HPSS on the full mix when no stem is provided.
    y_guitar = None
    if guitar_stem_path is not None:
        try:
            y_g, _ = librosa.load(str(guitar_stem_path), sr=SR, mono=True)
            if len(y_g) >= SR * 2:
                y_guitar = y_g
        except Exception:
            y_guitar = None

    # Harmonic/percussive separation on the full mix — pitch work falls back
    # to y_h when no stem is available; percussive y_p always drives drums.
    y_h, y_p = librosa.effects.hpss(y, margin=3.0)
    y_pitch = y_guitar if y_guitar is not None else y_h

    if manual_bpm and manual_bpm > 0:
        tempo = float(manual_bpm)
        beat_dur = 60.0 / tempo
        beats = np.arange(0, duration, beat_dur)
    else:
        # Tempo + beats from the full signal (both components contribute).
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=HOP, trim=False)
        tempo = float(np.atleast_1d(tempo)[0]) or 120.0
        # Half-time / double-time trap sanity: push tempo into a playable band [70, 180].
        while tempo > 180: tempo /= 2
        while tempo < 70:  tempo *= 2
        beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP)
    # Downbeats: every 4th beat (naive but works for 4/4, which Carti is 99% of).
    downbeats = beats[::4] if len(beats) else beats

    # Onset detection:
    # - With a guitar stem: onsets come 100% from the stem's envelope. No
    #   full-mix blending — the mix contaminated the chart with vocal syllables
    #   and drum hits the listener never attributes to the guitar.
    # - Without a stem: fall back to the old 50/50 blend of full-signal and
    #   harmonic envelopes, which preserves backward behavior.
    if y_guitar is not None:
        combined_env = librosa.onset.onset_strength(
            y=y_guitar, sr=sr, hop_length=HOP, aggregate=np.median,
        )
    else:
        full_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP, aggregate=np.median)
        h_env = librosa.onset.onset_strength(y=y_h, sr=sr, hop_length=HOP, aggregate=np.median)
        combined_env = 0.5 * full_env + 0.5 * h_env
    h_frames = librosa.onset.onset_detect(
        onset_envelope=combined_env, sr=sr, hop_length=HOP,
        pre_max=3, post_max=3, pre_avg=6, post_avg=6, delta=0.12, wait=2,
    )
    onsets = librosa.frames_to_time(h_frames, sr=sr, hop_length=HOP)
    onset_strength = combined_env[h_frames] if len(h_frames) else np.array([])

    # v1.12: Boundary silence gate. Intro/outro padding and ambient tails
    # often produced a lone phantom note at the very start or end of the chart —
    # onset_detect fires on any faint transient and the RMS floor was low enough
    # to pass. Drop onsets whose frame RMS sits below the song's 15th percentile:
    # that cuts real-silence false triggers while keeping any onset that lives
    # inside the music proper (where even the quietest section runs above p15).
    full_rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    if len(full_rms) >= 10 and len(onsets):
        rms_floor = float(np.percentile(full_rms, 15))
        kept_idx = []
        for i, t in enumerate(onsets):
            fi = min(int(t * sr / HOP), len(full_rms) - 1)
            if full_rms[fi] >= rms_floor:
                kept_idx.append(i)
        onsets = onsets[kept_idx] if kept_idx else onsets[:0]
        onset_strength = onset_strength[kept_idx] if kept_idx else onset_strength[:0]

    # Percussive onsets drive drum accents on top of the beat template.
    p_env = librosa.onset.onset_strength(y=y_p, sr=sr, hop_length=HOP, aggregate=np.median)
    p_frames = librosa.onset.onset_detect(
        onset_envelope=p_env, sr=sr, hop_length=HOP,
        pre_max=3, post_max=3, pre_avg=6, post_avg=6, delta=0.18, wait=1,
    )
    perc_onsets = librosa.frames_to_time(p_frames, sr=sr, hop_length=HOP)

    # Pitch work on harmonic component.
    chroma = librosa.feature.chroma_cqt(y=y_h, sr=sr, hop_length=HOP)
    # Dominant pitch Hz per frame via YIN on harmonic (f0, not perfect, but monotonic).
    try:
        f0 = librosa.yin(y_h, fmin=65.0, fmax=1200.0, sr=sr, hop_length=HOP)
        f0 = np.nan_to_num(f0, nan=0.0)
    except Exception:
        f0 = np.zeros_like(chroma[0])

    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=HOP)[0]

    sections = _segment_sections(y, sr, duration)
    chords, key = _detect_chords(chroma, sr=sr, hop_length=HOP)

    return Analysis(
        duration_s=duration, bpm=tempo, beats=beats, downbeats=downbeats,
        onsets=onsets, onset_strength=onset_strength,
        perc_onsets=perc_onsets,
        chroma=chroma, pitch_hz=f0,
        rms=rms, centroid=centroid, sections=sections,
        chords=chords, key=key,
    )


def _segment_sections(y: np.ndarray, sr: int, duration: float) -> list:
    import librosa
    try:
        bounds_frames = librosa.segment.agglomerative(
            librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=13),
            k=min(6, max(2, int(duration / 30))),
        )
        bounds_s = librosa.frames_to_time(bounds_frames, sr=sr, hop_length=HOP)
    except Exception:
        return [(0.0, "intro")]
    labels = ["intro", "verse", "chorus", "verse", "chorus", "bridge", "outro"]
    out = []
    for i, t in enumerate(bounds_s):
        out.append((float(t), labels[min(i, len(labels) - 1)]))
    return out


def _detect_chords(chroma: np.ndarray, sr: int, hop_length: int) -> Tuple[List[ChordEvent], str]:
    """
    Extract chord events per frame and overall song key via native template matching.
    Zero external heavy dependencies. Fallback/safe for flat or synthetic inputs.
    """
    import librosa

    if chroma is None or chroma.shape[1] == 0:
        return [], ""

    pitch_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    chord_profiles = {
        "5": [0, 7],           # Power chord (root + fifth)
        "maj": [0, 4, 7],      # Major triad
        "min": [0, 3, 7],      # Minor triad
        "sus": [0, 5, 7],      # Suspended 4th
    }

    templates = []
    template_labels = []
    for root in range(12):
        for kind, intervals in chord_profiles.items():
            v = np.zeros(12, dtype=np.float32)
            for iv in intervals:
                v[(root + iv) % 12] = 1.0
            v_norm = np.linalg.norm(v)
            if v_norm > 0:
                v /= v_norm
            templates.append(v)
            template_labels.append((root, kind))
    T = np.array(templates, dtype=np.float32)  # (48, 12)

    # Check for flat or zero input
    c_max = float(chroma.max())
    c_min = float(chroma.min())
    if c_max <= 1e-6 or (c_max - c_min) <= 1e-6:
        return [], ""

    # Temporal smoothing (moving average across 3 frames)
    smoothed = chroma.astype(np.float32).copy()
    if smoothed.shape[1] >= 3:
        smoothed[:, 1:-1] = (smoothed[:, :-2] + smoothed[:, 1:-1] + smoothed[:, 2:]) / 3.0

    norms = np.linalg.norm(smoothed, axis=0, keepdims=True)
    norms[norms < 1e-4] = 1.0
    norm_chroma = smoothed / norms

    sims = T @ norm_chroma  # shape: (48, frames)
    best_idx = np.argmax(sims, axis=0)
    best_sims = np.max(sims, axis=0)

    frames = chroma.shape[1]
    times = librosa.frames_to_time(np.arange(frames), sr=sr, hop_length=hop_length)

    events: List[ChordEvent] = []
    for f in range(frames):
        r, k = template_labels[best_idx[f]]
        events.append(ChordEvent(
            time_s=float(times[f]),
            root=r,
            kind=k,
            confidence=float(best_sims[f]),
        ))

    # Key estimation via Krumhansl-Kessler profiles
    maj_prof = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    min_prof = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    maj_prof = (maj_prof - maj_prof.mean()) / maj_prof.std()
    min_prof = (min_prof - min_prof.mean()) / min_prof.std()

    chroma_sum = np.sum(chroma, axis=1)
    cs_std = float(chroma_sum.std())
    est_key = ""
    if cs_std > 1e-4:
        cs_norm = (chroma_sum - chroma_sum.mean()) / cs_std
        best_corr = -999.0
        for r in range(12):
            c_maj = float(np.corrcoef(cs_norm, np.roll(maj_prof, r))[0, 1])
            if c_maj > best_corr:
                best_corr = c_maj
                est_key = f"{pitch_names[r]}:maj"
            c_min = float(np.corrcoef(cs_norm, np.roll(min_prof, r))[0, 1])
            if c_min > best_corr:
                best_corr = c_min
                est_key = f"{pitch_names[r]}:min"

    return events, est_key
