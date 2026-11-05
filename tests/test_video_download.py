"""
Tests for Clone Hero background video.mp4 support:
- Settings toggle (download_video default False)
- packager.place_video
- downloader.download_video (yt-dlp invocation, mp4 remux)
- pipeline integration with video_start_time in song.ini
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from core.config import Settings
from core.errors import WriteError
from core.ini_writer import IniMeta, write_ini
from core.packager import place_video


class TestSettingsVideoFlag:
    def test_download_video_default_false(self):
        s = Settings()
        assert s.download_video is False
        assert s.video_start_time_ms == 0

    def test_settings_serialization(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        with patch.object(Settings, "path", return_value=cfg_file):
            s = Settings(download_video=True, video_start_time_ms=150)
            s.save()

            loaded = Settings.load()
            assert loaded.download_video is True
            assert loaded.video_start_time_ms == 150


class TestPackagerPlaceVideo:
    def test_place_video_success(self, tmp_path):
        src = tmp_path / "downloaded.mp4"
        src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        dest_folder = tmp_path / "SongFolder"
        dest_folder.mkdir()

        placed = place_video(src, dest_folder)
        assert placed == dest_folder / "video.mp4"
        assert placed.exists()
        assert not src.exists()

    def test_place_video_failure_raises_write_error(self, tmp_path):
        src = tmp_path / "nonexistent.mp4"
        dest_folder = tmp_path / "SongFolder"
        dest_folder.mkdir()

        with pytest.raises(WriteError):
            place_video(src, dest_folder)


class _CapturedVideoYDL:
    opts_seen: List[Dict[str, Any]] = []
    out_dir: Path = Path(".")
    fake_id: str = "fakeVid123"

    def __init__(self, opts: Dict[str, Any]):
        _CapturedVideoYDL.opts_seen.append(dict(opts))
        self._opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url: str, download: bool = True):
        # Create the fake video file
        target = _CapturedVideoYDL.out_dir / f"{_CapturedVideoYDL.fake_id}_video.mp4"
        target.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return {
            "id": _CapturedVideoYDL.fake_id,
            "title": "fake video",
            "duration": 120,
        }


@pytest.fixture
def capture_video_ydl(monkeypatch, tmp_path):
    _CapturedVideoYDL.opts_seen = []
    _CapturedVideoYDL.out_dir = tmp_path

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = _CapturedVideoYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)

    import core.downloader as dl
    monkeypatch.setattr(dl, "_probe_ffmpeg", lambda: None)
    monkeypatch.setattr(dl, "ffmpeg_path", lambda: "ffmpeg")
    yield _CapturedVideoYDL


class TestDownloaderVideo:
    def test_download_video_opts_and_format(self, capture_video_ydl, tmp_path):
        import core.downloader as dl

        res_path = dl.download_video(
            url="https://youtube.com/watch?v=12345",
            out_dir=tmp_path,
            max_height=720,
            proxy_url="http://proxy:8080",
        )
        assert res_path.exists()
        assert res_path.name.endswith(".mp4")

        assert len(capture_video_ydl.opts_seen) > 0
        first_opts = capture_video_ydl.opts_seen[0]
        assert first_opts.get("merge_output_format") == "mp4"
        assert first_opts.get("remux_video") == "mp4"
        assert first_opts.get("proxy") == "http://proxy:8080"
        assert "720" in first_opts.get("format", "")


class TestPipelineVideoIntegration:
    def test_ini_without_video(self, tmp_path):
        ini_path = tmp_path / "song.ini"
        imeta = IniMeta(
            name="Test Song",
            artist="Test Artist",
            song_length_ms=180000,
            video_start_time=None,
        )
        write_ini(ini_path, imeta)
        content = ini_path.read_text(encoding="utf-8")
        assert "video_start_time" not in content

    def test_ini_with_video(self, tmp_path):
        ini_path = tmp_path / "song.ini"
        imeta = IniMeta(
            name="Test Song",
            artist="Test Artist",
            song_length_ms=180000,
            video_start_time=0,
        )
        write_ini(ini_path, imeta)
        content = ini_path.read_text(encoding="utf-8")
        assert "video_start_time = 0" in content
