from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class IniMeta:
    name: str
    artist: str
    album: str = ""
    genre: str = "Custom"
    year: str = ""
    song_length_ms: int = 0
    charter: str = "autochart"
    diff_guitar: int = 3
    diff_bass: int = -1
    diff_drums: int = -1
    diff_keys: int = -1
    preview_start_ms: int = 0
    icon: int = 0
    loading_phrase: str = "Auto-generated - expect to tune in Moonscraper"
    delay: int = 0
    video_start_time: Optional[int] = None


def write_ini(path: Path, m: IniMeta) -> Path:
    lines = [
        "[Song]",
        f"name = {m.name}",
        f"artist = {m.artist}",
        f"album = {m.album}",
        f"genre = {m.genre}",
        f"year = {m.year}",
        f"song_length = {m.song_length_ms}",
        f"charter = {m.charter}",
        "diff_band = 3",
        f"diff_guitar = {m.diff_guitar}",
        f"diff_bass = {m.diff_bass}",
        f"diff_drums = {m.diff_drums}",
        f"diff_keys = {m.diff_keys}",
        f"preview_start_time = {m.preview_start_ms}",
        f"icon = {m.icon}",
        f"loading_phrase = {m.loading_phrase}",
        f"delay = {m.delay}",
    ]
    if m.video_start_time is not None:
        lines.append(f"video_start_time = {m.video_start_time}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
