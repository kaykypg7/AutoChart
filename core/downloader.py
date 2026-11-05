import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from core.config import Settings, _appdata_dir, ffmpeg_path
from core.errors import DownloadError, FFmpegMissing


log = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    audio_path: Path
    title: str
    uploader: str
    duration_s: int
    thumbnail_url: Optional[str]
    info: dict
    # jobs.json record id — exposed so pipeline can attach per-stage timings
    # to the same job record after download_audio returns.
    job_id: Optional[str] = None


ProgressCB = Callable[[float, str], None]


_JOBS_LOCK = Lock()


def _jobs_path() -> Path:
    return _appdata_dir() / "jobs.json"


def _load_jobs_file() -> Dict[str, Any]:
    p = _jobs_path()
    if not p.exists():
        return {"jobs": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "jobs" not in data:
            return {"jobs": []}
        return data
    except (json.JSONDecodeError, OSError, ValueError):
        return {"jobs": []}


def _save_jobs_file(data: Dict[str, Any]) -> None:
    p = _jobs_path()
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _register_job(url: str, out_dir: Path, meta: Optional[Dict[str, Any]] = None) -> str:
    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "url": url,
        "out_dir": str(out_dir),
        "status": "pending",
        "error": None,
        "created_at": time.time(),
        "finished_at": None,
        "meta": meta or {},
    }
    with _JOBS_LOCK:
        data = _load_jobs_file()
        data["jobs"].append(record)
        _save_jobs_file(data)
    return job_id


def _mark_job(job_id: str, status: str, error: Optional[str] = None) -> None:
    with _JOBS_LOCK:
        data = _load_jobs_file()
        for rec in data["jobs"]:
            if rec.get("id") == job_id:
                rec["status"] = status
                rec["error"] = error
                rec["finished_at"] = time.time()
                break
        _save_jobs_file(data)


def lyrics_cache_get(key: str) -> Optional[Dict[str, Any]]:
    """
    Return a cached lyrics record for `key` ("<artist>|<title>" normalized),
    or None on miss. Cache lives inside jobs.json under the "lyrics_cache"
    section so it shares a single APPDATA footprint with the job table and
    clears together with it.
    """
    if not key:
        return None
    with _JOBS_LOCK:
        data = _load_jobs_file()
        cache = data.get("lyrics_cache") or {}
        rec = cache.get(key)
    return rec if isinstance(rec, dict) else None


def lyrics_cache_put(key: str, record: Dict[str, Any]) -> None:
    """Write/overwrite a cache record. Silent no-op on empty key."""
    if not key:
        return
    with _JOBS_LOCK:
        data = _load_jobs_file()
        cache = data.get("lyrics_cache")
        if not isinstance(cache, dict):
            cache = {}
        cache[key] = record
        data["lyrics_cache"] = cache
        _save_jobs_file(data)


def update_job(job_id: str, **fields) -> None:
    """
    Merge arbitrary fields into a job record. Used by pipeline.py to attach
    per-stage timings (stage_durations_ms) and the final pipeline stage/outcome
    to the same jobs.json record that download_audio created.

    Unknown job_id is a silent no-op — a crash between register and update
    must never bring down the caller.
    """
    if not job_id:
        return
    with _JOBS_LOCK:
        data = _load_jobs_file()
        for rec in data["jobs"]:
            if rec.get("id") == job_id:
                rec.update(fields)
                break
        _save_jobs_file(data)


def load_pending_jobs() -> List[Dict[str, Any]]:
    """
    Return all job records that never completed. Call after startup to decide
    whether to offer a resume prompt. The caller is responsible for either
    re-queueing the URLs or clearing them (see clear_finished_jobs / mark_resumed).
    """
    with _JOBS_LOCK:
        data = _load_jobs_file()
    return [rec for rec in data.get("jobs", []) if rec.get("status") == "pending"]


def clear_finished_jobs() -> int:
    """Prune done/failed records. Returns how many were dropped. Pending survives."""
    with _JOBS_LOCK:
        data = _load_jobs_file()
        before = len(data.get("jobs", []))
        data["jobs"] = [r for r in data.get("jobs", []) if r.get("status") == "pending"]
        _save_jobs_file(data)
        return before - len(data["jobs"])


def _probe_ffmpeg() -> None:
    ff = ffmpeg_path()
    if not (ff == "ffmpeg" or Path(ff).exists()):
        raise FFmpegMissing(f"ffmpeg not found at {ff}")


def _cookies_opt(browser: str) -> Optional[tuple]:
    """
    yt-dlp accepts cookiesfrombrowser as a tuple (browser[, profile[, keyring[, container]]]).
    Firefox works out of the box. Chrome / Edge / Brave hit a wall on recent Chromium
    cookie encryption (DPAPI + app-bound encryption since Chrome 127+) so we warn and
    disable rather than crash the pipeline.
    """
    b = (browser or "").lower().strip()
    if not b or b == "none":
        return None
    if b in {"chrome", "chromium", "edge", "brave", "opera", "vivaldi"}:
        return None  # intentionally skipped; see docstring
    if b in {"firefox", "librewolf", "whale", "safari"}:
        return (b,)
    return (b,)


def download_audio(url: str, out_dir: Path, quality: int = 5,
                   progress_cb: Optional[ProgressCB] = None,
                   browser_cookies: str = "firefox",
                   cookies_txt_path: str = "",
                   proxy_url: str = "",
                   job_meta: Optional[Dict[str, Any]] = None) -> DownloadResult:
    _probe_ffmpeg()
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / "%(id)s.%(ext)s")

    job_id = _register_job(url, out_dir, job_meta)

    # Mutable byte counter shared with the progress hook so attempt-level logs can
    # report bytes seen even when the attempt crashes mid-stream.
    bytes_counter = {"downloaded": 0}

    def _hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            bytes_counter["downloaded"] = got
            if progress_cb:
                pct = (got / total) if total else 0.0
                progress_cb(pct, f"Downloading {int(pct*100)}%")
        elif d.get("status") == "finished":
            bytes_counter["downloaded"] = d.get("downloaded_bytes") or bytes_counter["downloaded"]
            if progress_cb:
                progress_cb(1.0, "Download finished, transcoding...")

    # Permissive format: "ba*" matches HLS/DASH/progressive audio streams.
    base_opts = {
        "format": "bestaudio*/bestaudio/best",
        "outtmpl": tmpl,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": ffmpeg_path(),
        "progress_hooks": [_hook],
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "vorbis",
            "preferredquality": str(quality),
        }],
        "noplaylist": True,
        "nocheckcertificate": True,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "geo_bypass": True,
        # Order of YouTube internal player clients to query. Different clients
        # expose different format manifests; iterating them improves the chance
        # of resolving a usable audio stream when one returns an empty format list.
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web_safari", "mweb", "web"],
            },
        },
    }
    if proxy_url:
        base_opts["proxy"] = proxy_url
    browser_cookies_tuple = _cookies_opt(browser_cookies)
    txt_cookies_valid = bool(cookies_txt_path) and Path(cookies_txt_path).is_file()

    # Fallback chain, in priority order:
    #   1. user-supplied cookies.txt file (wins when present — bypasses browser decryption entirely)
    #   2. live browser session cookies (firefox-class only)
    #   3. anonymous
    #   4. android-only player client (last resort for format resolution)
    attempts = []
    if txt_cookies_valid:
        with_txt = dict(base_opts)
        with_txt["cookiefile"] = cookies_txt_path
        attempts.append(("cookies-txt", with_txt))
    if browser_cookies_tuple:
        with_browser = dict(base_opts)
        with_browser["cookiesfrombrowser"] = browser_cookies_tuple
        attempts.append(("with-cookies", with_browser))
    attempts.append(("no-cookies", base_opts))
    android_only = dict(base_opts)
    android_only["extractor_args"] = {"youtube": {"player_client": ["android"]}}
    attempts.append(("android-only", android_only))

    last_err: Optional[Exception] = None
    info = None
    try:
        rate_limit_hits = 0
        for idx, (label, opts) in enumerate(attempts):
            # Exponential backoff only after a 429 — 2s, 4s, 8s, 16s. Doubles per
            # consecutive rate-limit hit so YouTube's edge gets room to cool down
            # before the next attempt swaps cookies/player clients.
            if rate_limit_hits > 0:
                wait = 2 ** rate_limit_hits
                log.info("downloader.backoff attempt=%s wait_s=%d", label, wait)
                time.sleep(wait)

            bytes_counter["downloaded"] = 0
            start = time.monotonic()
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                duration = time.monotonic() - start
                log.info(
                    "downloader.attempt label=%s outcome=ok duration_s=%.2f bytes=%d url=%s",
                    label, duration, bytes_counter["downloaded"], url,
                )
                break
            except Exception as e:
                duration = time.monotonic() - start
                msg = str(e).lower()
                is_429 = "http error 429" in msg or "too many requests" in msg
                is_retryable = is_429 or any(k in msg for k in (
                    "format is not available", "requested format",
                    "http error 403",
                    "unable to extract", "players",
                ))
                log.warning(
                    "downloader.attempt label=%s outcome=%s duration_s=%.2f bytes=%d err=%r",
                    label, "429" if is_429 else ("retryable" if is_retryable else "fatal"),
                    duration, bytes_counter["downloaded"], str(e),
                )
                last_err = e
                if not is_retryable:
                    raise DownloadError(f"yt-dlp failed [{label}]: {e}") from e
                rate_limit_hits = rate_limit_hits + 1 if is_429 else 0
                continue

        if info is None:
            raise DownloadError(f"yt-dlp failed after {len(attempts)} attempts: {last_err}")

        vid = info.get("id")
        ogg_path = out_dir / f"{vid}.ogg"
        if not ogg_path.exists():
            candidates = list(out_dir.glob(f"{vid}.*"))
            if candidates:
                ogg_path = candidates[0]
            else:
                raise DownloadError(f"No audio file produced for {url}")

        result = DownloadResult(
            audio_path=ogg_path,
            title=info.get("title") or "",
            uploader=info.get("uploader") or info.get("channel") or "",
            duration_s=int(info.get("duration") or 0),
            thumbnail_url=info.get("thumbnail"),
            info=info,
            job_id=job_id,
        )
    except Exception as e:
        _mark_job(job_id, "failed", error=str(e))
        raise
    else:
        _mark_job(job_id, "done")
        return result


def download_video(url: str, out_dir: Path, max_height: int = 1080,
                   progress_cb: Optional[ProgressCB] = None,
                   browser_cookies: str = "firefox",
                   cookies_txt_path: str = "",
                   proxy_url: str = "") -> Path:
    """
    Download video stream (H.264/MP4) for Clone Hero background playback.
    Returns the path to the downloaded MP4 file in out_dir.
    """
    _probe_ffmpeg()
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / "%(id)s_video.%(ext)s")

    bytes_counter = {"downloaded": 0}

    def _hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            bytes_counter["downloaded"] = got
            if progress_cb:
                pct = (got / total) if total else 0.0
                progress_cb(pct, f"Downloading video {int(pct*100)}%")
        elif d.get("status") == "finished":
            bytes_counter["downloaded"] = d.get("downloaded_bytes") or bytes_counter["downloaded"]
            if progress_cb:
                progress_cb(1.0, "Video download finished, processing...")

    fmt = (
        f"bestvideo[height<={max_height}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={max_height}][vcodec^=avc1]+bestaudio/"
        f"bestvideo[height<={max_height}]+bestaudio/"
        f"best[height<={max_height}][ext=mp4]/"
        f"best[height<={max_height}]/best"
    )

    base_opts = {
        "format": fmt,
        "outtmpl": tmpl,
        "merge_output_format": "mp4",
        "remux_video": "mp4",
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": ffmpeg_path(),
        "progress_hooks": [_hook],
        "noplaylist": True,
        "nocheckcertificate": True,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "geo_bypass": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web_safari", "mweb", "web"],
            },
        },
    }
    if proxy_url:
        base_opts["proxy"] = proxy_url
    browser_cookies_tuple = _cookies_opt(browser_cookies)
    txt_cookies_valid = bool(cookies_txt_path) and Path(cookies_txt_path).is_file()

    attempts = []
    if txt_cookies_valid:
        with_txt = dict(base_opts)
        with_txt["cookiefile"] = cookies_txt_path
        attempts.append(("cookies-txt", with_txt))
    if browser_cookies_tuple:
        with_browser = dict(base_opts)
        with_browser["cookiesfrombrowser"] = browser_cookies_tuple
        attempts.append(("with-cookies", with_browser))
    attempts.append(("no-cookies", base_opts))
    android_only = dict(base_opts)
    android_only["extractor_args"] = {"youtube": {"player_client": ["android"]}}
    attempts.append(("android-only", android_only))

    last_err: Optional[Exception] = None
    info = None
    rate_limit_hits = 0
    for idx, (label, opts) in enumerate(attempts):
        if rate_limit_hits > 0:
            wait = 2 ** rate_limit_hits
            log.info("downloader.backoff (video) attempt=%s wait_s=%d", label, wait)
            time.sleep(wait)

        bytes_counter["downloaded"] = 0
        start = time.monotonic()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            duration = time.monotonic() - start
            log.info(
                "downloader.video.attempt label=%s outcome=ok duration_s=%.2f bytes=%d url=%s",
                label, duration, bytes_counter["downloaded"], url,
            )
            break
        except Exception as e:
            duration = time.monotonic() - start
            msg = str(e).lower()
            is_429 = "http error 429" in msg or "too many requests" in msg
            is_retryable = is_429 or any(k in msg for k in (
                "format is not available", "requested format",
                "http error 403",
                "unable to extract", "players",
            ))
            log.warning(
                "downloader.video.attempt label=%s outcome=%s duration_s=%.2f bytes=%d err=%r",
                label, "429" if is_429 else ("retryable" if is_retryable else "fatal"),
                duration, bytes_counter["downloaded"], str(e),
            )
            last_err = e
            if not is_retryable:
                raise DownloadError(f"yt-dlp video failed [{label}]: {e}") from e
            rate_limit_hits = rate_limit_hits + 1 if is_429 else 0
            continue

    if info is None:
        raise DownloadError(f"yt-dlp video failed after {len(attempts)} attempts: {last_err}")

    vid = info.get("id")
    mp4_path = out_dir / f"{vid}_video.mp4"
    if not mp4_path.exists():
        candidates = [c for c in out_dir.glob(f"{vid}_video.*") if not c.name.endswith(".ogg")]
        if candidates:
            mp4_path = candidates[0]
        else:
            candidates_all = [c for c in out_dir.glob("*.mp4") if not c.name.endswith(".ogg")]
            if candidates_all:
                mp4_path = candidates_all[0]
            else:
                raise DownloadError(f"No video file produced for {url}")

    if mp4_path.suffix.lower() != ".mp4":
        target_mp4 = mp4_path.with_suffix(".mp4")
        import subprocess
        cmd = [ffmpeg_path(), "-y", "-i", str(mp4_path), "-c:v", "copy", "-c:a", "copy", str(target_mp4)]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0 and target_mp4.exists():
            try: mp4_path.unlink()
            except OSError: pass
            mp4_path = target_mp4

    return mp4_path

