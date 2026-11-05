# PyInstaller spec for CloneHeroAutoChart
# Build with:   pyinstaller build/build.spec
# Output:       dist/CloneHeroAutoChart.exe

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_all

PROJECT_ROOT = Path(SPECPATH).resolve().parent  # build/ -> project root

# Exclude only genuine test suites, docs, and unused CUDA.
# Functional libraries (scipy.constants, scipy.integrate, scipy._external,
# narwhals, sklearn submodules) MUST remain bundled, as scipy.signal,
# scipy.stats, and librosa.segment require them at runtime.
_EXCLUDED_PREFIXES = (
    "numba.tests",
    "numba.cuda",
    "llvmlite.tests",
    "scipy.signal.tests",
    "scipy.sparse.tests",
    "scipy.linalg.tests",
    "scipy.special.tests",
    "scipy.interpolate.tests",
    "scipy.fft.tests",
    "scipy.ndimage.tests",
    "scipy._lib.tests",
    "scipy.stats.tests",
    "scipy.optimize.tests",
    "scipy.spatial.tests",
    "sklearn.tests",
    "sklearn.utils.tests",
)

def _keep(mod: str) -> bool:
    return not any(mod == p or mod.startswith(p + ".") for p in _EXCLUDED_PREFIXES)

hiddenimports = []
hiddenimports += [m for m in collect_submodules("librosa") if _keep(m)]
hiddenimports += [m for m in collect_submodules("numba") if _keep(m)]
hiddenimports += [m for m in collect_submodules("llvmlite") if _keep(m)]
hiddenimports += [m for m in collect_submodules("scipy") if _keep(m)]
hiddenimports += [m for m in collect_submodules("sklearn") if _keep(m)]
hiddenimports += [
    "audioread",
    "soundfile",
    "yt_dlp",
    "spotipy",
    "PIL._tkinter_finder",
    "unittest",
    "unittest.case",
]

def _path_keep(entry) -> bool:
    dest = entry[1].replace("\\", "/").lower()
    for p in _EXCLUDED_PREFIXES:
        needle = "/" + p.replace(".", "/") + "/"
        if needle in "/" + dest + "/" or ("/" + dest).endswith(needle.rstrip("/")):
            return False
    return True

datas = []
binaries = []

numba_datas, numba_binaries, numba_hidden = collect_all("numba")
llvm_datas, llvm_binaries, llvm_hidden = collect_all("llvmlite")
ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")

datas += [d for d in numba_datas + llvm_datas + ctk_datas if _path_keep(d)]
binaries += [b for b in numba_binaries + llvm_binaries + ctk_binaries if _path_keep(b)]
hiddenimports += [h for h in (numba_hidden + llvm_hidden + ctk_hidden) if _keep(h)]

resources_dir = PROJECT_ROOT / "resources"
if (resources_dir / "ffmpeg.exe").exists():
    datas.append((str(resources_dir / "ffmpeg.exe"), "resources"))
if (resources_dir / "ffprobe.exe").exists():
    datas.append((str(resources_dir / "ffprobe.exe"), "resources"))
if (resources_dir / "icon.ico").exists():
    datas.append((str(resources_dir / "icon.ico"), "resources"))

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter.test",
        "pytest",
        "matplotlib",
        "IPython",
        "notebook",
        "jupyter",
        "numba.tests",
        "numba.cuda",
        "llvmlite.tests",
        "scipy.signal.tests",
        "scipy.sparse.tests",
        "scipy.linalg.tests",
        "scipy.special.tests",
        "scipy.interpolate.tests",
        "scipy.fft.tests",
        "scipy.ndimage.tests",
        "scipy._lib.tests",
        "scipy.stats.tests",
        "scipy.optimize.tests",
        "scipy.spatial.tests",
        "sklearn.tests",
        "sklearn.utils.tests",
        "pycparser.lextab",
        "pycparser.yacctab",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon_file = str(resources_dir / "icon.ico") if (resources_dir / "icon.ico").exists() else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CloneHeroAutoChart",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    windowed=True,
    disable_windowed_traceback=False,
    icon=icon_file,
)
