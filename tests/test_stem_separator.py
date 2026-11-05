"""Minimal tests for core.stem_separator.

The heavy path (actual separation) requires demucs + torch installed and
takes 2-5 minutes per song, so it's marked slow/integration. These tests
only exercise the import surface and the cache-hit short-circuit.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import stem_separator


class TestIsAvailable:
    def test_returns_bool(self):
        result = stem_separator.is_available()
        assert isinstance(result, bool)


class TestCacheHit:
    def test_cache_hit_short_circuits(self, tmp_path: Path):
        """If a guitar.wav already exists with content, separate_guitar_stem
        returns its path without touching the model."""
        audio = tmp_path / "song.ogg"
        audio.write_bytes(b"x" * 2048)  # dummy input — won't be opened

        stems_dir = audio.with_suffix(audio.suffix + ".stems")
        stems_dir.mkdir()
        cached_guitar = stems_dir / "guitar.wav"
        cached_guitar.write_bytes(b"y" * 4096)

        out = stem_separator.separate_guitar_stem(audio)
        assert out == cached_guitar
        assert out.read_bytes() == b"y" * 4096  # untouched

    def test_cache_hit_rejects_tiny_file(self, tmp_path: Path):
        """A sub-1KB guitar.wav is treated as corrupt and would trigger
        re-separation — but without demucs installed we expect ImportError.
        Skip the import side if demucs isn't available."""
        if not stem_separator.is_available():
            pytest.skip("demucs/torch not installed")
        audio = tmp_path / "song.ogg"
        audio.write_bytes(b"x" * 2048)
        stems_dir = audio.with_suffix(audio.suffix + ".stems")
        stems_dir.mkdir()
        tiny = stems_dir / "guitar.wav"
        tiny.write_bytes(b"\x00" * 128)  # too small to count as cached
        # No real audio to separate — we only care that it didn't take
        # the cache path. Expect some error, but NOT a cache return.
        with pytest.raises(Exception):
            stem_separator.separate_guitar_stem(audio)
