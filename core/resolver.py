import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.errors import InputError


class InputKind(str, Enum):
    YT_VIDEO = "yt_video"
    YT_PLAYLIST = "yt_playlist"
    SPOTIFY_TRACK = "spotify_track"
    SPOTIFY_PLAYLIST = "spotify_playlist"
    SPOTIFY_ALBUM = "spotify_album"
    SEARCH_TEXT = "search_text"


@dataclass
class ResolvedInput:
    kind: InputKind
    raw: str
    id: Optional[str] = None


YT_VIDEO_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/)([A-Za-z0-9_-]{11})"
)
YT_PLAYLIST_RE = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")
SP_TRACK_RE = re.compile(r"open\.spotify\.com/track/([A-Za-z0-9]+)")
SP_PLAYLIST_RE = re.compile(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)")
SP_ALBUM_RE = re.compile(r"open\.spotify\.com/album/([A-Za-z0-9]+)")


def resolve(raw: str) -> ResolvedInput:
    text = raw.strip()
    if not text:
        raise InputError("Empty input.")

    m = SP_PLAYLIST_RE.search(text)
    if m:
        return ResolvedInput(InputKind.SPOTIFY_PLAYLIST, text, m.group(1))
    m = SP_ALBUM_RE.search(text)
    if m:
        return ResolvedInput(InputKind.SPOTIFY_ALBUM, text, m.group(1))
    m = SP_TRACK_RE.search(text)
    if m:
        return ResolvedInput(InputKind.SPOTIFY_TRACK, text, m.group(1))
    m = YT_PLAYLIST_RE.search(text)
    if m and "watch?v=" not in text:
        return ResolvedInput(InputKind.YT_PLAYLIST, text, m.group(1))
    m = YT_VIDEO_RE.search(text)
    if m:
        return ResolvedInput(InputKind.YT_VIDEO, text, m.group(1))
    if text.startswith(("http://", "https://")):
        raise InputError(f"Unrecognized URL: {text[:80]}")
    return ResolvedInput(InputKind.SEARCH_TEXT, text)
