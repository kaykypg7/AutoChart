"""
Beat-tracker spike: librosa vs madmom on R.I.P. (Playboi Carti).

Hypothesis: librosa.beat.beat_track reports ~143.56 BPM for R.I.P. but drifts
during trap sections. madmom's RNN+DBN pipeline is known to track trap
tempi more faithfully. This script quantifies both.

Metric: mean distance (ms) from each percussive onset to its nearest beat
in each tracker's output. Lower = better local sync.

Output: scripts/compare_beat_trackers.json alongside this file, and a
summary printed to stdout.

If madmom is not installed, the script still runs the librosa half and
records why madmom is unavailable, so the baker has a signal either way.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

SONG = Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs/Playboi Carti - Playboi Carti - R.I.P. (auto)/song.ogg")
OUT = Path(__file__).parent / "compare_beat_trackers.json"


def _nearest_distance_ms(onsets, beats):
    """For each onset, find the nearest beat; return (mean, median, p90) in ms."""
    if len(onsets) == 0 or len(beats) == 0:
        return {"mean_ms": None, "median_ms": None, "p90_ms": None}
    beats_sorted = np.sort(np.asarray(beats, dtype=float))
    dists = []
    for t in onsets:
        idx = np.searchsorted(beats_sorted, t)
        candidates = []
        if idx > 0:
            candidates.append(abs(t - beats_sorted[idx - 1]))
        if idx < len(beats_sorted):
            candidates.append(abs(t - beats_sorted[idx]))
        dists.append(min(candidates))
    dists_ms = np.asarray(dists) * 1000.0
    return {
        "mean_ms": float(np.mean(dists_ms)),
        "median_ms": float(np.median(dists_ms)),
        "p90_ms": float(np.percentile(dists_ms, 90)),
    }


def run_librosa(y, sr):
    import librosa
    t0 = time.monotonic()
    tempo, beats_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats_t = librosa.frames_to_time(beats_frames, sr=sr)
    dur = time.monotonic() - t0
    # tempo on librosa 1.0 is a 1-element ndarray
    bpm = float(np.asarray(tempo).ravel()[0])
    return {
        "bpm": bpm,
        "beats": [float(b) for b in beats_t],
        "duration_s": dur,
    }


def run_madmom(audio_path: Path):
    try:
        from madmom.features.beats import RNNBeatProcessor, DBNBeatTrackingProcessor
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "available": False}

    t0 = time.monotonic()
    try:
        act = RNNBeatProcessor()(str(audio_path))
        # fps default on madmom RNN beat is 100 Hz; DBN runs on that activation.
        proc = DBNBeatTrackingProcessor(min_bpm=55, max_bpm=215, fps=100)
        beats_t = proc(act)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "available": False,
                "stage": "inference"}
    dur = time.monotonic() - t0

    beats_arr = np.asarray(beats_t, dtype=float)
    if len(beats_arr) >= 2:
        bpm = float(60.0 / np.median(np.diff(beats_arr)))
    else:
        bpm = 0.0
    return {
        "bpm": bpm,
        "beats": [float(b) for b in beats_arr],
        "duration_s": dur,
        "available": True,
    }


def main() -> int:
    if not SONG.exists():
        print(f"ERROR: song not found: {SONG}", file=sys.stderr)
        return 2

    import librosa
    print(f"loading {SONG.name} ...")
    y, sr = librosa.load(str(SONG), sr=22050, mono=True)
    print(f"loaded: {len(y)/sr:.1f}s @ {sr} Hz")

    # Reference onsets: percussive component -> onset_detect, in seconds.
    print("extracting percussive onsets (reference set) ...")
    y_h, y_p = librosa.effects.hpss(y)
    onset_frames = librosa.onset.onset_detect(y=y_p, sr=sr, backtrack=True)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    print(f"  {len(onset_times)} percussive onsets")

    print("running librosa beat tracker ...")
    lib = run_librosa(y, sr)
    print(f"  librosa: bpm={lib['bpm']:.2f}, {len(lib['beats'])} beats, {lib['duration_s']:.2f}s")

    print("running madmom beat tracker ...")
    mad = run_madmom(SONG)
    if mad.get("available"):
        print(f"  madmom:  bpm={mad['bpm']:.2f}, {len(mad['beats'])} beats, {mad['duration_s']:.2f}s")
    else:
        print(f"  madmom:  UNAVAILABLE — {mad.get('error')}")

    lib_align = _nearest_distance_ms(onset_times, lib["beats"])
    mad_align = _nearest_distance_ms(onset_times, mad.get("beats", [])) if mad.get("available") else {
        "mean_ms": None, "median_ms": None, "p90_ms": None,
    }

    out = {
        "song": str(SONG),
        "n_onsets": int(len(onset_times)),
        "librosa": {**lib, "align": lib_align},
        "madmom": {**mad, "align": mad_align},
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")
    print("\nALIGNMENT (lower = better sync to percussive onsets):")
    print(f"  librosa mean={lib_align['mean_ms']}  median={lib_align['median_ms']}  p90={lib_align['p90_ms']}")
    print(f"  madmom  mean={mad_align['mean_ms']}  median={mad_align['median_ms']}  p90={mad_align['p90_ms']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
