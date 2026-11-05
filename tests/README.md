# CloneHeroAutoChart — Test Suite

Pytest suite that pins the format-stable surface of the project: the chart
writer, the input resolver, the folder-name sanitizer, and an audio-side
pipeline smoke run on a generated 30-second sine fixture.

## Install

```bash
python -m pip install pytest pytest-cov
```

The pipeline smoke test additionally needs the project runtime deps (`librosa`,
`numpy`, `scipy`, `soundfile`). The resolver, packager, and chart_writer tests
only need stdlib + numpy.

## Run

```bash
# full suite
pytest

# fast tests only (skip slow pipeline + integration tests)
pytest -m "not slow and not integration"

# with coverage
pytest --cov=core --cov-report=term-missing
```

## Fixtures

Two fixtures are generated on demand:

- `sine_440hz_30s.wav` — pure 440 Hz tone, used by the pipeline smoke test.
  Librosa finds no onsets on a steady sine, so it's useless for charter
  invariants — only for exercising the audio ingest path.
- `pulse_train_30s.wav` — tempo-varied pulse train (120 / 150 / 96 BPM
  segments, 5 cycled pitch bands). Produces ~80-100 onsets that librosa
  tracks reliably; used by `test_charter_v12.py` and `test_charter_quality.py`
  so chord_rate / fret-coverage / HOPO-count invariants actually bite in CI
  instead of skipping on an empty track.

Pre-generate manually:

```bash
# sine (ffmpeg path)
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=30:sample_rate=22050" \
    -ac 1 -c:a pcm_s16le tests/fixtures/sine_440hz_30s.wav

# both via the generator (numpy fallback — no ffmpeg needed)
python tests/fixtures/gen_fixture.py
python -c "from tests.fixtures.gen_fixture import generate_pulse_train; generate_pulse_train()"
```

## Markers

- `slow` — pipeline smoke + perf benchmark (needs librosa).
- `integration` — tests that shell out to external tooling (Moonscraper CLI
  via `MOONSCRAPER_CLI` env var).

## Perf benchmark

`test_pipeline_perf_benchmark` writes `tests/.benchmarks/last.json` with
per-stage wall time and peak RSS so CI can trend performance over commits.
