"""
Phase 13 — multi-genre validation on Arctic Monkeys.

Rock genre test for the charter. Pipeline:
  1. Parse 5 hand-charted Arctic MIDI (notes.mid) into Archivist's ParsedChart
     structure, aggregate into Arctic-specific ReferencePriors (never written
     to disk — stays in-memory so Carti priors remain untouched).
  2. Resolve + autochart the same 5 songs via the full project pipeline.
  3. Compare each generated chart against the Arctic priors (not Carti's),
     report chord_rate / fret_L1 / hopo_rate aggregates.

Why MIDI not .chart: all Arctic charts on disk are notes.mid (Rock Band
format), not notes.chart. The parser below extracts just the fields
compute_metrics needs — resolution, bpm_events, note_events, force_events,
sp_phrases, end_tick — and feeds them straight into Archivist's existing
compute_metrics + aggregate_priors functions.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import mido  # noqa: E402

from core.analyzer import analyze_audio               # noqa: E402
from core.chart_compare import compare_chart_vs_priors   # noqa: E402
from core.charter import build_chart                     # noqa: E402
from core.chart_writer import ChartMeta, write_chart     # noqa: E402
from core.config import Settings                          # noqa: E402
from core.pipeline import Job, Pipeline                   # noqa: E402
from core.reference import (                              # noqa: E402
    BPMEvent, ParsedChart, aggregate_priors, compute_metrics,
)
from core.search import yt_search                         # noqa: E402

SONGS_ROOT = Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs")
OUT = PROJ / "tmp" / "arctic_v2"


@dataclass
class ArcticTrack:
    name: str
    folder: str          # relative to SONGS_ROOT, has notes.mid
    search_query: str    # for yt-dlp


TRACKS: List[ArcticTrack] = [
    ArcticTrack("505", "Arctic Monkeys - 505",
                "Arctic Monkeys 505"),
    ArcticTrack("Do I Wanna Know", "Arctic Monkeys - Do I Wanna Know_",
                "Arctic Monkeys Do I Wanna Know"),
    ArcticTrack("Brianstorm", "Arctic Monkeys - Brianstorm",
                "Arctic Monkeys Brianstorm"),
    ArcticTrack("Reckless Serenade", "Arctic Monkeys - Reckless Serenade",
                "Arctic Monkeys Reckless Serenade"),
    ArcticTrack("Why'd You Only Call Me",
                "Arctic Monkeys - Why'd You Only Call Me When You're High_",
                "Arctic Monkeys Why'd You Only Call Me When You're High"),
]


# ---------- MIDI → ParsedChart ---------------------------------------------

# Clone Hero / Rock Band MIDI pitch map on PART GUITAR, Expert difficulty.
EXPERT_FRET_PITCHES = {96: 0, 97: 1, 98: 2, 99: 3, 100: 4}
EXPERT_FORCE_HOPO = 102     # the "force HOPO" overlay note
EXPERT_FORCE_STRUM = 101    # force-strum overlay — not a note either
EXPERT_OPEN_SYSEX_PITCH = None  # open notes are sysex, not pitch — skip
STAR_POWER_PITCH = 116       # overlapping = SP phrase span


def parse_midi_as_chart(path: Path) -> ParsedChart:
    """Minimal MIDI → ParsedChart: enough to feed compute_metrics."""
    mid = mido.MidiFile(str(path))
    res = mid.ticks_per_beat  # typically 480 for RB MIDI

    # Collect BPM events from the first track (standard RB layout: tempo
    # map lives on the "notes" / tempo track at track index 0).
    bpm_events: List[BPMEvent] = []
    for track in mid.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.type == "set_tempo":
                bpm = round(60_000_000.0 / msg.tempo, 3)
                bpm_events.append(BPMEvent(tick=t, bpm=bpm))
        if bpm_events:
            break
    if not bpm_events:
        bpm_events.append(BPMEvent(tick=0, bpm=120.0))
    bpm_events.sort(key=lambda e: e.tick)

    # Find PART GUITAR.
    guitar = next((t for t in mid.tracks if (t.name or "").strip() == "PART GUITAR"), None)
    if guitar is None:
        return ParsedChart(
            resolution=res, bpm_events=bpm_events, note_events=[],
            force_events=[], sp_phrases=[], end_tick=0, track="",
        )

    # Walk the guitar track with absolute tick timing. Track note_on until
    # the matching note_off so we recover sustain length and SP spans.
    note_events: List[Tuple[int, int, int]] = []  # (tick, fret, sustain)
    force_events: List[int] = []
    sp_phrases: List[Tuple[int, int]] = []
    open_notes: Dict[int, int] = {}  # pitch → start_tick
    end_tick = 0
    t = 0
    for msg in guitar:
        t += msg.time
        end_tick = max(end_tick, t)
        is_on = msg.type == "note_on" and getattr(msg, "velocity", 0) > 0
        is_off = (msg.type == "note_off") or (msg.type == "note_on" and getattr(msg, "velocity", 0) == 0)

        if is_on:
            open_notes[msg.note] = t
        elif is_off and msg.note in open_notes:
            start = open_notes.pop(msg.note)
            dur = max(0, t - start)
            pitch = msg.note
            if pitch in EXPERT_FRET_PITCHES:
                note_events.append((start, EXPERT_FRET_PITCHES[pitch], dur))
            elif pitch == EXPERT_FORCE_HOPO:
                force_events.append(start)
            elif pitch == STAR_POWER_PITCH:
                sp_phrases.append((start, dur))
            # pitch 101 (force strum) and anything else — intentionally dropped

    note_events.sort(key=lambda n: n[0])
    force_events.sort()
    sp_phrases.sort()
    for tick, _, sustain in note_events:
        end_tick = max(end_tick, tick + sustain)

    return ParsedChart(
        resolution=res,
        bpm_events=bpm_events,
        note_events=note_events,
        force_events=force_events,
        sp_phrases=sp_phrases,
        end_tick=end_tick,
        track="PART GUITAR",
    )


# ---------- phase 13 driver -------------------------------------------------

def build_arctic_priors():
    print("=" * 60)
    print("ARCTIC PRIORS (from hand-charted MIDI)")
    print("=" * 60)
    metrics = []
    for tr in TRACKS:
        mid_path = SONGS_ROOT / tr.folder / "notes.mid"
        if not mid_path.exists():
            print(f"  [skip] {tr.name}: no notes.mid at {mid_path}")
            continue
        parsed = parse_midi_as_chart(mid_path)
        if not parsed.note_events:
            print(f"  [skip] {tr.name}: no PART GUITAR notes")
            continue
        m = compute_metrics(tr.name, parsed)
        metrics.append(m)
        print(f"  {tr.name:28s}  bpm={m.bpm_avg:6.2f}  dur={m.duration_s:7.1f}s  "
              f"groups={m.chord_group_count:4d}  chord%={m.chord_rate*100:5.2f}  "
              f"hopo%={m.hopo_rate*100:5.2f}  sp/min={m.sp_phrases_per_min:5.2f}")

    priors = aggregate_priors(metrics)
    print("\nArctic priors aggregate:")
    print(f"  bpm_avg               {priors.corpus_bpm_avg}")
    print(f"  median_notes_per_bar  {priors.median_notes_per_bar}")
    print(f"  notes_per_bar_norm    {priors.notes_per_bar_norm_bpm}")
    print(f"  chord_rate            {priors.chord_rate}")
    print(f"  hopo_rate             {priors.hopo_rate}")
    print(f"  sp_phrases_per_min    {priors.sp_phrases_per_min}")
    print(f"  fret_distribution     {priors.fret_distribution}")
    return priors


def run_autochart_vs_arctic(priors):
    print("\n" + "=" * 60)
    print("AUTOCHART vs ARCTIC PRIORS")
    print("=" * 60)
    OUT.mkdir(parents=True, exist_ok=True)
    settings = Settings.load()
    settings.output_folder = str(OUT)
    settings.folder_suffix = " (auto)"
    settings.genre_profile = "rock"   # phase 14 — force v2.0 rock profile
    print(f"\ngenre_profile forced to: {settings.genre_profile!r}")
    pipeline = Pipeline(settings)

    rows: List[dict] = []
    t_total = time.monotonic()
    for i, tr in enumerate(TRACKS, 1):
        label = f"Arctic Monkeys - {tr.name}"
        print(f"\n[{i}/{len(TRACKS)}] {label}")
        t0 = time.monotonic()
        cached_folder = OUT / f"Arctic Monkeys - {tr.name} (auto)"
        if (cached_folder / "song.ogg").exists():
            print(f"  -> using cached audio in {cached_folder.name}")
            manual_bpm = 165.0 if tr.name == "Brianstorm" else None
            analysis = analyze_audio(cached_folder / "song.ogg", manual_bpm=manual_bpm)
            cd = build_chart(analysis, drums=None, lyrics=None, genre_profile="rock")
            cmeta = ChartMeta(name=tr.name, artist="Arctic Monkeys", difficulty=3)
            chart_path = cached_folder / "notes.chart"
            write_chart(chart_path, cd, cmeta)
            stage = "done"
        else:
            try:
                results = yt_search(tr.search_query, limit=1,
                                    browser_cookies=settings.browser_cookies)
            except Exception as e:
                print(f"  SEARCH FAILED: {e}")
                rows.append({"name": tr.name, "stage": "search_failed", "error": str(e)})
                continue
            if not results or not results[0].url:
                rows.append({"name": tr.name, "stage": "no_result"})
                continue
            print(f"  -> {results[0].title}  {results[0].url}")
            job = Job(url=results[0].url, title_hint=tr.name,
                      artist_hint="Arctic Monkeys")
            result = pipeline.run_job(job, lambda p: None)
            stage = result.stage
            if stage == "failed":
                elapsed = time.monotonic() - t0
                print(f"  FAILED {elapsed:.1f}s: {result.error}")
                rows.append({"name": tr.name, "stage": stage, "elapsed_s": round(elapsed, 1), "error": result.error})
                continue
            chart_path = (result.folder or Path()) / "notes.chart"
            if not chart_path.exists():
                elapsed = time.monotonic() - t0
                rows.append({"name": tr.name, "stage": stage, "elapsed_s": round(elapsed, 1), "error": "no notes.chart"})
                continue

        elapsed = time.monotonic() - t0
        row = {"name": tr.name, "stage": stage, "elapsed_s": round(elapsed, 1)}
        try:
            delta = compare_chart_vs_priors(chart_path, priors=priors)
        except Exception as e:
            row["error"] = f"compare: {e}"
            rows.append(row)
            continue
        in_band = {k: bool(v["in_band"]) for k, v in delta.scalars.items()}
        row.update({
            "bpm": delta.bpm_avg,
            "chord_rate": delta.scalars["chord_rate"]["candidate"],
            "hopo_rate": delta.scalars["hopo_rate"]["candidate"],
            "fret_l1": delta.fret_l1_distance,
            "interval_l1": delta.interval_l1_distance,
            "in_band": in_band,
            "in_band_count": sum(in_band.values()),
            "passed": sum(in_band.values()) >= 3,
        })
        print(f"  OK {elapsed:.1f}s  {delta.summary}")
        print(f"     in-band {row['in_band_count']}/5  -> {'PASS' if row['passed'] else 'FAIL'}")
        rows.append(row)

    scored = [r for r in rows if "chord_rate" in r]
    agg = {
        "runs": len(rows),
        "scored": len(scored),
        "passed": sum(1 for r in scored if r.get("passed")),
        "elapsed_total_s": round(time.monotonic() - t_total, 1),
    }
    if scored:
        agg["chord_rate_mean"] = round(sum(r["chord_rate"] for r in scored) / len(scored), 4)
        agg["hopo_rate_mean"]  = round(sum(r["hopo_rate"]  for r in scored) / len(scored), 4)
        agg["fret_l1_mean"]    = round(sum(r["fret_l1"]    for r in scored) / len(scored), 4)
        agg["interval_l1_mean"]= round(sum(r["interval_l1"] for r in scored) / len(scored), 4)

    print("\n" + "=" * 60)
    print(f"AGGREGATE: {agg.get('passed',0)}/{agg.get('runs',0)} passed")
    if "chord_rate_mean" in agg:
        print(f"  chord_rate mean  = {agg['chord_rate_mean']:.4f}  (prior {priors.chord_rate:.4f})")
        print(f"  hopo_rate mean   = {agg['hopo_rate_mean']:.4f}  (prior {priors.hopo_rate:.4f})")
        print(f"  fret_L1 mean     = {agg['fret_l1_mean']:.4f}")
        print(f"  interval_L1 mean = {agg['interval_l1_mean']:.4f}")

    (OUT / "arctic_report.json").write_text(
        json.dumps({"priors": {
            "chord_rate": priors.chord_rate,
            "hopo_rate": priors.hopo_rate,
            "median_notes_per_bar": priors.median_notes_per_bar,
            "fret_distribution": priors.fret_distribution,
        }, "tracks": rows, "aggregate": agg}, indent=2), encoding="utf-8")
    print(f"\n  report: {OUT / 'arctic_report.json'}")
    return agg


def main() -> int:
    priors = build_arctic_priors()
    agg = run_autochart_vs_arctic(priors)
    return 0 if agg.get("scored", 0) else 1


if __name__ == "__main__":
    sys.exit(main())
