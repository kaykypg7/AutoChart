"""
Multi-song validation: run the full v1.5+lyrics pipeline on 5 Playboi Carti
tracks, score each generated chart against Archivist's reference priors,
report an aggregated table.

Pass criterion:
  A chart "passes" when >= 3 of 5 scalar metrics land inside the per-chart
  observed band from the reference set:
    median_notes_per_bar, notes_per_bar_norm_bpm, chord_rate,
    hopo_rate, sp_phrases_per_min
  3-of-5 is permissive on purpose — the priors are a shape target, not a
  spec; a chart that nails chord-rate + fret-L1 but misses sp-phrases is
  still playable.

Output:
  tmp/validate_v15/<folder-per-track>/   (full chart artifacts)
  tmp/validate_v15/validate_report.json  (per-track scores + aggregate)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from core.chart_compare import compare_chart_vs_priors   # noqa: E402
from core.config import Settings                          # noqa: E402
from core import lyrics as _lyrics_mod                    # noqa: E402
from core.pipeline import Job, Pipeline                   # noqa: E402
from core.search import yt_search                         # noqa: E402

OUT_ROOT = PROJ / "tmp" / "validate_v15"
TRACKS: List[Dict[str, str]] = [
    {"artist": "Playboi Carti", "title": "EVILJ0RDAN",
     "query": "Playboi Carti EVILJ0RDAN"},
    {"artist": "Playboi Carti", "title": "Timeless",
     "query": "Playboi Carti Timeless The Weeknd"},
    {"artist": "Playboi Carti", "title": "Rockstar Made",
     "query": "Playboi Carti Rockstar Made"},
    {"artist": "Playboi Carti", "title": "Fell In Luv",
     "query": "Playboi Carti Fell In Luv Bryson Tiller"},
    {"artist": "Playboi Carti", "title": "Shoota",
     "query": "Playboi Carti Shoota Lil Uzi Vert"},
]


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    settings = Settings.load()
    settings.output_folder = str(OUT_ROOT)
    settings.folder_suffix = " (auto)"
    pipeline = Pipeline(settings)

    rows: List[dict] = []
    t_total = time.monotonic()

    for i, tr in enumerate(TRACKS, 1):
        label = f"{tr['artist']} - {tr['title']}"
        print(f"\n[{i}/{len(TRACKS)}] {label}")
        t0 = time.monotonic()

        # Resolve the search query to a real video URL first — yt-dlp's
        # ytsearch1 prefix returns a search-page info whose `id` doesn't
        # match the downloaded file name, breaking downloader.py's lookup.
        try:
            results = yt_search(tr["query"], limit=1,
                                browser_cookies=settings.browser_cookies)
        except Exception as e:
            print(f"  SEARCH FAILED: {type(e).__name__}: {e}")
            rows.append({"track": label, "stage": "failed",
                         "error": f"search: {e}", "elapsed_s": round(time.monotonic() - t0, 1)})
            continue
        if not results or not results[0].url:
            print(f"  NO RESULT for query: {tr['query']}")
            rows.append({"track": label, "stage": "failed",
                         "error": "no search result",
                         "elapsed_s": round(time.monotonic() - t0, 1)})
            continue
        video_url = results[0].url
        print(f"  resolved: {results[0].title} -> {video_url}")

        job = Job(url=video_url, title_hint=tr["title"], artist_hint=tr["artist"])
        last: Dict[str, str] = {}

        def _progress(p):
            last["stage"] = p.stage
            last["msg"] = p.message

        result = pipeline.run_job(job, _progress)
        elapsed = time.monotonic() - t0

        row: dict = {
            "track": label,
            "stage": result.stage,
            "elapsed_s": round(elapsed, 1),
        }
        if result.stage == "failed":
            row["error"] = result.error or last.get("msg", "unknown")
            print(f"  FAILED after {elapsed:.1f}s: {row['error']}")
            rows.append(row)
            continue

        chart_path = (result.folder or Path()) / "notes.chart"
        row["folder"] = str(result.folder) if result.folder else None
        row["chart_path"] = str(chart_path)
        if not chart_path.exists():
            row["error"] = f"notes.chart missing after done: {chart_path}"
            print(f"  WEIRD: {row['error']}")
            rows.append(row)
            continue

        try:
            delta = compare_chart_vs_priors(chart_path)
        except Exception as e:
            row["error"] = f"compare failed: {type(e).__name__}: {e}"
            print(f"  COMPARE FAILED: {row['error']}")
            rows.append(row)
            continue

        in_band = {k: bool(v["in_band"]) for k, v in delta.scalars.items()}
        row["bpm"] = delta.bpm_avg
        row["chord_rate"] = delta.scalars["chord_rate"]["candidate"]
        row["hopo_rate"] = delta.scalars["hopo_rate"]["candidate"]
        row["median_notes_per_bar"] = delta.scalars["median_notes_per_bar"]["candidate"]
        row["notes_per_bar_norm_bpm"] = delta.scalars["notes_per_bar_norm_bpm"]["candidate"]
        row["sp_phrases_per_min"] = delta.scalars["sp_phrases_per_min"]["candidate"]
        row["fret_l1"] = delta.fret_l1_distance
        row["interval_l1"] = delta.interval_l1_distance
        row["in_band"] = in_band
        row["in_band_count"] = sum(in_band.values())
        row["passed"] = row["in_band_count"] >= 3

        print(f"  OK  {elapsed:.1f}s  {delta.summary}")
        print(f"       in-band: {row['in_band_count']}/5   -> {'PASS' if row['passed'] else 'FAIL'}")
        rows.append(row)

    total = time.monotonic() - t_total
    scored = [r for r in rows if "chord_rate" in r]
    passed = [r for r in scored if r.get("passed")]
    agg = {
        "runs":   len(rows),
        "scored": len(scored),
        "passed": len(passed),
        "elapsed_total_s": round(total, 1),
    }
    if scored:
        agg["chord_rate_mean"] = round(sum(r["chord_rate"] for r in scored) / len(scored), 4)
        agg["fret_l1_mean"]    = round(sum(r["fret_l1"]    for r in scored) / len(scored), 4)
        agg["hopo_rate_mean"]  = round(sum(r["hopo_rate"]  for r in scored) / len(scored), 4)

    report = {"tracks": rows, "aggregate": agg}
    (OUT_ROOT / "validate_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    stats = dict(_lyrics_mod.LYRICS_STATS)
    total_lookups = stats["cache_hits"] + stats["cache_misses"]
    hit_rate = (stats["cache_hits"] / total_lookups * 100.0) if total_lookups else 0.0
    agg["lyrics_cache_hits"] = stats["cache_hits"]
    agg["lyrics_cache_misses"] = stats["cache_misses"]
    agg["lyrics_cache_hit_rate_pct"] = round(hit_rate, 1)

    print("\n" + "=" * 60)
    print(f"AGGREGATE: {agg.get('passed', 0)}/{agg.get('runs', 0)} passed "
          f"({agg.get('scored', 0)} scored)")
    print(f"  lyrics cache: {stats['cache_hits']} hits / "
          f"{stats['cache_misses']} misses  ({hit_rate:.1f}% hit rate)")
    if "chord_rate_mean" in agg:
        print(f"  chord_rate mean: {agg['chord_rate_mean']:.4f}  "
              f"(prior {0.268:.3f})")
        print(f"  fret_L1 mean:    {agg['fret_l1_mean']:.4f}  "
              f"(0 = identical shape)")
        print(f"  hopo_rate mean:  {agg['hopo_rate_mean']:.4f}  "
              f"(prior 0.000)")
    print(f"  elapsed: {total:.1f}s")
    print(f"  report:  {OUT_ROOT / 'validate_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
