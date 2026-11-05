"""Demucs htdemucs_6s wrapper — isolates the guitar stem from a mixed track.

Design notes
------------
- Lazy imports: `torch`, `torchaudio`, and `demucs.*` are only imported on
  first call, so the GUI launches instantly even when stem separation is
  disabled in settings.
- Model cache: the first `get_model("htdemucs_6s")` call downloads ~85MB
  of model weights into torch's hub cache (%USERPROFILE%\\.cache\\torch\\hub
  on Windows). Subsequent calls are offline and fast (~1s to load).
- CPU-only path: we force `device="cpu"` so the bundled app runs on any
  machine without needing CUDA. A 3-minute song takes ~2-4 minutes on a
  mid-range CPU. GPU users still get CPU here because PyInstaller-bundled
  torch ships without CUDA wheels.
- Output path: separated WAV lands in a stems/ subdir next to the source
  audio so re-running on the same song can short-circuit (see cache hit
  check).

Public surface:
    separate_guitar_stem(audio_path, progress_cb=None) -> Path
        Returns the path to <audio_path>.stems/guitar.wav
    is_available() -> bool
        Lightweight check (no model load) for whether the dep is installed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional
import warnings

import numpy as np


def is_available() -> bool:
    """True iff demucs + torch are importable. No model load, no download."""
    try:
        import demucs  # noqa: F401
        import torch   # noqa: F401
        return True
    except ImportError:
        return False


# Module-level model cache — avoid re-loading on every song in a batch.
_MODEL_CACHE = None


def _load_model():
    """Lazy-import demucs and cache the model (~85MB weights, 500MB RAM)."""
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    # Silence demucs' progress bars / warnings when we're the only consumer.
    warnings.filterwarnings("ignore", category=UserWarning, module="demucs")
    warnings.filterwarnings("ignore", category=FutureWarning)
    from demucs.pretrained import get_model
    model = get_model("htdemucs_6s")
    model.cpu().eval()
    _MODEL_CACHE = model
    return model


def _stems_dir_for(audio_path: Path) -> Path:
    return audio_path.with_suffix(audio_path.suffix + ".stems")


def separate_guitar_stem(
    audio_path: Path,
    progress_cb: Optional[Callable[[str], None]] = None,
    overlap: float = 0.1,
    split: bool = True,
) -> Optional[Path]:
    """Run htdemucs_6s on audio_path and write guitar.wav alongside it.

    Returns the Path to the guitar stem WAV, or None when the stem comes back
    silent (songs without real guitar — e.g. pure trap, piano ballads — produce
    a near-empty guitar stem; the caller then falls back to full-mix analysis).

    Cache: if guitar.wav already exists and is non-empty, skip separation.

    Raises ImportError if demucs/torch aren't installed. All other errors
    propagate as-is — the pipeline catches them and falls back to using the
    full mix for analysis.
    """
    audio_path = Path(audio_path)
    stems_dir = _stems_dir_for(audio_path)
    guitar_out = stems_dir / "guitar.wav"

    if guitar_out.exists() and guitar_out.stat().st_size > 1024:
        if progress_cb:
            progress_cb(f"Guitar stem cached: {guitar_out.name}")
        return guitar_out

    if progress_cb:
        progress_cb("Loading Demucs model (first run downloads ~85MB)...")

    import numpy as np
    import torch
    import librosa
    import soundfile as sf
    from demucs.apply import apply_model

    model = _load_model()
    target_sr = model.samplerate
    target_ch = model.audio_channels

    if progress_cb:
        progress_cb(f"Loading audio: {audio_path.name}")

    # librosa handles any format ffmpeg/soundfile can open — avoids torchaudio's
    # 2.11 torchcodec dependency. mono=False preserves stereo when the source
    # has it; mono files come back as 1D and we expand below.
    y, sr = librosa.load(str(audio_path), sr=target_sr, mono=False)
    if y.ndim == 1:
        # mono -> duplicate across required channel count
        y = np.stack([y] * target_ch, axis=0)
    elif y.shape[0] > target_ch:
        y = y[:target_ch]
    elif y.shape[0] < target_ch:
        # e.g. unexpected single-row 2D from a mono source
        y = np.repeat(y[:1], target_ch, axis=0)

    wav = torch.from_numpy(y.astype(np.float32)).unsqueeze(0)  # (1, C, T)

    if progress_cb:
        progress_cb("Separating stems (this is the slow step)...")

    with torch.no_grad():
        stems = apply_model(
            model, wav, split=split, overlap=overlap,
            device="cpu", progress=False,
        )
    # stems shape: (batch=1, sources, channels, samples)
    try:
        guitar_idx = model.sources.index("guitar")
    except ValueError:
        # Fall back to 'other' if the 6-stem model ever ships without guitar.
        guitar_idx = model.sources.index("other")

    guitar_wav = stems[0, guitar_idx].cpu().numpy()  # (C, T)

    # Stem-quality guard: when a song has no real guitar (e.g. pure trap / piano
    # ballad), htdemucs_6s's guitar stem comes back near-silent or leaks noise.
    # Drop the file if RMS is below a floor — the pipeline will then analyze the
    # full mix like it did before stem separation existed.
    rms = float(np.sqrt(np.mean(guitar_wav.astype(np.float64) ** 2)))
    if rms < 1e-3:
        if progress_cb:
            progress_cb(f"Guitar stem silent (RMS={rms:.5f}) — falling back to full mix.")
        return None

    # soundfile wants (T, C) for multi-channel writes.
    guitar_wav_tc = guitar_wav.T if guitar_wav.ndim == 2 else guitar_wav

    stems_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(guitar_out), guitar_wav_tc, target_sr, subtype="PCM_16")

    if progress_cb:
        progress_cb(f"Guitar stem written: {guitar_out.name} (RMS={rms:.3f})")
    return guitar_out
