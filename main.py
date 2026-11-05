"""
Clone Hero Auto-Charter — entry point.

Setup order matters:
  1. Ensure project root is on sys.path (so `core` and `gui` resolve both
     when run as a script and when frozen by PyInstaller).
  2. Call setup_runtime_env() BEFORE any librosa/numba import so NUMBA_CACHE_DIR
     is honored (MEIPASS extraction dir is read-only on frozen builds).
  3. Launch the CTk GUI.
"""
import logging
import os
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    if getattr(sys, "frozen", False):
        here = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        here = Path(__file__).resolve().parent
    p = str(here)
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> None:
    _bootstrap_path()
    from core.config import setup_runtime_env, _local_appdata_dir
    setup_runtime_env()

    log_file = _local_appdata_dir() / "app.log"
    handlers = [logging.FileHandler(str(log_file), mode="a", encoding="utf-8")]
    if sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=handlers,
    )
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

    from gui.app import run
    run()


if __name__ == "__main__":
    main()
