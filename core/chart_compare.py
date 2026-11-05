"""Compare a generated chart to the hand-charted reference priors.

Two surfaces:

    compare_chart_vs_priors(chart_path)   -> ChartDelta
    sanity_report()                       -> prints per-metric distribution
                                             of each prior across the 5 reference charts

CLI:
    python -m core.chart_compare --sanity
    python -m core.chart_compare <path-to-notes.chart>
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from core import stats
from core.reference import (
    ChartMetrics,
    ReferencePriors,
    compute_metrics,
    parse_notes_chart,
)


PRIORS_PATH = Path(__file__).resolve().parent / "reference_priors.json"


# ---------- loading ---------------------------------------------------------

def load_priors(path: Path = PRIORS_PATH) -> ReferencePriors:
    data = json.loads(path.read_text(encoding="utf-8"))
    # dataclass accepts dicts with extra keys skipped — only keep known fields
    known = {f for f in ReferencePriors.__dataclass_fields__}
    core_kwargs = {k: v for k, v in data.items() if k in known}
    # JSON turns int keys into strings — put them back for the fret histograms.
    if "fret_histogram" in core_kwargs:
        core_kwargs["fret_histogram"] = {int(k): v for k, v in core_kwargs["fret_histogram"].items()}
    if "fret_distribution" in core_kwargs:
        core_kwargs["fret_distribution"] = {int(k): v for k, v in core_kwargs["fret_distribution"].items()}
    return ReferencePriors(**core_kwargs)


# ---------- delta model -----------------------------------------------------

@dataclass
class ChartDelta:
    """Per-metric comparison of one chart against the reference priors.

    All `_delta` fields are (candidate - prior); `_pct` is signed percent
    change relative to the prior (positive = candidate is higher). A
    `_in_band` flag marks whether the candidate falls inside the min/max
    observed across the 5 reference charts — that's the "soft spec" that
    CHART-ALGO should land inside.
    """
    chart_path: str
    track: str
    bpm_avg: float
    duration_s: float

    # scalar metrics
    scalars: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # distribution metrics (fret / interval): per-key delta
    fret_distribution_delta: Dict[int, float] = field(default_factory=dict)
    interval_distribution_delta: Dict[str, float] = field(default_factory=dict)
    # overall "distance" from the priors (0 = identical shape)
    fret_l1_distance: float = 0.0
    interval_l1_distance: float = 0.0
    # qualitative summary line, safe to print
    summary: str = ""


def _scalar_entry(cand: float, prior: float, lo: float, hi: float) -> Dict[str, float]:
    delta = cand - prior
    pct = stats.safe_div(delta, prior) * 100.0 if prior else 0.0
    return {
        "candidate": round(cand, 4),
        "prior": round(prior, 4),
        "delta": round(delta, 4),
        "pct_change": round(pct, 2),
        "band_min": round(lo, 4),
        "band_max": round(hi, 4),
        "in_band": float(lo <= cand <= hi),
    }


def _per_chart_scalar(priors: ReferencePriors, key: str) -> List[float]:
    return [float(c[key]) for c in priors.per_chart if key in c]


def compare_chart_vs_priors(
    chart_path: Path | str,
    priors: Optional[ReferencePriors] = None,
    name: Optional[str] = None,
) -> ChartDelta:
    path = Path(chart_path)
    if not path.exists():
        raise FileNotFoundError(f"notes.chart not found: {path}")
    if priors is None:
        priors = load_priors()

    parsed = parse_notes_chart(path)
    if not parsed.note_events:
        raise ValueError(f"no playable notes in {path}")
    cand = compute_metrics(name or path.parent.name, parsed)

    # Per-chart bands for in-band checks.
    bands = {
        "median_notes_per_bar": _per_chart_scalar(priors, "median_notes_per_bar"),
        "notes_per_bar_norm_bpm": _per_chart_scalar(priors, "notes_per_bar_norm_bpm"),
        "chord_rate": _per_chart_scalar(priors, "chord_rate"),
        "hopo_rate": _per_chart_scalar(priors, "hopo_rate"),
        "sp_phrases_per_min": _per_chart_scalar(priors, "sp_phrases_per_min"),
    }

    scalars: Dict[str, Dict[str, float]] = {}
    for key, prior_val in [
        ("median_notes_per_bar", priors.median_notes_per_bar),
        ("notes_per_bar_norm_bpm", priors.notes_per_bar_norm_bpm),
        ("chord_rate", priors.chord_rate),
        ("hopo_rate", priors.hopo_rate),
        ("sp_phrases_per_min", priors.sp_phrases_per_min),
    ]:
        series = bands[key]
        lo = min(series) if series else prior_val
        hi = max(series) if series else prior_val
        scalars[key] = _scalar_entry(getattr(cand, key), prior_val, lo, hi)

    cand_fret_dist = stats.normalize(cand.fret_histogram)
    fret_delta = {
        int(k): round(cand_fret_dist.get(int(k), 0.0) - priors.fret_distribution.get(int(k), 0.0), 4)
        for k in stats.FRET_KEYS
    }
    fret_l1 = sum(abs(v) for v in fret_delta.values())

    cand_int_dist = stats.normalize_labels(cand.interval_histogram)
    interval_delta: Dict[str, float] = {}
    all_bins = set(cand_int_dist) | set(priors.interval_distribution)
    for k in sorted(all_bins):
        interval_delta[k] = round(
            cand_int_dist.get(k, 0.0) - priors.interval_distribution.get(k, 0.0), 4
        )
    interval_l1 = sum(abs(v) for v in interval_delta.values())

    out_of_band = [k for k, v in scalars.items() if not v["in_band"]]
    summary_bits = [
        f"bpm={cand.bpm_avg:.1f} ({cand.bpm_bucket})",
        f"groups={cand.chord_group_count}",
        f"chord%={cand.chord_rate*100:.1f} (prior {priors.chord_rate*100:.1f})",
        f"fret_L1={fret_l1:.3f}",
        f"int_L1={interval_l1:.3f}",
        f"out_of_band={out_of_band or 'none'}",
    ]

    return ChartDelta(
        chart_path=str(path),
        track=cand.track,
        bpm_avg=cand.bpm_avg,
        duration_s=cand.duration_s,
        scalars=scalars,
        fret_distribution_delta=fret_delta,
        interval_distribution_delta=interval_delta,
        fret_l1_distance=round(fret_l1, 4),
        interval_l1_distance=round(interval_l1, 4),
        summary=" | ".join(summary_bits),
    )


# ---------- sanity report ---------------------------------------------------

SCALAR_KEYS = [
    "median_notes_per_bar",
    "notes_per_bar_norm_bpm",
    "chord_rate",
    "hopo_rate",
    "sp_phrases_per_min",
]


def _rank_percentile(series: List[float], value: float) -> float:
    """Fraction of samples <= value, in [0,100]."""
    if not series:
        return 0.0
    below = sum(1 for s in series if s <= value)
    return 100.0 * below / len(series)


def sanity_report(priors: Optional[ReferencePriors] = None) -> None:
    priors = priors or load_priors()
    charts = priors.per_chart
    print(f"=== sanity report — {len(charts)} reference charts ===\n")

    # Scalars table: value + rank-percentile against the 5 charts
    print(f"{'metric':<24} {'prior':>10} {'min':>8} {'median':>8} {'max':>8}   per-chart (value / percentile)")
    for key in SCALAR_KEYS:
        series = [float(c[key]) for c in charts]
        prior_val = getattr(priors, key)
        bits = []
        for c in charts:
            v = float(c[key])
            p = _rank_percentile(series, v)
            bits.append(f"{c['name'][:10]:>10}={v:.3f}@p{p:3.0f}")
        print(
            f"{key:<24} {prior_val:>10.3f} {min(series):>8.3f} "
            f"{stats.median(series):>8.3f} {max(series):>8.3f}   "
            + "  ".join(bits)
        )

    # Fret histogram: per-chart distribution vs prior
    print(f"\n{'fret':<6} {'prior':>8}   " + "  ".join(f"{c['name'][:10]:>10}" for c in charts))
    for fret in stats.FRET_KEYS:
        prior_v = priors.fret_distribution.get(fret, 0.0)
        row = []
        for c in charts:
            fh = {int(k): v for k, v in c["fret_histogram"].items()}
            total = sum(fh.values()) or 1
            row.append(fh.get(fret, 0) / total)
        print(f"{fret:<6} {prior_v:>8.4f}   " + "  ".join(f"{v:>10.4f}" for v in row))

    # Interval histogram: per-chart distribution vs prior
    print(f"\n{'interval':<10} {'prior':>8}   " + "  ".join(f"{c['name'][:10]:>10}" for c in charts))
    for bin_label in priors.interval_distribution:
        prior_v = priors.interval_distribution[bin_label]
        row = []
        for c in charts:
            ih = c["interval_histogram"]
            total = sum(ih.values()) or 1
            row.append(ih.get(bin_label, 0) / total)
        print(f"{bin_label:<10} {prior_v:>8.4f}   " + "  ".join(f"{v:>10.4f}" for v in row))


# ---------- CLI -------------------------------------------------------------

def _format_delta(d: ChartDelta) -> str:
    lines = [
        f"chart:    {d.chart_path}",
        f"track:    {d.track}",
        f"bpm:      {d.bpm_avg:.2f}",
        f"duration: {d.duration_s:.2f}s",
        "",
        f"{'metric':<24} {'candidate':>10} {'prior':>10} {'delta':>10} {'pct':>8} {'band':>18} {'in_band':>8}",
    ]
    for key, s in d.scalars.items():
        band = f"[{s['band_min']:.3f},{s['band_max']:.3f}]"
        lines.append(
            f"{key:<24} {s['candidate']:>10.4f} {s['prior']:>10.4f} "
            f"{s['delta']:>+10.4f} {s['pct_change']:>+7.1f}% {band:>18} "
            f"{'YES' if s['in_band'] else 'NO':>8}"
        )
    lines.append("")
    lines.append(f"fret distribution delta    (L1={d.fret_l1_distance:.4f})")
    for fret in stats.FRET_KEYS:
        lines.append(f"  fret {fret}: {d.fret_distribution_delta.get(fret, 0.0):+.4f}")
    lines.append(f"\ninterval distribution delta (L1={d.interval_l1_distance:.4f})")
    for k, v in d.interval_distribution_delta.items():
        lines.append(f"  {k:<10}: {v:+.4f}")
    lines.append(f"\nsummary: {d.summary}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="compare a chart against reference priors")
    ap.add_argument("chart", nargs="?", help="path to a notes.chart to compare")
    ap.add_argument("--sanity", action="store_true", help="print per-metric sanity report")
    ap.add_argument("--json", action="store_true", help="emit delta as JSON on stdout")
    args = ap.parse_args(argv)

    if args.sanity:
        sanity_report()
        if not args.chart:
            return 0
        print()

    if not args.chart:
        ap.print_help()
        return 2

    delta = compare_chart_vs_priors(args.chart)
    if args.json:
        print(json.dumps(asdict(delta), indent=2))
    else:
        print(_format_delta(delta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
