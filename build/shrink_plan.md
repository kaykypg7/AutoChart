# Shrink Plan — CloneHeroAutoChart.exe

**Author:** Starter · **Date:** 2026-11-02 · **Status:** PROPOSED (no .spec changes yet)

---

## Baseline

| Metric | Value |
|---|---|
| `dist/CloneHeroAutoChart.exe` | **209.11 MB** (219,268,856 bytes) |
| Target | **150 MB** |
| Gap | **-59.11 MB** |
| Build host | Windows 11, Python 3.14, PyInstaller 6.x, venv at `.venv/` |
| Spec | `build/build.spec`, `strip=False`, `upx=False` |

### Site-package weight (uncompressed on disk → bundled into the pkg)

| Package | Disk MB |
|---|---:|
| llvmlite | 116.20 (115 MB is `binding/llvmlite.dll` alone — not shrinkable without dropping numba) |
| scipy | 109.68 |
| sklearn | 40.94 |
| numpy | 31.38 |
| numba | 26.54 |
| librosa | 2.25 |

Observed zlib ratio in the `.pkg` is **~0.64** (209 MB packed from ~326 MB raw). All savings below use `packed = raw × 0.64`.

---

## What the project actually imports

Direct from `core/` + `gui/`:

- `numpy` (3 files)
- `librosa.load / get_duration / effects.hpss / beat.beat_track / frames_to_time / onset.onset_strength / onset.onset_detect / feature.chroma_cqt / feature.rms / feature.spectral_centroid / feature.mfcc / segment.agglomerative / yin`
- `scipy.signal.butter`, `scipy.signal.sosfiltfilt` (ONLY — one site in `drum_transcriber.py:35`)
- **`sklearn`: zero direct imports.** Pulled only transitively by `librosa.segment.agglomerative` → `sklearn.cluster.AgglomerativeClustering` + `sklearn.feature_extraction.image.grid_to_graph`.
- `numba` / `llvmlite`: required at runtime by librosa JIT paths.

---

## Diagnosed bloat (from PyInstaller TOC)

| Symptom | TOC entries | Cause |
|---|---:|---|
| `numba.tests.*` bundled | 542 | `collect_submodules("numba")` sweeps `tests/` |
| `scipy.*.tests.*` bundled | 759 | `collect_submodules("scipy")` sweeps tests of every subpackage |
| `sklearn.utils.tests.*` bundled | 92 | `collect_submodules("sklearn.utils")` sweeps `tests/` (confirmed in build log: `Analyzing hidden import 'sklearn.utils.tests.test_random'` ... all the way to `test_weight_vector`) |
| `numba.cuda.*` bundled | 423 | No GPU on the target → dead weight |
| `scipy.stats.*` bundled | 223 | Unused by our code path |
| `scipy.optimize.*` bundled | 279 | Unused by our code path |
| `scipy.spatial.*` bundled | 72 | Unused by our code path |
| `narwhals.*` bundled | 308 | Dataframe compat layer pulled transitively by sklearn — unused |

---

## Proposed exclusions (ordered by MB-saved estimate)

Add these to `excludes=[...]` in `build/build.spec`. Estimates are **packed MB saved** (raw × 0.64).

### Tier A — Zero-risk (pure dead weight)

| # | Exclude | Reason | Est. saving |
|---|---|---|---:|
| A1 | `numba.tests` | Test suite shipped by `collect_submodules("numba")` | **-6.7 MB** |
| A2 | `numba.cuda` | No GPU on target machine | **-2.5 MB** |
| A3 | `scipy.io`, `scipy.integrate`, `scipy.odr`, `scipy.cluster`, `scipy.datasets`, `scipy.differentiate`, `scipy.misc`, `scipy.constants`, `scipy.fftpack` | None imported by librosa core paths or our code | **-5.8 MB** |
| A4 | `sklearn.datasets`, `sklearn.ensemble`, `sklearn.linear_model`, `sklearn.svm`, `sklearn.decomposition`, `sklearn.model_selection`, `sklearn.manifold`, `sklearn.mixture`, `sklearn.gaussian_process`, `sklearn.inspection`, `sklearn.compose`, `sklearn.impute`, `sklearn.covariance`, `sklearn.cross_decomposition`, `sklearn.neural_network`, `sklearn.semi_supervised`, `sklearn.feature_selection`, `sklearn.tree`, `sklearn.experimental`, `sklearn.frozen`, `sklearn.callback`, `sklearn.datasets`, `sklearn.tests` | librosa.segment.agglomerative needs only `cluster`, `feature_extraction.image`, `utils`, `base`, `metrics`, `_loss`, `neighbors` | **-10.5 MB** |
| A5 | `narwhals` | Pulled only via sklearn optional dataframe backend | **-2.5 MB** |
| A6 | `scipy._external` (array_api_compat torch/dask bridges) | Non-numpy backends unused | **-1.1 MB** |
| A7 | `llvmlite.tests`, `sklearn.externals.tests`, `scipy.*.tests` (catchall patterns) | Residual test modules | **-2.0 MB** |
| A8 | `tcl.*.tests`, `pytest`, `unittest.test`, `distutils`, `setuptools` extras, `pycparser.lextab`, `pycparser.yacctab` (already warned-missing) | Build-time only | **-1.0 MB** |

**Tier A subtotal: ~-32.1 MB → projected 177 MB**

### Tier B — Low-risk (requires one smoke test on clean VM)

| # | Exclude | Reason / risk | Est. saving |
|---|---|---|---:|
| B1 | `scipy.stats` | Not called by our code or by librosa funcs we touch; sklearn.cluster.AgglomerativeClustering with `linkage="ward"` doesn't need it. Risk: some `sklearn.metrics` paths pull `scipy.stats.rankdata` → smoke-test `analyzer.full_analyze` end-to-end. | **-12.3 MB** |
| B2 | `scipy.optimize` | Not called by us. sklearn.cluster can import but with `linkage="ward"` doesn't execute. Risk: numba's `np.ufunc` or sklearn init may touch. | **-9.8 MB** |
| B3 | `scipy.spatial` | Not called directly. `grid_to_graph` returns a sparse matrix — no distance computation. Risk: `AgglomerativeClustering(connectivity=...)` with precomputed may touch. | **-5.1 MB** |
| B4 | `sklearn.preprocessing` | Not called in our segment path. Risk: some sklearn.base init paths reference `StandardScaler` indirectly. | **-1.2 MB** |

**Tier B subtotal: ~-28.4 MB → projected cumulative 149 MB**

### Tier C — Aggressive (code-change required, FLAG only — do NOT include in Fase 1 .spec)

| # | Action | Est. saving | Trade-off |
|---|---|---:|---|
| C1 | Enable `upx=True` with `upx_exclude` listing every numba `.pyd`, scipy `_*.pyd`, llvmlite.dll, numpy OpenBLAS DLLs | **-10 to -15 MB** | UPX pass adds ~30s build time; wrong exclude = runtime crash. Also triggers AV false positives on some Windows installs. |
| C2 | `strip=True` on remaining non-protected binaries | -1 MB | Marginal on PE; some DLLs can't be stripped. |
| C3 | Drop `numba` + `llvmlite` entirely (`NUMBA_DISABLE_JIT=1` env, patch librosa decorators) | **-130 MB** | Charting runs 5–20× slower on `librosa.resample`, `yin`, `beat_track`. Requires UX decision from the baker — not Fase 1. |
| C4 | Replace `librosa.segment.agglomerative` call in `analyzer.py:97` with a numpy/scipy-only clustering (e.g. `scipy.cluster.hierarchy.fcluster`) → then drop **all** of sklearn + narwhals | **-30 MB** (on top of Tier A4/B4) | Modest refactor in `core/analyzer.py`. Changes clustering behavior slightly. |

---

## Projected end state

| Scenario | Projected .exe size | vs. 150 MB target |
|---|---:|---:|
| Baseline | 209 MB | +59 |
| Tier A only | ~177 MB | +27 |
| Tier A + B | **~149 MB** | **-1 (hit)** |
| Tier A + B + C1 (UPX) | ~137 MB | -13 (comfortable) |
| Tier A + B + C4 (drop sklearn) | ~119 MB | -31 (lots of headroom) |

**Recommended Fase 1 ship:** Tier A + Tier B. Lands at ~149 MB, meets target, zero code changes outside the .spec, requires one clean-VM smoke test.

### Current status (as of 2026-11-02)

- Tier A: **MERGED as default** — measured 194.53 MB (-14.58 MB vs 209.11 baseline; below the -32 MB projection because llvmlite.dll's 115 MB dominates the pack).
- Tier B: **GATED behind `CHAC_AGGRESSIVE_SHRINK=1`** env var in `build.spec`. Default build is unchanged. See `build/README.md` for how to run the aggressive build and the required clean-VM smoke-test protocol before promoting to default.
- Tier C1 (UPX): deferred.
- Strip of `llvmlite.dll`: investigated and ruled out — the PyPI MSVC wheel is already symbol-free (debug directory holds only a 1 KB POGO marker; no DWARF or CodeView). GNU-style strip saves ~1 KB.

---

## Validation protocol for Tier B (before merge)

1. Apply exclusions.
2. Rebuild: `.\build\make_release.ps1`.
3. Measure `dist/CloneHeroAutoChart.exe` size.
4. On a clean Windows VM with no Python installed:
   - Double-click .exe → GUI opens, no missing-module traceback.
   - Run one full pipeline: paste a YouTube URL → charter completes → `.chart` + `.ini` written.
5. If `ImportError` appears for an excluded module: binary-split the Tier B list to find the offender; move it from `excludes` to a kept set.

---

## Items explicitly NOT in Fase 1

- UPX (Tier C1) — reserve for Fase 2 once baseline-150 is confirmed.
- Auto-updater and signing — separate workstream per role charter.
- numba removal (Tier C3) — needs baker approval on perf trade-off.
- sklearn removal via librosa patch (Tier C4) — Fase 2 if we need more headroom.
