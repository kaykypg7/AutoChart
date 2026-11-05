"""
Heuristic drum transcriber (v1).

Band-filters the audio and runs independent onset detection on each band:
    low-pass (<120Hz)   -> kick
    band 120-250Hz      -> snare body
    band 2500-6000Hz    -> snare/hat crackle (resolves snare vs hat with centroid)
    high-pass (>6000Hz) -> hat/cymbal

v2 upgrade: swap the heuristic for ADTOF (TensorFlow CNN) by implementing the
same transcribe() signature. See core/README-adtof.md for the port notes.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import numpy as np


SR = 22050
HOP = 512


@dataclass
class DrumEvents:
    kicks: List[float] = field(default_factory=list)       # seconds
    snares: List[float] = field(default_factory=list)
    hats: List[float] = field(default_factory=list)
    toms: List[float] = field(default_factory=list)
    cymbals: List[float] = field(default_factory=list)


def _band_onsets(y: np.ndarray, sr: int, low: float, high: float, delta: float = 0.3) -> np.ndarray:
    import librosa
    from scipy.signal import butter, sosfiltfilt
    ny = sr / 2.0
    lo, hi = max(low / ny, 0.001), min(high / ny, 0.999)
    sos = butter(4, [lo, hi], btype="band", output="sos")
    yb = sosfiltfilt(sos, y)
    env = librosa.onset.onset_strength(y=yb, sr=sr, hop_length=HOP)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP,
                                        pre_max=4, post_max=4, pre_avg=8, post_avg=8,
                                        delta=delta, wait=2)
    return librosa.frames_to_time(frames, sr=sr, hop_length=HOP)


def transcribe(audio_path: Path) -> DrumEvents:
    import librosa
    y, sr = librosa.load(str(audio_path), sr=SR, mono=True)

    kicks = _band_onsets(y, sr, 20, 120, delta=0.35)
    low_mid = _band_onsets(y, sr, 150, 400, delta=0.30)
    high_mid = _band_onsets(y, sr, 2000, 6000, delta=0.25)
    highs = _band_onsets(y, sr, 7000, 10000, delta=0.20)

    def _dedupe(a: np.ndarray, b: np.ndarray, min_gap: float = 0.03) -> np.ndarray:
        if len(b) == 0:
            return a
        if len(a) == 0:
            return a
        keep = []
        for t in a:
            if not np.any(np.abs(b - t) < min_gap):
                keep.append(t)
        return np.array(keep)

    snares = _dedupe(low_mid, kicks, 0.04)
    hats = _dedupe(highs, snares, 0.03)
    cymbals = _dedupe(high_mid, snares, 0.03)
    cymbals = _dedupe(cymbals, hats, 0.05)

    return DrumEvents(
        kicks=sorted(float(t) for t in kicks),
        snares=sorted(float(t) for t in snares),
        hats=sorted(float(t) for t in hats),
        toms=[],
        cymbals=sorted(float(t) for t in cymbals),
    )
