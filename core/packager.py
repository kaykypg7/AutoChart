import re
import shutil
from pathlib import Path

from core.errors import WriteError

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_folder_name(name: str, max_len: int = 120) -> str:
    s = _INVALID.sub("_", name).strip(". ")
    s = re.sub(r"\s+", " ", s)
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s or "untitled"


_SUFFIX_RE = re.compile(
    r"\s*[\[\(](?:official\s*(?:video|audio|music\s*video|hd\s*video|visualizer|lyric\s*video)|"
    r"music\s*video|audio|lyric\s*video|visualizer|hd|4k)[\]\)]\s*",
    re.IGNORECASE,
)


def clean_song_metadata(raw_artist: str, raw_title: str) -> tuple:
    """
    Clean raw artist and title (e.g. from YouTube):
    - Strip suffixes like [Official Video], (Official Audio), (Visualizer), etc.
    - If title is formatted as "Artist - Title", extract the true artist and title.
    - Remove duplicate artist prefixing.
    """
    artist = raw_artist.strip()
    title = raw_title.strip()

    title = _SUFFIX_RE.sub("", title).strip()

    if " - " in title:
        parts = title.split(" - ", 1)
        lead, tail = parts[0].strip(), parts[1].strip()
        if not artist or artist.lower() == "unknown" or lead.lower() == artist.lower():
            artist = lead
            title = tail
        elif tail and not lead:
            title = tail

    title = _SUFFIX_RE.sub("", title).strip()

    if artist and title.lower().startswith(f"{artist.lower()} - "):
        title = title[len(artist) + 3:].strip()

    return artist or "Unknown", title or "Unknown"


def ensure_dir(songs_root: Path, folder_name: str) -> Path:
    songs_root.mkdir(parents=True, exist_ok=True)
    target = songs_root / folder_name
    if target.exists():
        try:
            if not any(target.iterdir()):
                return target
        except OSError:
            pass
        n = 2
        while (songs_root / f"{folder_name} v{n}").exists():
            v_dir = songs_root / f"{folder_name} v{n}"
            try:
                if not any(v_dir.iterdir()):
                    return v_dir
            except OSError:
                pass
            n += 1
        target = songs_root / f"{folder_name} v{n}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def place_audio(src: Path, folder: Path, stream_name: str = "song.ogg") -> Path:
    dst = folder / stream_name
    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        raise WriteError(f"Could not move audio to {dst}: {e}") from e
    return dst


def place_video(src: Path, folder: Path, stream_name: str = "video.mp4") -> Path:
    dst = folder / stream_name
    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        raise WriteError(f"Could not move video to {dst}: {e}") from e
    return dst

