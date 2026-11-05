from dataclasses import dataclass
from typing import List, Optional

from core.errors import SpotifyAuthError


@dataclass
class TrackMeta:
    title: str
    artist: str
    album: str
    year: str
    duration_ms: int
    image_url: Optional[str] = None
    spotify_id: Optional[str] = None


class SpotifyClient:
    def __init__(self, client_id: str, client_secret: str):
        if not client_id or not client_secret:
            raise SpotifyAuthError("Spotify client_id/secret not configured.")
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
        except ImportError as e:
            raise SpotifyAuthError(f"spotipy not installed: {e}")
        auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        self._sp = spotipy.Spotify(auth_manager=auth, requests_timeout=20)

    @staticmethod
    def _year(release_date: str) -> str:
        return release_date[:4] if release_date else ""

    @staticmethod
    def _biggest_image(images) -> Optional[str]:
        if not images:
            return None
        return sorted(images, key=lambda i: i.get("width") or 0, reverse=True)[0].get("url")

    def track(self, track_id: str) -> TrackMeta:
        t = self._sp.track(track_id)
        return TrackMeta(
            title=t["name"],
            artist=", ".join(a["name"] for a in t["artists"]),
            album=t["album"]["name"],
            year=self._year(t["album"].get("release_date", "")),
            duration_ms=t["duration_ms"],
            image_url=self._biggest_image(t["album"].get("images", [])),
            spotify_id=t["id"],
        )

    def playlist(self, playlist_id: str) -> List[TrackMeta]:
        out: List[TrackMeta] = []
        results = self._sp.playlist_items(playlist_id, additional_types=("track",))
        while results:
            for item in results["items"]:
                t = item.get("track")
                if not t or t.get("is_local"):
                    continue
                out.append(TrackMeta(
                    title=t["name"],
                    artist=", ".join(a["name"] for a in t["artists"]),
                    album=t["album"]["name"],
                    year=self._year(t["album"].get("release_date", "")),
                    duration_ms=t["duration_ms"],
                    image_url=self._biggest_image(t["album"].get("images", [])),
                    spotify_id=t["id"],
                ))
            results = self._sp.next(results) if results.get("next") else None
        return out

    def album(self, album_id: str) -> List[TrackMeta]:
        album = self._sp.album(album_id)
        year = self._year(album.get("release_date", ""))
        img = self._biggest_image(album.get("images", []))
        out: List[TrackMeta] = []
        results = self._sp.album_tracks(album_id)
        while results:
            for t in results["items"]:
                out.append(TrackMeta(
                    title=t["name"],
                    artist=", ".join(a["name"] for a in t["artists"]),
                    album=album["name"],
                    year=year,
                    duration_ms=t["duration_ms"],
                    image_url=img,
                    spotify_id=t["id"],
                ))
            results = self._sp.next(results) if results.get("next") else None
        return out
