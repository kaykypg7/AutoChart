"""
Synced lyrics backend.

Source: LRCLIB (https://lrclib.net)
----------------------------------------------------------------------
LRCLIB is a free, public, no-auth database of synchronized lyrics in LRC
format. It satisfies the project's constraint of "no credential beyond
what Settings already stores" — Spotify client_id/secret stays the only
secret in config.json.

License
-------
  * LRCLIB server code: MIT  (https://github.com/tranxuanthang/lrclib)
  * Lyrics payloads: contributor-provided; LRCLIB publishes them under
    CC0 1.0 ("No Rights Reserved") where the contributor consented.
    Downstream products that redistribute lyrics should still verify
    the original rights-holder, since song lyrics themselves are
    copyrighted works and CC0 is only the DB-contribution waiver.
    For an auto-charter that writes a .chart file consumed locally,
    this is acceptable fair-use territory; a public redistribution
    pipeline would need its own licensing conversation.

Rejected alternatives (all required additional credentials or scraping):
  * Spotify /tracks/{id}/lyrics — needs the user's sp_dc cookie
    (an authorization-code / web-player session), not client_credentials.
    Out of scope: would force a new secret into Settings.
  * Musixmatch synced API — commercial-tier API key required. The
    "developer" tier only exposes unsynced lyrics; synced is paywalled.
  * Genius — unsynced only, no timestamps in any tier.
  * NetEase Cloud Music — undocumented endpoint, geo-blocked outside CN,
    ToS violation to scrape.

Return shape
------------
`fetch_synced_lyrics` returns `(lyrics_text, timestamps_s)` or `None`:
  * lyrics_text:   "\n"-joined lines in time order, text only (no [mm:ss] prefix).
  * timestamps_s:  list of floats, same length as `lyrics_text.splitlines()`,
                   each is the start time of its line in seconds.
"""

from __future__ import annotations

import json
import logging
import re
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)


@dataclass
class LyricsData:
    """Carrier used by the chart layer. One entry per LRC line, text + start time.

    Invariant: `len(lines) == len(times_s)` and `times_s` is non-decreasing.
    """
    lines: List[str] = field(default_factory=list)
    times_s: List[float] = field(default_factory=list)
    source: str = "lrclib"


LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "CloneHeroAutoChart/1.0 (https://github.com/)"
TIMEOUT_S = 8.0
_RETRY_STATUSES = {502, 503, 504}
_LRC_LINE_RE = re.compile(r"^\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\](.*)$")


# Observability for the pipeline / validator — read at the end of a batch run
# to compute cache hit rate. Reset at process start; not thread-safe (pipeline
# serializes jobs through run_queue, so this is fine today).
LYRICS_STATS: dict = {"cache_hits": 0, "cache_misses": 0,
                      "net_hits": 0, "net_misses": 0}


def _cache_key(artist: str, title: str) -> str:
    """Normalized cache key — case-insensitive, whitespace-collapsed."""
    return f"{(artist or '').strip().lower()}|{(title or '').strip().lower()}"


def _http_get_json(url: str, retries: int = 2, backoff_s: float = 1.5) -> Optional[object]:
    """GET + JSON decode. Retries on 5xx and connection errors; gives up on 404 immediately."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in _RETRY_STATUSES and attempt <= retries:
                log.info("lyrics.lrclib.retry status=%d attempt=%d url=%s", e.code, attempt, url)
                time.sleep(backoff_s * attempt)
                continue
            log.info("lyrics.lrclib.http_err status=%d url=%s", e.code, url)
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            if attempt <= retries:
                log.info("lyrics.lrclib.retry err=%r attempt=%d", e, attempt)
                time.sleep(backoff_s * attempt)
                continue
            log.info("lyrics.lrclib.err %r url=%s", e, url)
            return None


def _parse_lrc(lrc_text: str) -> Tuple[str, List[float]]:
    """
    Parse an LRC blob into parallel (text, times_s) lists.

    LRC format: `[mm:ss.xx]text`. Metadata lines like `[ar:...]`, `[length:...]`,
    `[by:...]` fail the regex (two-digit seconds required) and are discarded.
    A single lyric line can carry multiple timestamps (used for repeated choruses);
    each timestamp emits its own entry so the chart engine sees each occurrence.
    Empty-text timestamps (instrumental breaks `[03:00.00]`) are dropped so the
    parity invariant `len(text.splitlines()) == len(times_s)` always holds.
    """
    entries: List[Tuple[float, str]] = []
    for raw in lrc_text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        # Multi-timestamp lines: `[00:12.00][00:48.00]chorus text`
        times: List[float] = []
        rest = stripped
        while True:
            m = _LRC_LINE_RE.match(rest)
            if not m:
                break
            mm, ss, frac, remainder = m.groups()
            t = int(mm) * 60 + int(ss)
            if frac:
                t += int(frac.ljust(3, "0")[:3]) / 1000.0
            times.append(t)
            # If there's another `[mm:ss]` immediately following, keep peeling.
            if remainder.startswith("["):
                rest = remainder
                continue
            text = remainder.strip()
            if text:
                for ts in times:
                    entries.append((ts, text))
            break
    entries.sort(key=lambda p: p[0])
    return "\n".join(t for _, t in entries), [ts for ts, _ in entries]


def fetch_synced_lyrics(artist: str, title: str,
                        duration_s: Optional[int] = None,
                        album: Optional[str] = None
                        ) -> Optional[Tuple[str, List[float]]]:
    """
    Look up synchronized lyrics for one track.

    Args:
        artist:     performing artist name (as it appears on the release)
        title:      track title (as it appears on the release)
        duration_s: optional length in seconds — narrows match on /api/get
                    (LRCLIB prefers duration-close matches to disambiguate
                    live versions, remasters, etc.)
        album:      optional album name — same disambiguation role

    Returns:
        (lyrics_text, timestamps_s) when synced lyrics exist, else None.
        timestamps_s is monotonically non-decreasing (sorted).

    No credentials required.
    """
    if not artist or not title:
        return None

    # Cache check — jobs.json "lyrics_cache" section, keyed by (artist, title).
    # Lazy import to avoid a hard dep on downloader at import time (so unit
    # tests of the LRC parser can run without touching the APPDATA directory).
    try:
        from core.downloader import lyrics_cache_get, lyrics_cache_put
    except Exception:
        lyrics_cache_get = lyrics_cache_put = None  # type: ignore

    key = _cache_key(artist, title)
    if lyrics_cache_get is not None:
        rec = lyrics_cache_get(key)
        if rec and rec.get("synced_lyrics"):
            LYRICS_STATS["cache_hits"] += 1
            text, times = _parse_lrc(rec["synced_lyrics"])
            if times:
                return text, times
    LYRICS_STATS["cache_misses"] += 1

    # Three-tier network fallback, cheapest first:
    #   1. /api/get WITH duration (narrowest; LRCLIB enforces a ~2s window)
    #   2. /api/get WITHOUT duration (same endpoint, broader match — saves
    #      one HTTP RTT vs jumping straight to /api/search when the only
    #      issue was a duration mismatch between the rip and the DB entry)
    #   3. /api/search (ranked list, last resort)
    synced = None

    base_params = {"artist_name": artist, "track_name": title}
    if album:
        base_params["album_name"] = album

    # Tier 1 — strict (with duration).
    if duration_s:
        p1 = dict(base_params)
        p1["duration"] = str(int(duration_s))
        data = _http_get_json(f"{LRCLIB_BASE}/get?" + urllib.parse.urlencode(p1))
        if isinstance(data, dict):
            synced = data.get("syncedLyrics")

    # Tier 2 — relaxed (/api/get without duration). Skip if tier 1 already
    # ran without duration (would be a redundant identical request).
    if not synced and duration_s:
        data = _http_get_json(f"{LRCLIB_BASE}/get?" + urllib.parse.urlencode(base_params))
        if isinstance(data, dict):
            synced = data.get("syncedLyrics")

    # Tier 3 — search.
    if not synced:
        q = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
        candidates = _http_get_json(f"{LRCLIB_BASE}/search?{q}")
        if isinstance(candidates, list):
            for cand in candidates:
                if cand.get("syncedLyrics"):
                    synced = cand["syncedLyrics"]
                    break

    if not synced:
        LYRICS_STATS["net_misses"] += 1
        return None

    LYRICS_STATS["net_hits"] += 1

    text, times = _parse_lrc(synced)
    if not times:
        return None

    if lyrics_cache_put is not None:
        lyrics_cache_put(key, {
            "synced_lyrics": synced,
            "source": "lrclib",
            "cached_at": time.time(),
        })
    return text, times


_PHRASE_GAP_S = 2.5  # per baker's spec: gaps >= 2.5s between LRC lines open a new phrase
_PUNCT_STRIP = re.compile(f"[{re.escape(string.punctuation)}]+")


def _normalize_phrase(text: str) -> str:
    """Lower, collapse whitespace, strip punctuation. Used to compare phrases
    for repetition even when LRC transcribers vary punctuation ("yeah." vs "yeah")."""
    s = text.lower()
    s = _PUNCT_STRIP.sub(" ", s)
    s = " ".join(s.split())
    return s


def analyze_structure(lyrics: LyricsData) -> List[Tuple[float, str]]:
    """
    Derive song structure (verse/chorus) from LRCLIB line timings + text.

    Two signals, OR'd together:
      SIGNAL A (phrase-level, per baker's spec):
        1. Group consecutive lines into phrases on 2.5s gaps.
        2. Normalized phrase text that occurs >= 2 times → the phrase is a chorus.
      SIGNAL B (line-level, hip-hop safety net):
        Lines whose normalized text occurs >= 3 times across the whole song are
        "hook lines" (e.g., trap songs where the whole chorus is one line repeated
        6x inside dense flow). A phrase containing a hook line → chorus.
        3x threshold (not 2x) suppresses false positives on short filler lines
        like "yeah" that recur without being the hook.

    Returns:
      [(time_s, label)] in time order, one per phrase. label is "chorus" or "verse".
      [] if lyrics has no usable groupings.

    Signal A alone works on pop/rock where choruses sit between natural pauses.
    Hip-hop with dense flow has too few 2.5s gaps for signal A to produce
    multiple phrases, so signal B carries the load there. Through-composed
    material with no repetition on either signal returns all "verse" — correct.
    """
    if lyrics is None or not lyrics.lines or not lyrics.times_s:
        return []

    pairs = [(float(t), s) for t, s in zip(lyrics.times_s, lyrics.lines) if s and s.strip()]
    pairs.sort(key=lambda p: p[0])
    if not pairs:
        return []

    # Phase 1 — group into phrases on 2.5s gaps.
    phrases: List[Tuple[float, List[str]]] = []
    cur_lines: List[str] = [pairs[0][1]]
    cur_start = pairs[0][0]
    prev_t = pairs[0][0]
    for t, s in pairs[1:]:
        if t - prev_t >= _PHRASE_GAP_S:
            phrases.append((cur_start, cur_lines))
            cur_lines = [s]
            cur_start = t
        else:
            cur_lines.append(s)
        prev_t = t
    phrases.append((cur_start, cur_lines))

    # Phase 2a — count normalized phrase-group occurrences (signal A).
    norm_phrase = [_normalize_phrase(" ".join(lines)) for _, lines in phrases]
    phrase_counts: dict = {}
    for n in norm_phrase:
        phrase_counts[n] = phrase_counts.get(n, 0) + 1

    # Phase 2b — count normalized line occurrences across all lyric lines (signal B).
    line_counts: dict = {}
    for _, s in pairs:
        n = _normalize_phrase(s)
        if n:
            line_counts[n] = line_counts.get(n, 0) + 1
    hook_lines = {n for n, c in line_counts.items() if c >= 3}

    # Phase 3 — emit (time_s, label). Chorus wins if either signal fires.
    out: List[Tuple[float, str]] = []
    for (t_start, lines), nphrase in zip(phrases, norm_phrase):
        is_chorus_A = bool(nphrase) and phrase_counts.get(nphrase, 0) >= 2
        is_chorus_B = any(_normalize_phrase(l) in hook_lines for l in lines)
        label = "chorus" if (is_chorus_A or is_chorus_B) else "verse"
        out.append((t_start, label))
    return out
