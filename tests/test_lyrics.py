"""
LRCLIB backend coverage: parse paths, 404, network failure, malformed payload.

Note on the mock boundary: baker's brief said "mock requests.get" but the
module uses stdlib urllib.request.urlopen (no `requests` dependency on
LRCLIB's HTTP path). Mocking urlopen is the actual network boundary; the
behaviours tested are identical to what a requests-based implementation
would exercise.
"""
from __future__ import annotations

import io
import json
import urllib.error
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from core import lyrics as lyrics_mod
from core.lyrics import _parse_lrc, fetch_synced_lyrics


@pytest.fixture(autouse=True)
def _bypass_lyrics_cache(monkeypatch):
    """lyrics module reads/writes core.downloader.lyrics_cache_* (jobs.json
    on disk) before the HTTP calls. A successful fetch persists, so later
    tests reusing the same ("ar"/"ti") would hit the cache and skip the
    mocked urlopen. Patch both to no-op so every test exercises the net path."""
    import core.downloader as dl
    monkeypatch.setattr(dl, "lyrics_cache_get", lambda key: None, raising=False)
    monkeypatch.setattr(dl, "lyrics_cache_put", lambda key, rec: None, raising=False)


# ---------- urlopen double --------------------------------------------------

class _FakeResponse:
    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._buf.close()
        return False

    def read(self, *args, **kwargs):
        return self._buf.read(*args, **kwargs)


def _ok(payload: Any) -> _FakeResponse:
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


def _http_err(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url="x", code=code, msg="x", hdrs=None, fp=None)


# ---------- shared sample LRC payload --------------------------------------

SAMPLE_LRC = (
    "[ar:Sample Artist]\n"
    "[ti:Sample Song]\n"
    "[length:03:14]\n"
    "[00:12.50]First line of the song\n"
    "[00:18.00]Second line here\n"
    "[00:24.33]Third line, slightly later\n"
    "[01:02.00][02:05.00]Repeated chorus\n"
    "[03:00.00]\n"                  # instrumental break — empty text, dropped
)


# ---------- _parse_lrc unit tests ------------------------------------------

class TestParseLrc:
    def test_basic_parse_shape(self):
        text, times = _parse_lrc(SAMPLE_LRC)
        lines = text.splitlines()
        assert len(lines) == len(times), "parity invariant broken"
        assert all(t2 >= t1 for t1, t2 in zip(times, times[1:])), "times not sorted"

    def test_timestamps_decoded_correctly(self):
        _, times = _parse_lrc("[00:12.50]hello\n")
        assert times == [12.5]

    def test_fractional_milliseconds(self):
        _, times = _parse_lrc("[00:00.123]x\n")
        assert times == [0.123]

    def test_metadata_lines_dropped(self):
        text, times = _parse_lrc("[ar:Who]\n[00:01.00]only this\n[length:03:14]\n")
        assert text == "only this"
        assert times == [1.0]

    def test_multi_timestamp_line_duplicates(self):
        text, times = _parse_lrc("[00:10.00][00:20.00]chorus\n")
        assert text.splitlines() == ["chorus", "chorus"]
        assert times == [10.0, 20.0]

    def test_empty_lyric_timestamp_dropped(self):
        text, times = _parse_lrc("[00:30.00]\n[00:35.00]something\n")
        assert text == "something"
        assert times == [35.0]

    def test_empty_input_returns_empty_pair(self):
        text, times = _parse_lrc("")
        assert text == "" and times == []

    def test_only_metadata_returns_empty(self):
        text, times = _parse_lrc("[ar:Who]\n[ti:Song]\n")
        assert text == "" and times == []


# ---------- fetch_synced_lyrics — normal parse ------------------------------

class TestFetchNormalParse:
    def test_get_endpoint_returns_synced(self):
        payload = {"syncedLyrics": SAMPLE_LRC, "plainLyrics": "fallback"}
        with patch("core.lyrics.urllib.request.urlopen", return_value=_ok(payload)):
            got = fetch_synced_lyrics("artist", "title", duration_s=194)
        assert got is not None
        text, times = got
        assert text  # non-empty
        assert len(text.splitlines()) == len(times)
        assert times[0] == 12.5

    def test_passes_album_and_duration_in_query(self):
        captured: List[str] = []

        def _fake_urlopen(req, timeout=None):
            captured.append(req.full_url)
            return _ok({"syncedLyrics": "[00:01.00]hi\n"})

        with patch("core.lyrics.urllib.request.urlopen", side_effect=_fake_urlopen):
            fetch_synced_lyrics("ar", "ti", duration_s=200, album="al")
        assert captured, "urlopen was not called"
        url = captured[0]
        assert "artist_name=ar" in url
        assert "track_name=ti" in url
        assert "album_name=al" in url
        assert "duration=200" in url

    def test_falls_back_to_search_when_get_lacks_synced(self):
        """Full 3-tier flow with duration_s:
          Tier 1 (/get WITH duration)   → plain only, no synced
          Tier 2 (/get WITHOUT duration)→ plain only, no synced
          Tier 3 (/search)              → list, one entry has synced → used
        """
        responses = [
            _ok({"plainLyrics": "unsynced only"}),              # Tier 1
            _ok({"plainLyrics": "still no synced"}),            # Tier 2
            _ok([
                {"id": 1, "plainLyrics": "x"},
                {"id": 2, "syncedLyrics": "[00:05.00]late\n"},
            ]),                                                 # Tier 3
        ]
        with patch("core.lyrics.urllib.request.urlopen", side_effect=responses):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is not None
        assert got[1] == [5.0]

    def test_rejects_empty_artist_or_title(self):
        assert fetch_synced_lyrics("", "song") is None
        assert fetch_synced_lyrics("artist", "") is None


# ---------- 404 handling ----------------------------------------------------

class TestFetch404:
    def test_404_on_get_falls_through_to_search(self):
        # Tier 1 404 → Tier 2 404 → Tier 3 empty list → None overall.
        side = [_http_err(404), _http_err(404), _ok([])]
        with patch("core.lyrics.urllib.request.urlopen", side_effect=side):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is None

    def test_404_on_all_tiers_returns_none(self):
        side = [_http_err(404), _http_err(404), _http_err(404)]
        with patch("core.lyrics.urllib.request.urlopen", side_effect=side):
            got = fetch_synced_lyrics("nobody", "nothing", duration_s=200)
        assert got is None

    def test_404_does_not_retry(self):
        """404 is definitive — module should give up on each tier immediately,
        no backoff. With duration_s set, all 3 tiers fire; without, only
        tier 3 fires."""
        call_count = {"n": 0}

        def _once(req, timeout=None):
            call_count["n"] += 1
            raise _http_err(404)

        with patch("core.lyrics.urllib.request.urlopen", side_effect=_once), \
             patch("core.lyrics.time.sleep") as sleep_mock:
            fetch_synced_lyrics("ar", "ti", duration_s=200)
        # One 404 per tier (3 total: get-with-dur, get-no-dur, search), no retries.
        assert call_count["n"] == 3
        sleep_mock.assert_not_called()


# ---------- network error ---------------------------------------------------

class TestNetworkError:
    def test_url_error_returns_none_after_retries(self):
        err = urllib.error.URLError("Name or service not known")

        def _always_fail(req, timeout=None):
            raise err

        with patch("core.lyrics.urllib.request.urlopen", side_effect=_always_fail), \
             patch("core.lyrics.time.sleep"):  # no real sleeping in tests
            got = fetch_synced_lyrics("ar", "ti")
        assert got is None

    def test_timeout_returns_none(self):
        with patch("core.lyrics.urllib.request.urlopen", side_effect=TimeoutError("slow")), \
             patch("core.lyrics.time.sleep"):
            got = fetch_synced_lyrics("ar", "ti")
        assert got is None

    def test_5xx_retries_then_recovers(self):
        """503 twice on tier 1, then a good response on the retry — tier 1
        succeeds and tiers 2 and 3 never fire."""
        side = [
            _http_err(503),
            _http_err(503),
            _ok({"syncedLyrics": "[00:01.00]ok\n"}),
        ]
        with patch("core.lyrics.urllib.request.urlopen", side_effect=side), \
             patch("core.lyrics.time.sleep"):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is not None
        assert got[1] == [1.0]

    def test_5xx_exhausts_retries(self):
        """503 everywhere — all tiers fail → None. Need enough side_effect
        entries to cover the retry-per-tier budget."""
        side = [_http_err(503)] * 30
        with patch("core.lyrics.urllib.request.urlopen", side_effect=side), \
             patch("core.lyrics.time.sleep"):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is None


# ---------- malformed response ---------------------------------------------

class TestMalformedResponse:
    def test_non_json_body_returns_none(self):
        # JSONDecodeError triggers retries per tier, so every call needs its
        # own fresh response (context-manager exit closes the buffer).
        def _fresh_bad(req, timeout=None):
            return _FakeResponse(b"<html>oops</html>")
        with patch("core.lyrics.urllib.request.urlopen", side_effect=_fresh_bad), \
             patch("core.lyrics.time.sleep"):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is None

    def test_json_list_at_get_endpoint_falls_through(self):
        """/api/get is documented to return an object; a list payload is
        unexpected. Tiers 1+2 drop it, tier 3 gets empty list → None."""
        side = [_ok([{"id": 1}]), _ok([{"id": 2}]), _ok([])]
        with patch("core.lyrics.urllib.request.urlopen", side_effect=side):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is None

    def test_synced_present_but_empty_string_returns_none(self):
        side = [
            _ok({"syncedLyrics": ""}),      # Tier 1 empty
            _ok({"syncedLyrics": ""}),      # Tier 2 empty
            _ok([]),                        # Tier 3 empty
        ]
        with patch("core.lyrics.urllib.request.urlopen", side_effect=side):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is None

    def test_synced_present_but_only_metadata_returns_none(self):
        """LRC with no actual timed lines — parity check in fetch rejects
        it after tier 1 fills `synced` with metadata-only text."""
        payload = {"syncedLyrics": "[ar:X]\n[ti:Y]\n[length:03:14]\n"}
        with patch("core.lyrics.urllib.request.urlopen", return_value=_ok(payload)):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is None

    def test_search_entry_missing_syncedLyrics_key(self):
        side = [
            _ok({}),                                            # Tier 1 empty
            _ok({}),                                            # Tier 2 empty
            _ok([{"id": 1}, {"id": 2, "plainLyrics": "x"}]),    # Tier 3 no synced
        ]
        with patch("core.lyrics.urllib.request.urlopen", side_effect=side):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is None

    def test_search_entry_with_malformed_lrc_dropped(self):
        """A /search candidate with a non-timestamped syncedLyrics body yields
        no times → parity check kicks it out, returns None."""
        side = [
            _ok({}),
            _ok({}),
            _ok([{"id": 1, "syncedLyrics": "not a valid LRC body\nno timestamps\n"}]),
        ]
        with patch("core.lyrics.urllib.request.urlopen", side_effect=side):
            got = fetch_synced_lyrics("ar", "ti", duration_s=200)
        assert got is None
