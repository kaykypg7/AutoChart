# build/

PyInstaller build pipeline for `CloneHeroAutoChart.exe`.

## Files

- `make_release.ps1` — top-level build script (creates venv, installs deps, downloads ffmpeg, runs PyInstaller)
- `build.spec` — PyInstaller spec: hidden imports, data/binary collection, exclusion filters
- `shrink_plan.md` — size-reduction plan with Tier A/B/C breakdown and estimates
- `build/` and `dist/` (gitignored) — PyInstaller working trees; `dist/CloneHeroAutoChart.exe` is the artifact

## Standard build (default: TierA excludes only)

```powershell
.\build\make_release.ps1
```

- Current size: **~194.5 MB**
- Zero-risk excludes applied: numba tests + CUDA, scipy subpackages not used by librosa, unused sklearn modules, narwhals, test modules across scipy/sklearn/llvmlite.
- Safe for every ship — exercised in-process at build time.

## Aggressive build (TierB behind env-var gate)

Projected size: **~166 MB**. Reaches the roadmap target of ≤150 MB when combined with a future UPX pass.

```powershell
$env:CHAC_AGGRESSIVE_SHRINK = "1"
.\build\make_release.ps1
```

The spec prints `>>> build.spec: CHAC_AGGRESSIVE_SHRINK=1 — TierB excludes ACTIVE` when the gate fires. Default (unset or `="0"`) stays on TierA.

### What TierB additionally excludes

| Module | Rationale | Risk |
|---|---|---|
| `scipy.stats` | Not called by our code or by librosa funcs we touch. | `sklearn.metrics` can pull `scipy.stats.rankdata` on some paths. |
| `scipy.optimize` | Not called by us. sklearn.cluster with `linkage="ward"` doesn't execute optimize. | Transitive touches from `numba.np.ufunc` or sklearn init possible. |
| `scipy.spatial` | Not called directly. `grid_to_graph` returns sparse without distance work. | `AgglomerativeClustering(connectivity=...)` with `precomputed` metric would need it. |
| `sklearn.preprocessing` | Not called in our segment path. | `sklearn.base` init can reference `StandardScaler` indirectly on some versions. |

### REQUIRED validation before merging TierB as the default

A clean Windows VM (no Python installed, no project dev artifacts) must pass **all** steps:

1. Build with `CHAC_AGGRESSIVE_SHRINK=1`.
2. Copy only `dist/CloneHeroAutoChart.exe` to the clean VM.
3. Double-click the .exe → GUI opens, no missing-module traceback appears.
4. Full pipeline: paste one YouTube URL → charter completes to `.chart` + `.ini` on disk.
5. Run on a second song that exercises `librosa.segment.agglomerative` section detection.
6. If any `ImportError` surfaces: binary-split `_TIER_B_PREFIXES` in `build.spec` to isolate the offender, move it from the TierB tuple to a kept list, rebuild.

Only when steps 1–5 pass clean on the VM should the env-var gate be removed and TierB promoted to default.

## Rebuilding after dependency changes

The spec filters `_EXCLUDED_PREFIXES` from `collect_submodules()` and `collect_all()` results before Analysis sees them. If `requirements.txt` adds new fat (e.g. a new sklearn minor version with another ML submodule), update `_TIER_A_PREFIXES` or `_TIER_B_PREFIXES` in `build.spec` accordingly and re-measure.

## Measuring

```powershell
$f = Get-Item .\dist\CloneHeroAutoChart.exe
"{0:N2} MB" -f ($f.Length / 1MB)
```

Reference points:
- Pre-shrink baseline: **209.11 MB**
- TierA (default now): **194.53 MB**
- TierA + TierB (gated): **~166 MB** projected, needs smoke test
- TierA + TierB + UPX (Tier C1, not yet implemented): **~149 MB** projected
