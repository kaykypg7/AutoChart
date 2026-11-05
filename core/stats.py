"""Pure-stdlib statistics helpers for chart priors.

Kept dependency-free so it can be imported from anywhere in the pipeline
without pulling librosa / numpy. Everything here operates on plain Python
numbers and lists.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, Iterable, List, Mapping, Sequence


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def mean(values: Sequence[float]) -> float:
    return safe_div(sum(values), len(values))


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile. p in [0, 100]."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(s[int(k)])
    return float(s[lo] + (s[hi] - s[lo]) * (k - lo))


def histogram(values: Iterable[int], keys: Sequence[int]) -> Dict[int, int]:
    """Fixed-key histogram. Keys not in `keys` are dropped silently —
    pass the full expected key set so downstream reads are stable."""
    c = Counter(values)
    return {int(k): int(c.get(k, 0)) for k in keys}


def interval_histogram(values: Iterable[int], bins: Sequence[int]) -> Dict[str, int]:
    """Bucketed histogram over continuous int intervals.

    `bins` is the right-edge of each bucket in ascending order; the final
    bucket catches everything larger. Labels look like "0-96", "96-192",
    ..., "768+" so JSON stays readable.
    """
    edges = sorted(set(bins))
    labels = []
    for i, hi in enumerate(edges):
        lo = 0 if i == 0 else edges[i - 1]
        labels.append(f"{lo}-{hi}")
    labels.append(f"{edges[-1]}+")
    out = {lbl: 0 for lbl in labels}
    for v in values:
        placed = False
        for i, hi in enumerate(edges):
            if v < hi:
                out[labels[i]] += 1
                placed = True
                break
        if not placed:
            out[labels[-1]] += 1
    return out


def merge_counts(dicts: Iterable[Mapping[int, int]]) -> Dict[int, int]:
    acc: Counter = Counter()
    for d in dicts:
        acc.update(d)
    return dict(acc)


def merge_label_counts(dicts: Iterable[Mapping[str, int]]) -> Dict[str, int]:
    acc: Dict[str, int] = {}
    for d in dicts:
        for k, v in d.items():
            acc[k] = acc.get(k, 0) + int(v)
    return acc


def normalize(d: Mapping[int, int]) -> Dict[int, float]:
    total = sum(d.values())
    return {int(k): safe_div(v, total) for k, v in d.items()}


def normalize_labels(d: Mapping[str, int]) -> Dict[str, float]:
    total = sum(d.values())
    return {k: safe_div(v, total) for k, v in d.items()}


def bpm_bucket(bpm: float) -> str:
    """Four coarse bands matching playability tiers in guitar charts."""
    if bpm < 100:
        return "slow"
    if bpm < 140:
        return "mid"
    if bpm < 180:
        return "fast"
    return "vfast"


BPM_BUCKETS: List[str] = ["slow", "mid", "fast", "vfast"]
FRET_KEYS: List[int] = [0, 1, 2, 3, 4, 7]  # 0-4 colored, 7 open
INTERVAL_BINS: List[int] = [48, 96, 192, 384, 768]  # ticks @ res=192
