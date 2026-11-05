"""
core/genre_profiles.py — Genre profile system for CloneHeroAutoChart (v2.0).

Different musical genres require different charting paradigms:
- Trap / Hip-Hop: minimal HOPO forcing (strum-heavy/snapped), wider chord intervals, relaxed density at low BPMs.
- Rock: expressive HOPOs (tight consecutive hammer-ons/pull-offs), punchier sustains, higher density per bar.
- Pop: moderate chords, crisp sustains, accessible density.
- Metal: aggressive HOPOs, tight rapid runs, high density per bar, short sustains.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


GENRE_PROFILES: Dict[str, Dict[str, Any]] = {
    "trap": {
        "chord_rate_target": (0.20, 0.35),
        "hopo_rate_target": (0.00, 0.02),
        "npb_cap_by_bpm": {
            (0, 90):    14,
            (90, 120):  18,
            (120, 150): 24,
            (150, 999): 30,
        },
        "sustain_default_ms": 300,
        "density_guards": {
            "min_gap_ticks": 48,
            "onset_percentile_floor": 20,
            "per_bar_hard_cap": 24,
        },
        "beat_fill_notes": 0,
        "sp_phrase_divisor": 8,
    },
    "rock": {
        "chord_rate_target": (0.15, 0.35),
        "hopo_rate_target": (0.03, 0.12),
        "npb_cap_by_bpm": {
            (0, 90):    20,      # was 16 (+25%)
            (90, 120):  25,      # was 22 (+14%)
            (120, 150): 34,      # was 29 (+17%)
            (150, 999): 42,      # was 36 (+17%)
        },
        "sustain_default_ms": 200,
        "hopo_trigger_gap_ticks": 48,              # LOWER = mais HOPOs (was 48, keep but add fallback)
        "hopo_force_streak": 3,                    # v2.3: forca HOPO quando 3+ notas consecutivas em <96 ticks com frets diferentes
        "density_guards": {
            "min_gap_ticks": 32,
            "onset_percentile_floor": 10,
            "per_bar_hard_cap": 32,
        },
        "beat_fill_notes": 2,
        "sp_phrase_divisor": 4,
    },
    "pop": {
        "chord_rate_target": (0.15, 0.30),
        "hopo_rate_target": (0.00, 0.04),
        "npb_cap_by_bpm": {
            (0, 90):    14,
            (90, 120):  18,
            (120, 150): 24,
            (150, 999): 30,
        },
        "sustain_default_ms": 250,
        "density_guards": {
            "min_gap_ticks": 48,
            "onset_percentile_floor": 20,
            "per_bar_hard_cap": 24,
        },
        "beat_fill_notes": 0,
        "sp_phrase_divisor": 8,
    },
    "metal": {
        "chord_rate_target": (0.20, 0.40),
        "hopo_rate_target": (0.05, 0.18),
        "npb_cap_by_bpm": {
            (0, 90):    18,
            (90, 120):  24,
            (120, 150): 32,
            (150, 999): 40,
        },
        "sustain_default_ms": 150,
        "density_guards": {
            "min_gap_ticks": 32,
            "onset_percentile_floor": 10,
            "per_bar_hard_cap": 36,
        },
        "beat_fill_notes": 2,
        "sp_phrase_divisor": 4,
    },
}

# "default" maps to trap settings per v2.0 spec
GENRE_PROFILES["default"] = dict(GENRE_PROFILES["trap"])


def get_genre_profile(name: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieve genre profile dictionary by name, falling back to 'default'.
    """
    if not name:
        return GENRE_PROFILES["default"]
    key = name.lower().strip()
    return GENRE_PROFILES.get(key, GENRE_PROFILES["default"])


def detect_genre(artist: str = "", title: str = "", spotify_genres: Optional[List[str]] = None) -> str:
    """
    Attempt to detect genre from Spotify metadata or artist/title keyword matching.
    Falls back to 'default'.
    """
    tokens = [artist, title]
    if spotify_genres:
        tokens.extend(spotify_genres)
    combined = " ".join(tokens).lower()

    # Metal
    metal_keywords = [
        "metal", "thrash", "deathcore", "metalcore", "death metal", "black metal",
        "slipknot", "metallica", "slayer", "megadeth", "iron maiden", "avenged sevenfold"
    ]
    if any(k in combined for k in metal_keywords):
        return "metal"

    # Rock / Indie / Punk
    rock_keywords = [
        "rock", "punk", "arctic monkeys", "indie", "grunge", "alternative", "foo fighters",
        "greenday", "nirvana", "muse", "radiohead", "strokes", "guitar hero"
    ]
    if any(k in combined for k in rock_keywords):
        return "rock"

    # Trap / Hip-Hop / Rap
    trap_keywords = [
        "trap", "hip hop", "hip-hop", "rap", "carti", "playboi", "drill", "uzi",
        "travis scott", "drake", "21 savage", "metro boomin", "future"
    ]
    if any(k in combined for k in trap_keywords):
        return "trap"

    # Pop / Synthpop / Dance
    pop_keywords = [
        "pop", "dance", "synthpop", "disco", "dua lipa", "taylor swift", "charli",
        "billie eilish", "ariana", "the weeknd"
    ]
    if any(k in combined for k in pop_keywords):
        return "pop"

    return "default"
