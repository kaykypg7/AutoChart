"""
core.reference parses a known hand-charted .chart and returns a
ReferencePriors with every documented field present and well-typed.

Ground truth: "Fell In Luv" (the first entry in reference.DEFAULT_CORPUS).
If the chart isn't present on this machine, the ground-truth checks skip
(so CI without the Songs library stays green), but the shape test runs on
an inline synthetic fixture so the parser surface is still exercised.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from core.reference import (
    DEFAULT_CORPUS,
    ParsedChart,
    ReferencePriors,
    aggregate_priors,
    compute_metrics,
    parse_notes_chart,
    parse_song_ini,
)


FELL_IN_LUV_FOLDER = next(
    (folder for name, folder in DEFAULT_CORPUS if name == "Fell In Luv"),
    None,
)


@pytest.fixture(scope="module")
def fell_in_luv_parsed() -> ParsedChart:
    if FELL_IN_LUV_FOLDER is None:
        pytest.skip("Fell In Luv not in DEFAULT_CORPUS")
    notes = FELL_IN_LUV_FOLDER / "notes.chart"
    if not notes.exists():
        pytest.skip(f"Fell In Luv notes.chart not found at {notes}")
    return parse_notes_chart(notes)


class TestFellInLuvGroundTruth:
    """Known numeric properties of the Fell In Luv hand-charted reference."""

    def test_parses_expert_single_track(self, fell_in_luv_parsed: ParsedChart):
        assert fell_in_luv_parsed.track == "ExpertSingle"

    def test_resolution_is_192(self, fell_in_luv_parsed: ParsedChart):
        assert fell_in_luv_parsed.resolution == 192

    def test_has_bpm_events(self, fell_in_luv_parsed: ParsedChart):
        assert len(fell_in_luv_parsed.bpm_events) >= 1
        assert fell_in_luv_parsed.bpm_events[0].bpm > 100.0

    def test_bpm_in_expected_range(self, fell_in_luv_parsed: ParsedChart):
        # Ground-truth avg BPM = 163.0; allow a wide band in case the chart
        # gets re-tempoed later.
        avg = fell_in_luv_parsed.avg_bpm()
        assert 140.0 <= avg <= 180.0, f"unexpected bpm: {avg}"

    def test_has_note_events(self, fell_in_luv_parsed: ParsedChart):
        # Ground truth: 746 N events across the ExpertSingle track.
        assert len(fell_in_luv_parsed.note_events) >= 500

    def test_has_star_power_phrases(self, fell_in_luv_parsed: ParsedChart):
        assert len(fell_in_luv_parsed.sp_phrases) >= 1

    def test_notes_sorted_by_tick(self, fell_in_luv_parsed: ParsedChart):
        ticks = [t for t, _, _ in fell_in_luv_parsed.note_events]
        assert ticks == sorted(ticks)

    def test_frets_in_allowed_set(self, fell_in_luv_parsed: ParsedChart):
        # Only fret 0-4 (colored notes) and 7 (open) are kept by the parser.
        allowed = {0, 1, 2, 3, 4, 7}
        for _, fret, _ in fell_in_luv_parsed.note_events:
            assert fret in allowed, f"unexpected fret {fret}"

    def test_duration_in_minutes_range(self, fell_in_luv_parsed: ParsedChart):
        dur = fell_in_luv_parsed.ticks_to_seconds(fell_in_luv_parsed.end_tick)
        assert 60 < dur < 600, f"unexpected duration: {dur}"


class TestMetricsAndPriors:
    """compute_metrics + aggregate_priors produce the full ReferencePriors
    schema with every field populated and typed as documented."""

    def test_metrics_fields_populated(self, fell_in_luv_parsed: ParsedChart):
        m = compute_metrics("Fell In Luv", fell_in_luv_parsed)
        assert m.name == "Fell In Luv"
        assert m.track == "ExpertSingle"
        assert m.bpm_avg > 0
        assert m.duration_s > 0
        assert m.bars > 0
        assert m.chord_group_count > 0
        assert 0.0 <= m.chord_rate <= 1.0
        assert 0.0 <= m.hopo_rate <= 1.0
        assert isinstance(m.fret_histogram, dict)
        assert isinstance(m.interval_histogram, dict)

    def test_priors_has_every_documented_field(self, fell_in_luv_parsed: ParsedChart):
        m = compute_metrics("Fell In Luv", fell_in_luv_parsed)
        priors = aggregate_priors([m])
        expected = {
            "source_charts", "corpus_bpm_avg", "corpus_duration_s",
            "median_notes_per_bar", "notes_per_bar_norm_bpm",
            "chord_rate", "hopo_rate", "sp_phrases_per_min",
            "fret_histogram", "fret_distribution",
            "interval_histogram", "interval_distribution",
            "per_bpm_bucket", "per_chart",
        }
        got = {f.name for f in dataclasses.fields(priors)}
        missing = expected - got
        assert not missing, f"ReferencePriors missing fields: {missing}"

    def test_fret_distribution_sums_to_one(self, fell_in_luv_parsed: ParsedChart):
        m = compute_metrics("Fell In Luv", fell_in_luv_parsed)
        priors = aggregate_priors([m])
        total = sum(priors.fret_distribution.values())
        assert abs(total - 1.0) < 1e-6, f"fret distribution sums to {total}"

    def test_interval_distribution_sums_to_one(self, fell_in_luv_parsed: ParsedChart):
        m = compute_metrics("Fell In Luv", fell_in_luv_parsed)
        priors = aggregate_priors([m])
        total = sum(priors.interval_distribution.values())
        # Can be 0 if there's < 2 note groups (impossible here) — otherwise ~1.
        assert abs(total - 1.0) < 1e-6

    def test_source_charts_list_matches_input(self, fell_in_luv_parsed: ParsedChart):
        m = compute_metrics("Fell In Luv", fell_in_luv_parsed)
        priors = aggregate_priors([m])
        assert priors.source_charts == ["Fell In Luv"]

    def test_per_bpm_bucket_is_dict_of_dicts(self, fell_in_luv_parsed: ParsedChart):
        m = compute_metrics("Fell In Luv", fell_in_luv_parsed)
        priors = aggregate_priors([m])
        assert isinstance(priors.per_bpm_bucket, dict)
        for bucket, values in priors.per_bpm_bucket.items():
            assert isinstance(bucket, str)
            assert isinstance(values, dict)
            assert "chord_rate" in values and "hopo_rate" in values


class TestSongIni:
    def test_parses_fell_in_luv_ini_if_present(self):
        if FELL_IN_LUV_FOLDER is None or not (FELL_IN_LUV_FOLDER / "song.ini").exists():
            pytest.skip("Fell In Luv song.ini not present")
        meta = parse_song_ini(FELL_IN_LUV_FOLDER / "song.ini")
        assert isinstance(meta, dict)
        assert meta  # at least one key

    def test_returns_empty_dict_for_missing_file(self, tmp_path: Path):
        assert parse_song_ini(tmp_path / "nope.ini") == {}


class TestSyntheticFallback:
    """Runs even without the Songs library — parser surface stays covered on
    bare CI runners."""

    def _write_minimal_chart(self, path: Path) -> None:
        path.write_text(
            "[Song]\n{\n  Resolution = 192\n}\n"
            "[SyncTrack]\n{\n  0 = TS 4\n  0 = B 120000\n  768 = B 140000\n}\n"
            "[Events]\n{\n  0 = E \"section intro\"\n}\n"
            "[ExpertSingle]\n{\n"
            "  0 = N 0 0\n"
            "  192 = N 1 0\n"
            "  192 = N 2 0\n"       # chord group (two notes same tick)
            "  384 = N 3 0\n"
            "  576 = N 4 0\n"
            "  768 = N 5 0\n"       # HOPO force
            "  960 = N 7 0\n"       # open
            "  1152 = S 2 192\n"    # star-power phrase
            "}\n",
            encoding="utf-8",
        )

    def test_parses_minimal_chart(self, tmp_path: Path):
        p = tmp_path / "notes.chart"
        self._write_minimal_chart(p)
        parsed = parse_notes_chart(p)
        assert parsed.resolution == 192
        assert len(parsed.bpm_events) == 2
        assert len(parsed.note_events) == 6     # fret 0,1,2,3,4,7 (HOPO 5 goes elsewhere)
        assert parsed.force_events == [768]
        assert parsed.sp_phrases == [(1152, 192)]
        assert parsed.track == "ExpertSingle"

    def test_metrics_from_minimal_chart(self, tmp_path: Path):
        p = tmp_path / "notes.chart"
        self._write_minimal_chart(p)
        parsed = parse_notes_chart(p)
        m = compute_metrics("synthetic", parsed)
        # One tick (192) has 2 notes → chord group with multi, so rate > 0.
        assert m.chord_group_with_multi >= 1
        assert m.chord_rate > 0.0

    def test_aggregate_from_two_charts(self, tmp_path: Path):
        a, b = tmp_path / "a.chart", tmp_path / "b.chart"
        self._write_minimal_chart(a)
        self._write_minimal_chart(b)
        pa, pb = parse_notes_chart(a), parse_notes_chart(b)
        ma = compute_metrics("A", pa)
        mb = compute_metrics("B", pb)
        priors = aggregate_priors([ma, mb])
        assert priors.source_charts == ["A", "B"]
        assert len(priors.per_chart) == 2

    def test_aggregate_empty_raises(self):
        with pytest.raises(ValueError):
            aggregate_priors([])
