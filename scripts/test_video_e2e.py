"""
End-to-end video.mp4 pipeline test.

Runs the full pipeline on one Carti track with download_video=True and
verifies the output folder contains all 5 expected artifacts plus the
song.ini carries video_start_time when a video.mp4 was placed.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from core.config import Settings     # noqa: E402
from core.pipeline import Job, Pipeline  # noqa: E402
from core.search import yt_search    # noqa: E402

OUT = PROJ / "tmp" / "video_e2e"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    settings = Settings.load()
    settings.output_folder = str(OUT)
    settings.folder_suffix = " (auto)"
    settings.download_video = True   # the flag under test
    settings.include_drums = True
    settings.enable_lyrics = True

    print(f"download_video      = {settings.download_video}")
    print(f"video_start_time_ms = {settings.video_start_time_ms}")

    print("\nresolving search ...")
    results = yt_search("Playboi Carti EVILJ0RDAN", limit=1,
                        browser_cookies=settings.browser_cookies)
    if not results:
        print("ERR: no search result")
        return 2
    url = results[0].url
    print(f"  -> {results[0].title}  {url}")

    job = Job(url=url, title_hint="EVILJ0RDAN", artist_hint="Playboi Carti")
    pipeline = Pipeline(settings)

    def _progress(p):
        pass

    print("\nrunning pipeline (this takes ~1-2 min with video download) ...")
    result = pipeline.run_job(job, _progress)
    print(f"\nstage={result.stage}")
    if result.stage == "failed":
        print(f"ERROR: {result.error}")
        return 1

    folder = result.folder
    print(f"folder: {folder}")
    expected = {"notes.chart", "song.ini", "song.ogg", "album.jpg", "video.mp4"}
    actual = {p.name for p in folder.iterdir() if p.is_file()}
    print(f"files produced: {sorted(actual)}")
    missing = expected - actual
    extra = actual - expected

    ok = True
    if missing:
        print(f"MISSING: {sorted(missing)}")
        ok = False
    if extra:
        print(f"extra (OK): {sorted(extra)}")

    # Verify song.ini carries video_start_time when video.mp4 is present
    ini = (folder / "song.ini").read_text(encoding="utf-8", errors="ignore")
    has_vst = "video_start_time" in ini
    print(f"\nsong.ini has 'video_start_time' line: {has_vst}")
    if (folder / "video.mp4").exists() and not has_vst:
        print("ERROR: video.mp4 present but song.ini missing video_start_time")
        ok = False

    # Sizes
    vp = folder / "video.mp4"
    if vp.exists():
        print(f"video.mp4 size: {vp.stat().st_size / 1024 / 1024:.1f} MB")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
