"""GitHub Releases update checker.

Runs on a daemon thread so the GUI never blocks. Hits the public
releases API (no auth, 60 req/hr/IP limit is fine for a desktop app
that checks once per launch), compares semver strings, and returns a
small dataclass the UI can inspect.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from typing import Callable, Optional

import requests

from core.version import __version__


GITHUB_RELEASES_API = "https://api.github.com/repos/kaykypg7/AutoChart/releases/latest"
RELEASES_PAGE_URL = "https://github.com/kaykypg7/AutoChart/releases/latest"
HTTP_TIMEOUT_S = 6.0


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    is_newer: bool
    html_url: str
    installer_url: Optional[str]
    portable_url: Optional[str]


def _parse_semver(s: str) -> tuple:
    """Lenient semver parser: 'v1.2.3' -> (1, 2, 3). Non-numeric tails ignored."""
    s = s.lstrip("vV ").strip()
    parts = re.split(r"[.\-+]", s)
    out = []
    for p in parts[:3]:
        m = re.match(r"^(\d+)", p)
        out.append(int(m.group(1)) if m else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _is_newer(latest: str, current: str) -> bool:
    try:
        return _parse_semver(latest) > _parse_semver(current)
    except Exception:
        return False


def fetch_latest() -> Optional[UpdateInfo]:
    """Blocking call. Returns None on any network / parse failure (never raises)."""
    try:
        r = requests.get(GITHUB_RELEASES_API, timeout=HTTP_TIMEOUT_S,
                         headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        data = r.json()
        tag = str(data.get("tag_name") or "").strip()
        if not tag:
            return None
        installer = None
        portable = None
        for asset in data.get("assets") or []:
            name = (asset.get("name") or "").lower()
            url = asset.get("browser_download_url")
            if not url:
                continue
            if name.endswith(".exe") and "setup" in name:
                installer = url
            elif name.endswith(".zip") and "portable" in name:
                portable = url
        return UpdateInfo(
            current=__version__,
            latest=tag,
            is_newer=_is_newer(tag, __version__),
            html_url=data.get("html_url") or RELEASES_PAGE_URL,
            installer_url=installer,
            portable_url=portable,
        )
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def check_async(on_result: Callable[[Optional[UpdateInfo]], None]) -> None:
    """Fire the check on a daemon thread. on_result is invoked on the worker
    thread — the GUI caller must marshal back to Tk via .after(0, ...)."""
    def _worker():
        info = fetch_latest()
        try:
            on_result(info)
        except Exception:
            pass
    t = threading.Thread(target=_worker, name="updater-check", daemon=True)
    t.start()
