# Chart Quality Report — R.I.P. (auto), v1.0 → v1.4

Target chart: `C:/Users/Kayky/Documents/Clone Hero/Songs/Playboi Carti - Playboi Carti - R.I.P. (auto)/notes.chart`
Reference priors: `core/reference_priors.json` (5 hand-charted songs: Fell In Luv, Mileage, NewwChoppa, Magnolia, EVILJ0RDAN)
Comparator: `core/chart_compare.py :: compare_chart_vs_priors()`
Produced by: Archivist · 2026-11-02

---

## Consolidated metric ledger

Scalar in-band bands derived from min/max observed across the 5 reference charts.
`v1.0` was never measured through this comparator (predates Fase 2); figures below start at v1.1 — the first version compared against the priors.

| metric                 |  prior | v1.0 | v1.1   | v1.2   | v1.3   | v1.4   | v1.5   | band              | status (v1.5)             |
|------------------------|-------:|------|-------:|-------:|-------:|-------:|-------:|-------------------|---------------------------|
| median_notes_per_bar   |  3.000 |  —   | 5.000  | 5.000  | 5.000  | 5.000  | 5.000  | [2.500, 6.000]    | in-band (high)            |
| notes_per_bar_norm_bpm |  2.338 |  —   | 4.180  | 4.276  | 4.276  | 4.276  | 4.276  | [1.840, 4.768]    | in-band (high)            |
| chord_rate             |  0.268 |  —   | 0.088  | 0.065  | 0.099  | 0.312  | 0.308  | [0.000, 1.000]    | **in-band + near prior**  |
| hopo_rate              |  0.000 |  —   | 0.086  | 0.000  | 0.000  | 0.000  | 0.000  | [0.000, 0.000]    | **in-band + match**       |
| sp_phrases_per_min     |  9.303 |  —   | 1.292  | 1.262  | 1.262  | 1.262  | 1.262  | [0.000, 43.085]   | in-band (low)             |
| fret_L1 (shape)        |      0 |  —   | 1.082  | 1.072  | 1.034  | 0.622  | 0.340  | lower = closer    | **69% flatten vs v1.1**   |
| interval_L1 (shape)    |      0 |  —   | 0.827  | 0.793  | 0.793  | 0.793  | 0.709  | lower = closer    | **first move: -11%**      |
| bpm_avg                | 153.71 |  —   | 143.56 | 140.32 | 140.32 | 140.32 | 140.32 | —                 | multi-tempo locked        |
| chord_group_count      |      — |  —   | 557    | 525    | 525    | 525    | 522    | —                 | stable                    |
| sync B markers         |      — |  —   | 1      | 297    | 297    | 297    | 297    | —                 | multi-tempo locked        |

### Per-version diff summary

- **v1.1** (first measured): HOPO forces fired at 8.6% (none of the 5 humans used `N 5` even once) — **only out-of-band metric** in the whole run. Chord density low. Single-BPM SyncTrack.
- **v1.2**: HOPO injection removed → `hopo_rate=0.000`. Multi-tempo SyncTrack enabled (1 → 297 B markers). Chord-triplication only thickened existing chord groups (2-note → 3-note), total group count fell and `chord_rate` regressed to 0.065. Fret 0 share exploded to +0.54 (power chords always on green).
- **v1.3**: Chord insertion expanded from downbeats-only to every beat; chord-root pulled from secondary chroma peak. Chord_rate climbed +53% to 0.099. Fret_L1 only marginally improved (1.072 → 1.034) — secondary chroma peak was clipping to the same bin.
- **v1.4**: Chord insertion gained off-beat RMS onsets; chord root rotated across top-3 chroma peaks. `chord_rate` hit **0.312 (+16.6% over prior, near-perfect)**. `fret_L1` dropped **42%** vs v1.1 to 0.622. HOPO held at zero.
- **v1.5**: Interval shape pass finally landed — `interval_L1` moved for the first time (0.793 → 0.709, -11%). Fret-root diversification continued — `fret_L1` dropped another 45% to **0.340**. `chord_rate=0.308` (+15.1% over prior, inside near-prior band). Fret 0 is now within +0.03 of the prior share — the main remaining skew is fret 7 (open notes) under-weight by -0.14.

---

## Final verdict (v1.5)

**In-band metrics (5/5 scalar priors)**: median_notes_per_bar, notes_per_bar_norm_bpm, chord_rate, hopo_rate, sp_phrases_per_min.

**Near-prior matches (2/5)**: `hopo_rate` (0.000 == 0.000, exact), `chord_rate` (0.308 vs 0.268, +15.1%).

**In-band but at distribution extremes (3/5)**:

| metric                 | v1.5   | prior  | position in reference distribution |
|------------------------|-------:|-------:|-------------------------------------|
| median_notes_per_bar   | 5.000  | 3.000  | p80 (only Mileage sits higher at 6) |
| notes_per_bar_norm_bpm | 4.276  | 2.338  | p80 (close to Mileage's 4.768 max)  |
| sp_phrases_per_min     | 1.262  | 9.303  | p40 (3 of 5 charts also sit at 0)   |

**Shape metrics**:

- `fret_L1 = 0.340` — 69% flatter than v1.1 (1.082). Fret 0 over-weight shrank from +0.54 → +0.29 → **+0.03** across v1.3 → v1.4 → v1.5. Remaining skew: fret 7 (open notes) under-weight by **-0.14**, fret 4 slightly over at +0.07.
- `interval_L1 = 0.709` — first movement since v1.1 (was stuck at 0.793 through v1.2-v1.4). v1.5 under-weights the 192-384 tick band (-0.18) and 0-48 (-0.10), over-weights 96-192 (+0.24). The 192-384 gap is the big remaining target.

### Fix list for v1.6+

1. **Interval shape (int_L1 = 0.709, now moving)** — v1.5 made the first dent. The 96-192 bin is still over-weight by +0.24, and 192-384 is under-weight by -0.18. Push the gap-snap pass harder: for notes landing in [96, 192), either promote the gap to 192 (snap next note later) or demote it to 48-96 if the audio onset is strong enough. The dense 0-48 band (ghost-note cluster) is still at 0 share — only Mileage uses that band and v1.5 doesn't follow.
2. **Fret 7 (open notes) under-weight (-0.14)** — only remaining big fret skew. 2 of 5 reference charts (Mileage, EVILJ0RDAN) use open notes at 44-50% share. R.I.P. still around 4%. Trigger open-note mapping when audio has low-frequency sustain (sub-bass band energy > threshold).
3. **Note density high (p80 on both notes-per-bar metrics)** — not failing but at the top of the reference distribution. Only worth touching if R.I.P. feels busy to play.
4. **SP sparse (p40)** — 1.26 phrases/min vs prior 9.30. Reference corpus is bimodal (0 or ~43/min); defensible as-is.

### Headline

**R.I.P. (auto) v1.5 passes all hard spec AND is now closing the shape gap**: 5/5 scalar metrics in-band, `fret_L1` down 69% vs v1.1, `interval_L1` finally moved (-11%). The HOPO fix remains stable across 4 iterations. One scalar metric (chord_rate) and the two shape metrics are all converging toward prior values.

---

## Cross-song generalization (Fase 8 batch)

**Target**: `tmp/validate_v15/` — Deadbolt was scheduled to drop 2+ auto-charts here for cross-song validation of v1.5 gains.

**Status as of 2026-11-02**: batch **fully landed** — 5/5 tracks parsed.

### Multi-song v1.5 — per-track table

Chord target band set by baker for this cohort: **[0.20, 0.35]** (tighter than the 5-ref prior envelope `[0, 1]`).
`feel_distance` = `fret_L1 + interval_L1` — a simple summed shape-distance score (lower = closer to the hand-charted shape).

| track               |  bpm  | duration | chord_rate | hopo | npb | sp/min | fret_L1 | int_L1 | feel_distance | chord-in-band [0.20,0.35] | OOB scalars        |
|---------------------|------:|---------:|-----------:|-----:|----:|-------:|--------:|-------:|--------------:|:-------------------------:|--------------------|
| EVILJ0RDAN (auto)   | 107.5 |  124.7s  |   0.4539   | 0.00 |  6  |  0.48  |  0.4294 | 1.1349 |     1.5643    |          NO (high)        | notes_per_bar_norm_bpm |
| Fell In Luv (auto)  | 163.1 |  204.0s  |   0.3711   | 0.00 |  5  |  0.00  |  0.3283 | 0.5909 |     0.9192    |          NO (high)        | —                  |
| Rockstar Made (auto)| 150.3 |  194.6s  |   0.3361   | 0.00 |  5  |  0.62  |  0.3519 | 0.7891 |     1.1410    |          **YES**          | —                  |
| Shoota (auto)       | 153.3 |  152.1s  |   0.4358   | 0.00 |  5  |  1.18  |  0.4616 | 0.5950 |     1.0566    |          NO (high)        | —                  |
| Timeless (auto)     | 119.8 |  251.0s  |   0.4836   | 0.00 |  6  |  0.24  |  0.3776 | 1.1075 |     1.4851    |          NO (high)        | notes_per_bar_norm_bpm |

### Multi-song v1.5 — aggregate

| metric                                | mean   | median | min    | max    |
|---------------------------------------|-------:|-------:|-------:|-------:|
| chord_rate                            | 0.4161 | 0.4358 | 0.3361 | 0.4836 |
| hopo_rate                             | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| median_notes_per_bar                  | 5.400  | 5.000  | 5.000  | 6.000  |
| sp_phrases_per_min                    | 0.504  | 0.481  | 0.000  | 1.180  |
| fret_L1                               | 0.3898 | 0.3776 | 0.3283 | 0.4616 |
| interval_L1                           | 0.8435 | 0.7891 | 0.5909 | 1.1349 |
| **feel_distance** (fret_L1 + int_L1)  | **1.2332** | **1.1410** | 0.9192 | 1.5643 |

### Multi-song v1.5 — verdict

- **chord in-band [0.20, 0.35]**: **1/5** (Rockstar Made only). The other 4 overshoot the band by 0.02–0.13. The v1.5 chord recipe is tuned too heavy for the whole cohort — the fix that got R.I.P. to 0.308 pushes most Carti songs past 0.35.
- **hopo**: **5/5 at 0.000** — the HOPO fix generalizes perfectly, no track regressed.
- **notes_per_bar_norm_bpm OOB**: **2/5** fail (EVILJ0RDAN at bpm=107, Timeless at bpm=120). Both are the slower tracks in the cohort — the recipe emits 6 notes/bar on them, which `/ (bpm/120)` blows past the [1.84, 4.77] band. Faster tracks (150-163 bpm) stay in-band.
- **Strongest outlier**: **EVILJ0RDAN (auto)** — highest `feel_distance` at 1.5643 (vs cohort median 1.1410), worst `interval_L1` in the batch (1.1349), chord `0.4539` (+0.10 over band), and OOB on `notes_per_bar_norm_bpm`. Second place: Timeless (feel=1.4851).
- **Best track**: Fell In Luv (auto) — lowest `feel_distance` (0.9192), lowest `fret_L1` (0.3283), only out-of-band miss is chord (0.3711, +0.02 over band). Closest to the hand-charted feel in the batch.
- **Median feel_distance**: **1.1410** — half the cohort sits below Rockstar Made's shape distance.

### Multi-song v1.5 — fix list for v1.6+

1. **Chord recipe is cohort-heavy** — 4/5 tracks overshoot [0.20, 0.35]. Scale the chord-insertion probability by `prior_chord_rate / observed_chord_rate` as a self-calibrating feedback knob, or cap chord density after the first pass.
2. **Low-BPM notes-per-bar blow-up** — EVILJ0RDAN (107bpm) and Timeless (120bpm) land at 6 notes/bar, which breaks `notes_per_bar_norm_bpm`. For tracks below ~130 bpm, reduce density target by `(bpm/120)²` instead of linear.
3. **Interval_L1 bimodal** — fast tracks (Fell In Luv, Shoota) sit at ~0.59, slow tracks (EVILJ0RDAN, Timeless) at ~1.11. The interval-quantization pass that fixed R.I.P. isn't firing on slow charts — likely tempo-dependent snap thresholds need bpm-normalization.
