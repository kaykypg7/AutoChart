import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


def _appdata_dir() -> Path:
    base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    d = Path(base) / "CloneHeroAutoChart"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _local_appdata_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    d = Path(base) / "CloneHeroAutoChart"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource_path(rel: str) -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "resources" / rel
    return Path(__file__).resolve().parent.parent / "resources" / rel


def ffmpeg_path() -> str:
    p = resource_path("ffmpeg.exe")
    if p.exists():
        return str(p)
    return "ffmpeg"


def ffprobe_path() -> str:
    p = resource_path("ffprobe.exe")
    if p.exists():
        return str(p)
    return "ffprobe"


@dataclass
class Settings:
    output_folder: str = r"C:\Users\Kayky\Documents\Clone Hero\Songs"
    charter: str = "autochart"
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    audio_quality: int = 5
    include_drums: bool = True
    open_folder_after_chart: bool = False
    manual_offset_ms: int = 0
    folder_suffix: str = " (auto)"
    browser_cookies: str = "firefox"   # firefox|chrome|edge|brave|none  (chrome breaks on modern cookie encryption)
    cookies_txt_path: str = ""         # absolute path to a Netscape-format cookies.txt; wins over browser_cookies when set
    proxy_url: str = ""                # proxy URL (http://user:pass@host:port, socks5://host:port); forwarded to yt-dlp as "proxy"
    enable_lyrics: bool = True         # when True, pipeline fetches synced lyrics from LRCLIB and bakes them into [Events]
    download_video: bool = False       # when True, pipeline downloads video.mp4 background via yt-dlp
    video_start_time_ms: int = 0       # offset in ms for video playback in song.ini (video_start_time)
    genre_profile: str = "auto"        # auto|trap|rock|pop|metal|default (v2.0 genre profile)
    manual_bpm: float = 0.0            # when > 0, overrides tempo detection with fixed BPM

    @classmethod
    def path(cls) -> Path:
        return _appdata_dir() / "config.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            known = {f for f in cls.__dataclass_fields__}
            return cls(**{k: v for k, v in data.items() if k in known})
        except (json.JSONDecodeError, TypeError, ValueError):
            return cls()

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def setup_runtime_env() -> None:
    """Must run before any numba/librosa import when frozen."""
    cache_dir = _local_appdata_dir() / "numba_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("NUMBA_CACHE_DIR", str(cache_dir))
    ff = resource_path("ffmpeg.exe")
    if ff.exists():
        os.environ["PATH"] = str(ff.parent) + os.pathsep + os.environ.get("PATH", "")
