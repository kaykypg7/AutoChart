"""
Grid search over librosa.beat.beat_track(tightness, start_bpm) for R.I.P.

Baseline from phase 5: default params give 116 ms mean onset-to-beat distance.
Goal: find a (tightness, start_bpm) pair that drops that number materially.

If the best combo beats 80 ms we promote it to the default in
core/analyzer.py (caller's decision; this script only recommends).
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

SONG = Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs/Playboi Carti - Playboi Carti - R.I.P. (auto)/song.ogg")
OUT = Path(__file__).parent / "tune_beat_tracker.json"
BASELINE_MS = 116.46

TIGHTNESS_GRID = [50, 100, 200, 400]       # librosa default = 100
START_BPM_GRID = [None, 70, 140, 170]      # None = let librosa auto-pick


def _nearest_distance_ms(onsets, beats):
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
    ms = np.asarray(dists) * 1000.0
    return {
        "mean_ms": float(np.mean(ms)),
        "median_ms": float(np.median(ms)),
        "p90_ms": float(np.percentile(ms, 90)),
    }


def main() -> int:
    if not SONG.exists():
        print(f"ERROR: song not found: {SONG}", file=sys.stderr)
        return 2

    import librosa
    print(f"loading {SONG.name} ...")
    y, sr = librosa.load(str(SONG), sr=22050, mono=True)
    print(f"loaded: {len(y)/sr:.1f}s @ {sr} Hz")

    # Reference set — same percussive-onset recipe as phase 5 so numbers are comparable.
    _, y_p = librosa.effects.hpss(y)
    onset_frames = librosa.onset.onset_detect(y=y_p, sr=sr, backtrack=True)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    print(f"reference set: {len(onset_times)} percussive onsets\n")

    results = []
    for t_val in TIGHTNESS_GRID:
        for s_val in START_BPM_GRID:
            kwargs = {"y": y, "sr": sr, "tightness": t_val}
            if s_val is not None:
                kwargs["start_bpm"] = s_val
            label = f"tightness={t_val:4d} start_bpm={str(s_val):>6}"
            t0 = time.monotonic()
            try:
                tempo, beat_frames = librosa.beat.beat_track(**kwargs, units="frames")
                beats_t = librosa.frames_to_time(beat_frames, sr=sr)
                dur = time.monotonic() - t0
                bpm = float(np.atleast_1d(tempo)[0])
                align = _nearest_distance_ms(onset_times, beats_t)
                row = {
                    "tightness": t_val,
                    "start_bpm": s_val,
                    "bpm": bpm,
                    "n_beats": int(len(beats_t)),
                    "elapsed_s": dur,
                    **align,
                }
                results.append(row)
                print(f"  {label}  bpm={bpm:6.2f} n={len(beats_t):3d} "
                      f"mean={align['mean_ms']:6.2f}ms  median={align['median_ms']:6.2f}  p90={align['p90_ms']:6.2f}  ({dur:.2f}s)")
            except Exception as e:
                print(f"  {label}  ERROR: {type(e).__name__}: {e}")
                results.append({
                    "tightness": t_val, "start_bpm": s_val,
                    "error": f"{type(e).__name__}: {e}",
                })

    # Rank by mean onset→beat ms, skip rows that errored.
    scored = [r for r in results if r.get("mean_ms") is not None]
    scored.sort(key=lambda r: r["mean_ms"])
    best = scored[0] if scored else None

    payload = {
        "song": str(SONG),
        "n_onsets": int(len(onset_times)),
        "baseline_mean_ms": BASELINE_MS,
        "grid": {
            "tightness": TIGHTNESS_GRID,
            "start_bpm": [str(s) if s is not None else None for s in START_BPM_GRID],
        },
        "results": results,
        "best": best,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")

    if best:
        improvement = (BASELINE_MS - best["mean_ms"]) / BASELINE_MS * 100.0
        print(f"\nBEST: tightness={best['tightness']} start_bpm={best['start_bpm']}  "
              f"mean={best['mean_ms']:.2f} ms  (baseline {BASELINE_MS:.2f} ms, "
              f"improvement {improvement:+.1f}%)")
        if best["mean_ms"] < 80.0:
            print("  -> beats the 80 ms threshold; recommend promoting to default.")
        else:
            print("  -> above 80 ms threshold; keep librosa defaults, pivot further.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
