"""Hand-charted reference parser → ReferencePriors.

Reads a small corpus of human-authored .chart files (notes.chart + song.ini)
and extracts statistical priors that CHART-ALGO consumes to calibrate its
output. The idea: whatever the humans do here is the ground truth distribution
the auto-chart should land inside.

Entrypoint:
    python -m core.reference         # rebuild core/reference_priors.json
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import stats


# ---------- raw parsing ------------------------------------------------------

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
EVENT_RE = re.compile(r"^\s*(\d+)\s*=\s*(.+?)\s*$")
NOTE_FRETS: Tuple[int, ...] = (0, 1, 2, 3, 4, 7)
FORCE_FRET: int = 5  # chart-format force-HOPO flag
TAP_FRET: int = 6    # tap flag (ignored for priors, just not counted as note)


@dataclass
class BPMEvent:
    tick: int
    bpm: float


@dataclass
class ParsedChart:
    resolution: int
    bpm_events: List[BPMEvent]
    note_events: List[Tuple[int, int, int]]       # (tick, fret, sustain)
    force_events: List[int]                       # ticks with N 5 (force HOPO)
    sp_phrases: List[Tuple[int, int]]             # (tick, length)
    end_tick: int                                 # last event tick seen
    track: str = ""                               # which [Expert*] section was used

    def ticks_to_seconds(self, tick: int) -> float:
        """Walk BPM segments to translate ticks → seconds."""
        if tick <= 0 or not self.bpm_events:
            return 0.0
        res = self.resolution
        seconds = 0.0
        for i, ev in enumerate(self.bpm_events):
            next_tick = self.bpm_events[i + 1].tick if i + 1 < len(self.bpm_events) else tick
            seg_end = min(next_tick, tick)
            if seg_end <= ev.tick:
                continue
            seconds += (seg_end - ev.tick) / res / ev.bpm * 60.0
            if next_tick >= tick:
                break
        return seconds

    def avg_bpm(self) -> float:
        """Time-weighted average BPM. Falls back to first event when the
        chart is too short to span multiple segments."""
        if not self.bpm_events:
            return 0.0
        if len(self.bpm_events) == 1 or self.end_tick <= self.bpm_events[0].tick:
            return self.bpm_events[0].bpm
        total_ticks = self.end_tick - self.bpm_events[0].tick
        weighted = 0.0
        for i, ev in enumerate(self.bpm_events):
            nxt = self.bpm_events[i + 1].tick if i + 1 < len(self.bpm_events) else self.end_tick
            weighted += ev.bpm * (nxt - ev.tick)
        return stats.safe_div(weighted, total_ticks, self.bpm_events[0].bpm)


DEFAULT_TRACK_PRIORITY: Tuple[str, ...] = (
    "ExpertSingle",         # lead guitar — what we actually want
    "ExpertDoubleGuitar",   # co-op lead
    "ExpertDoubleBass",     # bass — close-enough rhythmic structure
)


def parse_notes_chart(
    path: Path,
    difficulty: Optional[str] = None,
    track_priority: Tuple[str, ...] = DEFAULT_TRACK_PRIORITY,
) -> ParsedChart:
    """Minimal .chart parser — only the sections we need for priors.

    If `difficulty` is given it's parsed exclusively. Otherwise the first
    track in `track_priority` that has any note events is used, so a chart
    without a lead guitar (bass-only like Magnolia) still contributes.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    resolution = 192
    bpm_events: List[BPMEvent] = []
    end_tick = 0

    # Per-track buffers so we can pick the first non-empty track after one pass.
    per_track: Dict[str, Dict[str, list]] = {}

    def track_bucket(name: str) -> Dict[str, list]:
        return per_track.setdefault(name, {"notes": [], "forces": [], "sp": []})

    section: Optional[str] = None
    for raw in text.splitlines():
        m = SECTION_RE.match(raw)
        if m:
            section = m.group(1).strip()
            continue
        if section is None:
            continue

        if section == "Song":
            if "Resolution" in raw:
                try:
                    resolution = int(raw.split("=", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
            continue

        ev = EVENT_RE.match(raw)
        if not ev:
            continue
        tick = int(ev.group(1))
        body = ev.group(2)
        end_tick = max(end_tick, tick)

        if section == "SyncTrack":
            parts = body.split()
            if len(parts) >= 2 and parts[0] == "B":
                try:
                    bpm_events.append(BPMEvent(tick=tick, bpm=int(parts[1]) / 1000.0))
                except ValueError:
                    pass
            continue

        # Instrument sections — any [Expert*] / [Hard*] / etc.
        parts = body.split()
        if not parts:
            continue
        kind = parts[0]
        bucket = track_bucket(section)
        if kind == "N" and len(parts) >= 3:
            try:
                fret = int(parts[1])
                sustain = int(parts[2])
            except ValueError:
                continue
            if fret == FORCE_FRET:
                bucket["forces"].append(tick)
            elif fret == TAP_FRET:
                pass
            elif fret in NOTE_FRETS:
                bucket["notes"].append((tick, fret, sustain))
        elif kind == "S" and len(parts) >= 3 and parts[1] == "2":
            try:
                bucket["sp"].append((tick, int(parts[2])))
            except ValueError:
                pass

    # Pick which track to return. Explicit `difficulty` wins; otherwise the
    # first in priority order with any notes.
    chosen = ""
    chosen_bucket: Dict[str, list] = {"notes": [], "forces": [], "sp": []}
    candidates = (difficulty,) if difficulty else track_priority
    for name in candidates:
        if name and per_track.get(name, {}).get("notes"):
            chosen = name
            chosen_bucket = per_track[name]
            break

    bpm_events.sort(key=lambda e: e.tick)
    notes: List[Tuple[int, int, int]] = sorted(chosen_bucket["notes"], key=lambda n: n[0])
    # Extend end_tick past the last sustain in the chosen track.
    for tick, _, sustain in notes:
        end_tick = max(end_tick, tick + sustain)
    return ParsedChart(
        resolution=resolution,
        bpm_events=bpm_events,
        note_events=notes,
        force_events=sorted(chosen_bucket["forces"]),
        sp_phrases=sorted(chosen_bucket["sp"]),
        end_tick=end_tick,
        track=chosen,
    )


# ---------- song.ini (lightweight — just the keys we trust) -----------------

def parse_song_ini(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        if "=" not in line or line.strip().startswith("["):
            continue
        k, v = line.split("=", 1)
        out[k.strip().lower()] = v.strip()
    return out


# ---------- per-chart metrics -----------------------------------------------

@dataclass
class ChartMetrics:
    name: str
    track: str
    bpm_avg: float
    bpm_bucket: str
    duration_s: float
    bars: float
    note_event_count: int                     # raw N events (chord-expanded)
    chord_group_count: int                    # unique tick groups
    chord_group_with_multi: int               # groups with 2+ notes
    hopo_force_count: int                     # fret-5 events
    sp_phrase_count: int
    median_notes_per_bar: float
    notes_per_bar_norm_bpm: float             # notes/bar / (bpm/120) — tempo-normalized
    chord_rate: float                         # multi / groups
    hopo_rate: float                          # forces / groups
    sp_phrases_per_min: float
    fret_histogram: Dict[int, int]
    interval_histogram: Dict[str, int]        # bucketed inter-group tick deltas


def compute_metrics(name: str, parsed: ParsedChart) -> ChartMetrics:
    res = parsed.resolution
    ticks_per_bar = res * 4  # assume 4/4 — all five hand-charteds are 4/4

    duration = parsed.ticks_to_seconds(parsed.end_tick)
    bpm = parsed.avg_bpm()
    bars = stats.safe_div(parsed.end_tick, ticks_per_bar)

    # Group N events by tick to form "chord groups" (what the player sees as one strum).
    groups: Dict[int, List[int]] = {}
    for tick, fret, _ in parsed.note_events:
        groups.setdefault(tick, []).append(fret)
    group_ticks = sorted(groups.keys())
    chord_group_count = len(groups)
    multi = sum(1 for frets in groups.values() if len(frets) >= 2)

    # Notes-per-bar per bar bucket, then take the median (robust to intros / outros).
    per_bar: Dict[int, int] = {}
    for tick in group_ticks:
        bar_idx = tick // ticks_per_bar
        per_bar[bar_idx] = per_bar.get(bar_idx, 0) + 1
    npb_series = list(per_bar.values())
    median_npb = stats.median(npb_series)
    npb_norm = stats.safe_div(median_npb, bpm / 120.0) if bpm else 0.0

    # Inter-group intervals (ticks between consecutive chord groups).
    intervals = [group_ticks[i] - group_ticks[i - 1] for i in range(1, len(group_ticks))]

    # Scale interval bins by resolution so a res=96 chart would bucket the same way.
    bins = [b * res // 192 for b in stats.INTERVAL_BINS]
    interval_hist = stats.interval_histogram(intervals, bins)

    fret_hist = stats.histogram((f for _, f, _ in parsed.note_events), stats.FRET_KEYS)

    sp_per_min = stats.safe_div(len(parsed.sp_phrases), duration / 60.0)

    return ChartMetrics(
        name=name,
        track=parsed.track or "unknown",
        bpm_avg=round(bpm, 2),
        bpm_bucket=stats.bpm_bucket(bpm),
        duration_s=round(duration, 2),
        bars=round(bars, 2),
        note_event_count=len(parsed.note_events),
        chord_group_count=chord_group_count,
        chord_group_with_multi=multi,
        hopo_force_count=len(parsed.force_events),
        sp_phrase_count=len(parsed.sp_phrases),
        median_notes_per_bar=round(median_npb, 3),
        notes_per_bar_norm_bpm=round(npb_norm, 3),
        chord_rate=round(stats.safe_div(multi, chord_group_count), 4),
        hopo_rate=round(stats.safe_div(len(parsed.force_events), chord_group_count), 4),
        sp_phrases_per_min=round(sp_per_min, 3),
        fret_histogram=fret_hist,
        interval_histogram=interval_hist,
    )


# ---------- aggregated priors -----------------------------------------------

@dataclass
class ReferencePriors:
    """Calibration surface consumed by CHART-ALGO. Values are population
    estimates aggregated across the hand-charted reference corpus."""
    source_charts: List[str]
    corpus_bpm_avg: float
    corpus_duration_s: float
    median_notes_per_bar: float
    notes_per_bar_norm_bpm: float
    chord_rate: float
    hopo_rate: float
    sp_phrases_per_min: float
    fret_histogram: Dict[int, int]
    fret_distribution: Dict[int, float]
    interval_histogram: Dict[str, int]
    interval_distribution: Dict[str, float]
    per_bpm_bucket: Dict[str, Dict[str, float]] = field(default_factory=dict)
    per_chart: List[Dict] = field(default_factory=list)

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def aggregate_priors(metrics: List[ChartMetrics]) -> ReferencePriors:
    if not metrics:
        raise ValueError("no chart metrics to aggregate")

    total_dur = sum(m.duration_s for m in metrics)
    total_groups = sum(m.chord_group_count for m in metrics)
    total_multi = sum(m.chord_group_with_multi for m in metrics)
    total_forces = sum(m.hopo_force_count for m in metrics)
    total_sp = sum(m.sp_phrase_count for m in metrics)

    bpm_weighted = stats.safe_div(
        sum(m.bpm_avg * m.duration_s for m in metrics), total_dur
    )

    fret_hist = stats.merge_counts(m.fret_histogram for m in metrics)
    interval_hist = stats.merge_label_counts(m.interval_histogram for m in metrics)

    # Per-bucket snapshot — CHART-ALGO can pick the band matching the target BPM.
    per_bucket: Dict[str, Dict[str, float]] = {}
    for bucket in stats.BPM_BUCKETS:
        in_bucket = [m for m in metrics if m.bpm_bucket == bucket]
        if not in_bucket:
            continue
        dur = sum(m.duration_s for m in in_bucket)
        groups = sum(m.chord_group_count for m in in_bucket)
        multi = sum(m.chord_group_with_multi for m in in_bucket)
        forces = sum(m.hopo_force_count for m in in_bucket)
        sp = sum(m.sp_phrase_count for m in in_bucket)
        per_bucket[bucket] = {
            "charts": float(len(in_bucket)),
            "median_notes_per_bar": round(stats.median([m.median_notes_per_bar for m in in_bucket]), 3),
            "notes_per_bar_norm_bpm": round(stats.median([m.notes_per_bar_norm_bpm for m in in_bucket]), 3),
            "chord_rate": round(stats.safe_div(multi, groups), 4),
            "hopo_rate": round(stats.safe_div(forces, groups), 4),
            "sp_phrases_per_min": round(stats.safe_div(sp, dur / 60.0), 3),
        }

    return ReferencePriors(
        source_charts=[m.name for m in metrics],
        corpus_bpm_avg=round(bpm_weighted, 2),
        corpus_duration_s=round(total_dur, 2),
        median_notes_per_bar=round(stats.median([m.median_notes_per_bar for m in metrics]), 3),
        notes_per_bar_norm_bpm=round(stats.median([m.notes_per_bar_norm_bpm for m in metrics]), 3),
        chord_rate=round(stats.safe_div(total_multi, total_groups), 4),
        hopo_rate=round(stats.safe_div(total_forces, total_groups), 4),
        sp_phrases_per_min=round(stats.safe_div(total_sp, total_dur / 60.0), 3),
        fret_histogram={int(k): int(v) for k, v in fret_hist.items()},
        fret_distribution={int(k): round(v, 4) for k, v in stats.normalize(fret_hist).items()},
        interval_histogram=interval_hist,
        interval_distribution={k: round(v, 4) for k, v in stats.normalize_labels(interval_hist).items()},
        per_bpm_bucket=per_bucket,
        per_chart=[asdict(m) for m in metrics],
    )


# ---------- corpus discovery + CLI ------------------------------------------

DEFAULT_CORPUS: List[Tuple[str, Path]] = [
    ("Fell In Luv",
     Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs/Fell In Luv")),
    ("Mileage",
     Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs/Mileage")),
    ("NewwChoppa",
     Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs/NewwChoppa")),
    ("Magnolia",
     Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs/PlayBoi Carti - Magnolia")),
    ("EVILJ0RDAN",
     Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs/EVILJ0RDAN/EVILJ0RDAN")),
]


def build_priors(corpus: List[Tuple[str, Path]] = DEFAULT_CORPUS) -> ReferencePriors:
    collected: List[ChartMetrics] = []
    for name, folder in corpus:
        notes = folder / "notes.chart"
        if not notes.exists():
            print(f"[skip] {name}: no notes.chart at {folder}", file=sys.stderr)
            continue
        parsed = parse_notes_chart(notes)
        if not parsed.note_events:
            print(f"[skip] {name}: no ExpertSingle notes", file=sys.stderr)
            continue
        m = compute_metrics(name, parsed)
        collected.append(m)
        print(
            f"[ok]   {name:12s} [{m.track:20s}] bpm={m.bpm_avg:6.2f}  "
            f"dur={m.duration_s:7.2f}s  groups={m.chord_group_count:4d}  "
            f"chord%={m.chord_rate*100:5.2f}  sp/min={m.sp_phrases_per_min:5.2f}",
            file=sys.stderr,
        )
    return aggregate_priors(collected)


def main() -> int:
    out_path = Path(__file__).resolve().parent / "reference_priors.json"
    priors = build_priors()
    priors.to_json(out_path)
    print(f"wrote {out_path}  ({len(priors.source_charts)} charts)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
