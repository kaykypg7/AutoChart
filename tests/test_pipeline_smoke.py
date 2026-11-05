"""
End-to-end audio-side smoke test: feed the 30s sine fixture through
analyze_audio -> build_chart -> write_chart and assert a valid .chart file
drops out the other end. Also runs a stage-by-stage perf benchmark that
records wall time and peak RSS per stage, written to
tests/.benchmarks/last.json so CI can trend it.

The downloader stage is skipped — this smoke test owns the local audio file
directly. Download/network coverage is a separate (integration) concern.
"""
from __future__ import annotations

import json
import os
import time
import tracemalloc
from pathlib import Path

import pytest

from core.chart_writer import ChartMeta, write_chart
from tests.fixtures.gen_fixture import DEFAULT_PATH, generate


REQUIRED_SECTIONS = ("[Song]", "[SyncTrack]", "[Events]", "[ExpertSingle]")


@pytest.fixture(scope="session")
def sine_wav() -> Path:
    if not DEFAULT_PATH.exists():
        generate(DEFAULT_PATH)
    assert DEFAULT_PATH.exists(), "fixture generation failed"
    assert DEFAULT_PATH.stat().st_size > 1024, "fixture suspiciously small"
    return DEFAULT_PATH


@pytest.fixture
def librosa_available():
    try:
        import librosa  # noqa: F401
    except ImportError:
        pytest.skip("librosa not installed in this environment")


@pytest.mark.slow
def test_pipeline_smoke_sine_wav(sine_wav, librosa_available, tmp_path: Path):
    """
    analyze_audio -> build_chart -> write_chart on a 30s sine input must
    produce a non-empty .chart with every required section. A sine has no
    percussive content, so note counts can be near-zero; the test asserts
    structural soundness, not musicality.
    """
    from core.analyzer import analyze_audio
    from core.charter import build_chart

    analysis = analyze_audio(sine_wav)
    assert analysis.duration_s > 25.0, f"duration lost: {analysis.duration_s}"
    assert analysis.bpm > 0, f"bpm not detected: {analysis.bpm}"

    cd = build_chart(analysis, drums=None)
    assert cd.bpm_milli > 0
    assert len(cd.sync) >= 2  # at least one TS and one B

    meta = ChartMeta(name="Sine 440", artist="Fixture", album="Smoke")
    out = write_chart(tmp_path / "notes.chart", cd, meta)
    assert out.exists() and out.stat().st_size > 0

    text = out.read_text(encoding="utf-8")
    for section in REQUIRED_SECTIONS:
        assert section in text, f"missing section {section} in output"


@pytest.mark.slow
def test_pipeline_perf_benchmark(sine_wav, librosa_available, tmp_path: Path):
    """
    Benchmark per-stage wall time + peak-memory. Writes a JSON report to
    tests/.benchmarks/last.json for CI trend tracking. Thresholds are loose
    sanity checks (nothing should spike 10x unexpectedly).
    """
    from core.analyzer import analyze_audio
    from core.charter import build_chart

    stages: dict[str, dict[str, float]] = {}

    tracemalloc.start()
    t0 = time.perf_counter()
    analysis = analyze_audio(sine_wav)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    stages["analyze_audio"] = {"wall_s": time.perf_counter() - t0, "peak_mb": peak / 1024 / 1024}

    tracemalloc.start()
    t0 = time.perf_counter()
    cd = build_chart(analysis, drums=None)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    stages["build_chart"] = {"wall_s": time.perf_counter() - t0, "peak_mb": peak / 1024 / 1024}

    tracemalloc.start()
    t0 = time.perf_counter()
    write_chart(tmp_path / "notes.chart", cd, ChartMeta(name="x", artist="x"))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    stages["write_chart"] = {"wall_s": time.perf_counter() - t0, "peak_mb": peak / 1024 / 1024}

    report_dir = Path(__file__).parent / ".benchmarks"
    report_dir.mkdir(exist_ok=True)
    report = {
        "fixture": str(sine_wav),
        "fixture_bytes": sine_wav.stat().st_size,
        "stages": stages,
        "total_s": sum(s["wall_s"] for s in stages.values()),
    }
    (report_dir / "last.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Sanity thresholds — generous. A 30s sine on any reasonable laptop
    # finishes analyze_audio well under 60s and uses < 1 GB peak.
    assert stages["analyze_audio"]["wall_s"] < 60.0
    assert stages["analyze_audio"]["peak_mb"] < 1024.0
    assert stages["write_chart"]["wall_s"] < 5.0


def test_fixture_generator_produces_wav(tmp_path: Path):
    """Generator itself works even when ffmpeg isn't on PATH (numpy fallback)."""
    out = tmp_path / "tiny.wav"
    generate(out, freq=440, duration=1, sr=22050)
    assert out.exists()
    assert out.stat().st_size > 44  # at least a wav header
