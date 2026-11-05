"""
core.resolver.resolve: 6 input kinds, URL precedence, edge cases.
Resolver is deterministic (pure regex), so these are exact-match assertions.
"""
import pytest

from core.errors import InputError
from core.resolver import InputKind, resolve


class TestYouTubeVideo:
    @pytest.mark.parametrize("url,vid", [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("http://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/abcDEF12345", "abcDEF12345"),
        ("https://www.youtube.com/embed/abcDEF12345?autoplay=1", "abcDEF12345"),
        ("https://youtube.com/watch?v=abc_DEF-123", "abc_DEF-123"),
    ])
    def test_recognizes_video(self, url, vid):
        r = resolve(url)
        assert r.kind is InputKind.YT_VIDEO
        assert r.id == vid

    def test_video_in_playlist_url_still_video(self):
        # watch?v=... with &list=... — resolver treats as video, not playlist.
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc"
        r = resolve(url)
        assert r.kind is InputKind.YT_VIDEO
        assert r.id == "dQw4w9WgXcQ"


class TestYouTubePlaylist:
    def test_plain_playlist(self):
        r = resolve("https://www.youtube.com/playlist?list=PLabc_DEF-123")
        assert r.kind is InputKind.YT_PLAYLIST
        assert r.id == "PLabc_DEF-123"


class TestSpotifyTrack:
    def test_track(self):
        r = resolve("https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh")
        assert r.kind is InputKind.SPOTIFY_TRACK
        assert r.id == "4iV5W9uYEdYUVa79Axb7Rh"

    def test_track_with_query_tail(self):
        r = resolve("https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh?si=xyz")
        assert r.kind is InputKind.SPOTIFY_TRACK
        assert r.id == "4iV5W9uYEdYUVa79Axb7Rh"


class TestSpotifyPlaylist:
    def test_playlist(self):
        r = resolve("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        assert r.kind is InputKind.SPOTIFY_PLAYLIST
        assert r.id == "37i9dQZF1DXcBWIGoYBM5M"


class TestSpotifyAlbum:
    def test_album(self):
        r = resolve("https://open.spotify.com/album/0JGOiO34nwfUdDrD612dOp")
        assert r.kind is InputKind.SPOTIFY_ALBUM
        assert r.id == "0JGOiO34nwfUdDrD612dOp"


class TestSearchText:
    @pytest.mark.parametrize("q", [
        "Playboi Carti - Sky",
        "   mix: lofi beats 2026   ",
        "unknown song title with spaces",
    ])
    def test_free_text_becomes_search(self, q):
        r = resolve(q)
        assert r.kind is InputKind.SEARCH_TEXT
        assert r.id is None
        assert r.raw == q.strip()


class TestErrorCases:
    @pytest.mark.parametrize("empty", ["", "   ", "\t\n"])
    def test_empty_raises(self, empty):
        with pytest.raises(InputError):
            resolve(empty)

    def test_unknown_url_raises(self):
        with pytest.raises(InputError):
            resolve("https://soundcloud.com/some-track")

    def test_http_without_match_raises(self):
        with pytest.raises(InputError):
            resolve("http://example.com/nothing/here")


class TestPrecedence:
    """Spotify playlist > album > track > YT playlist > YT video > search text."""

    def test_spotify_wins_over_yt_in_mixed_string(self):
        # If a message pastes both a YT and a Spotify URL, Spotify is picked.
        text = "check this https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh and https://youtu.be/dQw4w9WgXcQ"
        r = resolve(text)
        assert r.kind is InputKind.SPOTIFY_TRACK

    def test_url_scheme_without_known_host_errors(self):
        with pytest.raises(InputError):
            resolve("https://bandcamp.com/album/foo")
