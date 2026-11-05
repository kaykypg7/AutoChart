"""
Validate the cookies precedence chain inside core.downloader.download_audio.

Precedence (from the fallback-chain comment in downloader.py):
    1. cookies.txt file (wins when present) — cookiefile key in opts
    2. live browser session cookies           — cookiesfrombrowser key
    3. anonymous                              — neither key
    4. android-only player client             — neither cookies key

We mock yt_dlp.YoutubeDL to capture the opts dict passed on each attempt and
inspect the FIRST attempt (the one that would win if everything else is
healthy). The real yt_dlp isn't installed in CI's fast-test lane; a fake
module is injected into sys.modules before the function is called.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest


# ---------- fake yt_dlp injected into sys.modules ---------------------------

class _CapturedYDL:
    """Context-manager double for yt_dlp.YoutubeDL that records opts and
    writes a placeholder audio file so downloader's post-check succeeds."""
    opts_seen: List[Dict[str, Any]] = []
    out_dir: Path = Path(".")
    fake_id: str = "fakeID12345"

    def __init__(self, opts: Dict[str, Any]):
        _CapturedYDL.opts_seen.append(dict(opts))
        self._opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url: str, download: bool = True):
        # Simulate a successful extract — drop the expected .ogg so the
        # downloader's post-download file check passes.
        (_CapturedYDL.out_dir / f"{_CapturedYDL.fake_id}.ogg").write_bytes(b"OggS\x00")
        return {
            "id": _CapturedYDL.fake_id,
            "title": "fake title",
            "uploader": "fake up",
            "duration": 123,
            "thumbnail": "https://example.invalid/thumb.jpg",
        }


def _install_fake_yt_dlp():
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = _CapturedYDL  # type: ignore[attr-defined]
    sys.modules["yt_dlp"] = mod


@pytest.fixture
def captured(monkeypatch, tmp_path):
    _CapturedYDL.opts_seen = []
    _CapturedYDL.out_dir = tmp_path
    _install_fake_yt_dlp()

    # Skip the ffmpeg probe — it inspects the real filesystem.
    import core.downloader as dl
    monkeypatch.setattr(dl, "_probe_ffmpeg", lambda: None)
    # Redirect the jobs journal to tmp so we don't touch AppData.
    monkeypatch.setattr(dl, "_jobs_path", lambda: tmp_path / "jobs.json")
    yield _CapturedYDL


def _run(tmp_path: Path, **kwargs):
    import core.downloader as dl
    return dl.download_audio(url="https://y.invalid/x", out_dir=tmp_path, **kwargs)


class TestCookiesTxtWins:
    def test_cookies_txt_file_takes_precedence_over_browser(self, captured, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        _run(tmp_path, browser_cookies="firefox", cookies_txt_path=str(cookies))

        first = captured.opts_seen[0]
        assert first.get("cookiefile") == str(cookies), \
            "first attempt must use cookies.txt when a valid file is given"
        assert "cookiesfrombrowser" not in first, \
            "cookiesfrombrowser must NOT appear on the cookies-txt attempt"

    def test_invalid_cookies_txt_path_falls_through_to_browser(self, captured, tmp_path):
        # Non-existent file → fall through, browser wins first attempt.
        _run(tmp_path, browser_cookies="firefox",
             cookies_txt_path=str(tmp_path / "does_not_exist.txt"))

        first = captured.opts_seen[0]
        assert "cookiefile" not in first
        assert first.get("cookiesfrombrowser") == ("firefox",)

    def test_empty_cookies_txt_path_falls_through_to_browser(self, captured, tmp_path):
        _run(tmp_path, browser_cookies="firefox", cookies_txt_path="")

        first = captured.opts_seen[0]
        assert "cookiefile" not in first
        assert first.get("cookiesfrombrowser") == ("firefox",)


class TestBrowserCookieBranches:
    def test_firefox_passes_browser_tuple(self, captured, tmp_path):
        _run(tmp_path, browser_cookies="firefox")
        first = captured.opts_seen[0]
        assert first.get("cookiesfrombrowser") == ("firefox",)

    @pytest.mark.parametrize("browser", ["chrome", "edge", "brave", "chromium", "opera", "vivaldi"])
    def test_chromium_family_disabled(self, captured, tmp_path, browser):
        # Chromium-class browsers are intentionally skipped — DPAPI/app-bound
        # encryption makes cookiesfrombrowser crash. First attempt must have
        # NO cookies at all.
        _run(tmp_path, browser_cookies=browser, cookies_txt_path="")
        first = captured.opts_seen[0]
        assert "cookiesfrombrowser" not in first
        assert "cookiefile" not in first

    @pytest.mark.parametrize("browser", ["", "none"])
    def test_disabled_browser_runs_anonymous_first(self, captured, tmp_path, browser):
        _run(tmp_path, browser_cookies=browser, cookies_txt_path="")
        first = captured.opts_seen[0]
        assert "cookiesfrombrowser" not in first
        assert "cookiefile" not in first


class TestAttemptChainOrder:
    """Each attempt is a separate opts dict; the chain order encodes the
    documented precedence."""

    def test_order_is_txt_then_browser_then_anon(self, captured, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# cookies\n", encoding="utf-8")
        _run(tmp_path, browser_cookies="firefox", cookies_txt_path=str(cookies))

        # First attempt succeeds (our fake always returns info), so only one
        # attempt is captured. To see the full chain we need to force failures.
        assert len(captured.opts_seen) >= 1
        assert captured.opts_seen[0].get("cookiefile") == str(cookies)

    def test_full_fallback_chain_on_repeated_failure(self, monkeypatch, tmp_path):
        """
        Force every attempt to raise a retryable error; capture every opts
        the downloader tried. Precedence: cookies-txt → browser → no-cookies
        → android-only.
        """
        attempted: List[Dict[str, Any]] = []

        class _FailingYDL:
            def __init__(self, opts):
                attempted.append(dict(opts))
                self._opts = opts

            def __enter__(self):
                return self

            def __exit__(self, et, e, tb):
                return False

            def extract_info(self, url, download=True):
                raise RuntimeError("Requested format is not available")

        fake = types.ModuleType("yt_dlp")
        fake.YoutubeDL = _FailingYDL  # type: ignore[attr-defined]
        sys.modules["yt_dlp"] = fake

        import core.downloader as dl
        monkeypatch.setattr(dl, "_probe_ffmpeg", lambda: None)
        monkeypatch.setattr(dl, "_jobs_path", lambda: tmp_path / "jobs.json")
        # Collapse the backoff sleep so this test stays snappy.
        monkeypatch.setattr(dl.time, "sleep", lambda *_a, **_k: None)

        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# cookies\n", encoding="utf-8")

        from core.errors import DownloadError
        with pytest.raises(DownloadError):
            dl.download_audio(
                url="https://y.invalid/x",
                out_dir=tmp_path,
                browser_cookies="firefox",
                cookies_txt_path=str(cookies),
            )

        # Expected chain: 4 attempts in strict order.
        assert len(attempted) == 4
        assert attempted[0].get("cookiefile") == str(cookies)
        assert "cookiesfrombrowser" not in attempted[0]

        assert attempted[1].get("cookiesfrombrowser") == ("firefox",)
        assert "cookiefile" not in attempted[1]

        assert "cookiefile" not in attempted[2]
        assert "cookiesfrombrowser" not in attempted[2]

        # android-only reduces the player_client list.
        android_opts = attempted[3]["extractor_args"]["youtube"]["player_client"]
        assert android_opts == ["android"]
