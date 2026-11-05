from dataclasses import dataclass
from typing import List, Optional

from core.config import ffmpeg_path
from core.downloader import _cookies_opt


@dataclass
class SearchResult:
    url: str
    title: str
    uploader: str
    duration_s: int
    thumbnail: Optional[str] = None


def _base_opts(browser_cookies: str = "firefox") -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "ffmpeg_location": ffmpeg_path(),
        "nocheckcertificate": True,
    }
    cookies = _cookies_opt(browser_cookies)
    if cookies:
        opts["cookiesfrombrowser"] = cookies
    return opts


def yt_search(query: str, limit: int = 10, browser_cookies: str = "firefox") -> List[SearchResult]:
    import yt_dlp
    out: List[SearchResult] = []
    with yt_dlp.YoutubeDL(_base_opts(browser_cookies)) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        for entry in info.get("entries", []) or []:
            if not entry:
                continue
            url = entry.get("url") or entry.get("webpage_url")
            if url and not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            out.append(SearchResult(
                url=url or "",
                title=entry.get("title") or "(no title)",
                uploader=entry.get("uploader") or entry.get("channel") or "",
                duration_s=int(entry.get("duration") or 0),
                thumbnail=entry.get("thumbnail"),
            ))
    return out


def yt_playlist(url: str, browser_cookies: str = "firefox") -> List[SearchResult]:
    import yt_dlp
    out: List[SearchResult] = []
    with yt_dlp.YoutubeDL(_base_opts(browser_cookies)) as ydl:
        info = ydl.extract_info(url, download=False)
        for entry in info.get("entries", []) or []:
            if not entry:
                continue
            vurl = entry.get("url") or entry.get("webpage_url")
            if vurl and not vurl.startswith("http"):
                vurl = f"https://www.youtube.com/watch?v={vurl}"
            out.append(SearchResult(
                url=vurl or "",
                title=entry.get("title") or "(no title)",
                uploader=entry.get("uploader") or entry.get("channel") or "",
                duration_s=int(entry.get("duration") or 0),
                thumbnail=entry.get("thumbnail"),
            ))
    return out
