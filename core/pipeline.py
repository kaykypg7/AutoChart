import logging
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core import art, charter, chart_writer, downloader, ini_writer, packager
from core.analyzer import analyze_audio
from core.charter import build_chart
from core.chart_writer import ChartMeta, write_chart
from core.config import Settings
from core.drum_transcriber import transcribe as transcribe_drums
from core.errors import AutoChartError, DownloadError
from core.ini_writer import IniMeta, write_ini
from core.lyrics import LyricsData, fetch_synced_lyrics
from core.genre_profiles import detect_genre
from core.spotify_client import TrackMeta


def _humanize_download_error(err: DownloadError) -> str:
    """Collapse yt-dlp's noisy chain into one line the UI can show the user."""
    raw = str(err).lower()
    if "http error 429" in raw or "too many requests" in raw:
        return "YouTube rate-limited this request. Try again in a few minutes, or set a proxy in Settings."
    if "http error 403" in raw or "sign in to confirm" in raw or "age-restricted" in raw:
        return "YouTube blocked this video (age-gated or bot-check). Export cookies.txt from a signed-in browser and set it in Settings."
    if "video unavailable" in raw or "private video" in raw or "removed" in raw:
        return "This video is unavailable (private, removed, or region-locked)."
    if "format is not available" in raw or "requested format" in raw:
        return "No usable audio stream for this video. YouTube didn't expose one for any player client."
    if "unable to extract" in raw or "players" in raw:
        return "YouTube extractor failed — the signature cipher changed. Update yt-dlp."
    if "ffmpeg" in raw:
        return "ffmpeg failed while transcoding. Verify resources/ffmpeg.exe is present and runnable."
    if "proxy" in raw and ("connection" in raw or "refused" in raw or "timed out" in raw):
        return "Proxy connection failed. Verify the proxy URL in Settings or clear it."
    # Fall back to the exception's own message, stripped of yt-dlp's "[yt-dlp failed [label]]" prefix.
    msg = str(err)
    if msg.startswith("yt-dlp failed"):
        return "Download failed. " + msg
    return "Download failed: " + msg


log = logging.getLogger(__name__)


@dataclass
class Job:
    url: str                                 # YouTube URL or ytsearch:... query
    title_hint: str = ""
    artist_hint: str = ""
    album_hint: str = ""
    year_hint: str = ""
    image_url_hint: Optional[str] = None
    search_query_itunes: str = ""


@dataclass
class JobProgress:
    job: Job
    stage: str = "queued"
    pct: float = 0.0
    message: str = ""
    folder: Optional[Path] = None
    error: Optional[str] = None


ProgressCB = Callable[[JobProgress], None]


class Pipeline:
    def __init__(self, settings: Settings, cancel: Optional[threading.Event] = None):
        self.settings = settings
        self.cancel = cancel or threading.Event()

    def _check(self) -> None:
        if self.cancel.is_set():
            raise AutoChartError("Cancelled")

    def _songs_root(self) -> Path:
        return Path(self.settings.output_folder)

    def run_job(self, job: Job, progress: ProgressCB) -> JobProgress:
        p = JobProgress(job=job, stage="downloading", pct=0.0)
        tmp = Path(tempfile.mkdtemp(prefix="chac_"))
        timings: Dict[str, int] = {}
        dr = None
        folder: Optional[Path] = None

        @contextmanager
        def _stage(name: str):
            """Capture wall-clock duration into the timings dict as `<name>_ms`.

            Monotonic clock so NTP skew can't produce negative deltas; integer
            milliseconds because the jobs.json consumer (profiling + the UI
            summary line) never needs sub-ms resolution and ints serialize
            cleaner than floats across tool boundaries.
            """
            t0 = time.monotonic()
            try:
                yield
            finally:
                timings[f"{name}_ms"] = int((time.monotonic() - t0) * 1000)

        try:
            progress(p)
            self._check()

            def _dl_cb(pct: float, msg: str) -> None:
                p.pct = 0.05 + pct * 0.25
                p.message = msg
                progress(p)

            with _stage("download"):
                dr = downloader.download_audio(
                    job.url, tmp,
                    quality=self.settings.audio_quality,
                    progress_cb=_dl_cb,
                    browser_cookies=self.settings.browser_cookies,
                    cookies_txt_path=self.settings.cookies_txt_path,
                    proxy_url=self.settings.proxy_url,
                    job_meta={
                        "title_hint": job.title_hint,
                        "artist_hint": job.artist_hint,
                        "album_hint": job.album_hint,
                        "year_hint": job.year_hint,
                        "image_url_hint": job.image_url_hint,
                        "search_query_itunes": job.search_query_itunes,
                    },
                )
            self._check()

            raw_title = job.title_hint or dr.title or "Unknown"
            raw_artist = job.artist_hint or dr.uploader or "Unknown"
            artist, title = packager.clean_song_metadata(raw_artist, raw_title)
            album = job.album_hint or ""
            year = job.year_hint or ""
            folder_name = packager.sanitize_folder_name(
                f"{artist} - {title}{self.settings.folder_suffix}"
            )
            folder = packager.ensure_dir(self._songs_root(), folder_name)
            p.folder = folder

            p.stage, p.pct, p.message = "art", 0.32, "Fetching album art..."
            progress(p)
            with _stage("art"):
                art.fetch_art(
                    folder / "album.jpg",
                    direct_url=job.image_url_hint or dr.thumbnail_url,
                    itunes_query=job.search_query_itunes or f"{artist} {title}",
                )
            self._check()

            video_file: Optional[Path] = None
            if self.settings.download_video:
                p.stage, p.pct, p.message = "video", 0.35, "Downloading video background..."
                progress(p)
                with _stage("video"):
                    def _vid_cb(pct: float, msg: str) -> None:
                        p.pct = 0.35 + pct * 0.04
                        p.message = f"Video: {msg}"
                        progress(p)
                    try:
                        video_file = downloader.download_video(
                            job.url, tmp,
                            progress_cb=_vid_cb,
                            browser_cookies=self.settings.browser_cookies,
                            cookies_txt_path=self.settings.cookies_txt_path,
                            proxy_url=self.settings.proxy_url,
                        )
                    except Exception as e:
                        # Video background is optional — a network/format issue must not block charting
                        log.warning("Video download failed (continuing without video): %s", e)
                        video_file = None
                self._check()

            guitar_stem: Optional[Path] = None
            if getattr(self.settings, "use_stem_separation", False):
                from core import stem_separator
                if stem_separator.is_available():
                    p.stage, p.pct, p.message = "stems", 0.32, "Isolating guitar stem (Demucs, 2-5 min)..."
                    progress(p)
                    with _stage("stems"):
                        try:
                            def _stem_progress(msg: str) -> None:
                                p.message = msg
                                progress(p)
                            guitar_stem = stem_separator.separate_guitar_stem(
                                dr.audio_path, progress_cb=_stem_progress,
                            )
                        except Exception as e:
                            # Fall back to full-mix analysis — slower chart quality,
                            # but the song still ships.
                            log.warning("Stem separation failed, falling back to full mix: %s", e)
                            guitar_stem = None
                    self._check()
                else:
                    log.info("Stem separation requested but demucs/torch not installed; using full mix.")

            p.stage, p.pct, p.message = "analyzing", 0.40, "Analyzing audio..."
            progress(p)
            with _stage("analyze"):
                manual_bpm = getattr(self.settings, "manual_bpm", 0.0)
                analysis = analyze_audio(
                    dr.audio_path,
                    manual_bpm=manual_bpm if manual_bpm > 0 else None,
                    guitar_stem_path=guitar_stem,
                )
            self._check()

            drums = None
            if self.settings.include_drums:
                p.stage, p.pct, p.message = "drums", 0.62, "Transcribing drums..."
                progress(p)
                with _stage("drums"):
                    drums = transcribe_drums(dr.audio_path)
                self._check()

            lyrics_data: Optional[LyricsData] = None
            if self.settings.enable_lyrics:
                p.stage, p.pct, p.message = "lyrics", 0.75, "Fetching synced lyrics..."
                progress(p)
                with _stage("lyrics"):
                    try:
                        res = fetch_synced_lyrics(
                            artist=artist, title=title,
                            duration_s=int(analysis.duration_s) or None,
                            album=album or None,
                        )
                    except Exception as e:
                        # Lyrics are a bonus — a network blip must never fail a chart.
                        log.info("Lyrics fetch raised: %s", e)
                        res = None
                    if res is not None:
                        text, times = res
                        lyrics_data = LyricsData(
                            lines=text.splitlines(),
                            times_s=list(times),
                            source="lrclib",
                        )
                self._check()

            p.stage, p.pct, p.message = "charting", 0.82, "Generating chart..."
            progress(p)
            with _stage("chart"):
                profile_name = getattr(self.settings, "genre_profile", "auto")
                if not profile_name or profile_name.lower() == "auto":
                    profile_name = detect_genre(artist=artist, title=title)
                cd = build_chart(analysis, drums, lyrics_data, genre_profile=profile_name)
            self._check()

            duration_ms = int(analysis.duration_s * 1000)
            preview_start = int(analysis.duration_s * 1000 * 0.33)
            cmeta = ChartMeta(
                name=title, artist=artist, album=album, year=year,
                charter=self.settings.charter,
                offset=self.settings.manual_offset_ms / 1000.0,
                difficulty=3,
                preview_start_ms=preview_start,
                preview_end_ms=preview_start + 30000,
            )

            p.stage, p.pct, p.message = "writing", 0.95, "Placing audio..."
            progress(p)
            with _stage("write"):
                # Group chart write + ini + audio placement under a single
                # "write" bucket — they're all cheap I/O and splitting them
                # out hides the composite I/O cost from the profiler.
                write_chart(folder / "notes.chart", cd, cmeta)
                imeta = IniMeta(
                    name=title, artist=artist, album=album, year=year,
                    song_length_ms=duration_ms,
                    charter=self.settings.charter,
                    diff_guitar=3,
                    diff_drums=3 if drums is not None else -1,
                    preview_start_ms=preview_start,
                    delay=self.settings.manual_offset_ms,
                    video_start_time=self.settings.video_start_time_ms if video_file else None,
                )
                write_ini(folder / "song.ini", imeta)
                packager.place_audio(dr.audio_path, folder, stream_name="song.ogg")
                if video_file and video_file.exists():
                    packager.place_video(video_file, folder, stream_name="video.mp4")


            p.stage, p.pct, p.message = "done", 1.0, f"Chart ready: {folder.name}"
            progress(p)
            return p
        except DownloadError as e:
            friendly = _humanize_download_error(e)
            log.warning("Download failed for %s: %s", job.url, e)
            p.stage, p.error, p.message = "failed", friendly, friendly
            progress(p)
            return p
        except AutoChartError as e:
            log.exception("Job failed")
            p.stage, p.error, p.message = "failed", str(e), str(e)
            progress(p)
            return p
        except Exception as e:
            log.exception("Job crashed")
            p.stage, p.error, p.message = "failed", str(e), f"Unexpected: {e}"
            progress(p)
            return p
        finally:
            # Attach stage timings to the jobs.json record created by
            # downloader._register_job. Guarded on dr/job_id so a failure
            # before download finishes doesn't crash the finally block.
            try:
                if dr is not None and dr.job_id:
                    downloader.update_job(
                        dr.job_id,
                        stage_durations_ms=timings,
                        total_ms=sum(timings.values()),
                        pipeline_stage=p.stage,
                        error=p.error,
                    )
            except Exception as e:
                log.info("update_job (stage timings) failed: %s", e)
            try:
                if p.stage != "done" and folder is not None and folder.exists():
                    if not (folder / "notes.chart").exists():
                        for f in folder.iterdir():
                            try: f.unlink()
                            except OSError: pass
                        folder.rmdir()
            except OSError:
                pass
            try:
                for f in tmp.iterdir():
                    try: f.unlink()
                    except OSError: pass
                tmp.rmdir()
            except OSError:
                pass

    def run_queue(self, jobs: List[Job], progress: ProgressCB) -> List[JobProgress]:
        results: List[JobProgress] = []
        for j in jobs:
            if self.cancel.is_set():
                break
            results.append(self.run_job(j, progress))
        return results
