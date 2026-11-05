"""
chart_writer round-trip: build a ChartData in memory, write it to a .chart text
file via write_chart, parse it back with a minimal section parser, and assert
every note, sync event, section event, and star-power phrase survived.

The parser is intentionally local to the test — the writer is the only writer
in the project, so the test needs its own reader to prove bytes-in == bytes-out
at the semantic level (not the whitespace level).
"""
import re
from pathlib import Path

import pytest

from core.chart_writer import ChartMeta, write_chart
from core.charter import ChartData, ChartNote, SectionEvent, StarPhrase


_SECTION_RE = re.compile(r"\[(?P<name>[^\]]+)\]\s*\{(?P<body>.*?)\}", re.DOTALL)
_NOTE_RE = re.compile(r"\s*(?P<tick>\d+)\s*=\s*N\s+(?P<fret>\d+)\s+(?P<len>\d+)")
_SP_RE = re.compile(r"\s*(?P<tick>\d+)\s*=\s*S\s+2\s+(?P<len>\d+)")
_SYNC_B_RE = re.compile(r"\s*(?P<tick>\d+)\s*=\s*B\s+(?P<val>\d+)")
_SYNC_TS_RE = re.compile(r"\s*(?P<tick>\d+)\s*=\s*TS\s+(?P<val>\d+)")
_EVENT_RE = re.compile(r'\s*(?P<tick>\d+)\s*=\s*E\s+"section\s+(?P<label>[^"]+)"')
_META_RE = re.compile(r"\s*(?P<key>\w+)\s*=\s*(?P<val>.+?)\s*$", re.MULTILINE)


def _parse_sections(text: str) -> dict:
    return {m.group("name"): m.group("body") for m in _SECTION_RE.finditer(text)}


def _parse_notes(body: str):
    notes = [
        ChartNote(tick=int(m.group("tick")), fret=int(m.group("fret")), length=int(m.group("len")))
        for m in _NOTE_RE.finditer(body)
    ]
    sp = [
        StarPhrase(tick=int(m.group("tick")), length=int(m.group("len")))
        for m in _SP_RE.finditer(body)
    ]
    return notes, sp


def _parse_sync(body: str):
    out = []
    for m in _SYNC_TS_RE.finditer(body):
        out.append((int(m.group("tick")), "TS", int(m.group("val"))))
    for m in _SYNC_B_RE.finditer(body):
        out.append((int(m.group("tick")), "B", int(m.group("val"))))
    return out


def _parse_events(body: str):
    return [SectionEvent(tick=int(m.group("tick")), label=m.group("label"))
            for m in _EVENT_RE.finditer(body)]


def _parse_song_meta(body: str) -> dict:
    out = {}
    for m in _META_RE.finditer(body):
        key, val = m.group("key"), m.group("val").strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        out[key] = val
    return out


def _sample_chart() -> ChartData:
    cd = ChartData(bpm_milli=120_000)
    cd.sync = [(0, "TS", 4), (0, "B", 120_000), (768, "B", 140_000)]
    cd.expert_single = [
        ChartNote(tick=0, fret=0, length=0),
        ChartNote(tick=192, fret=2, length=48),
        ChartNote(tick=192, fret=5, length=0),      # HOPO forcing flag
        ChartNote(tick=384, fret=7, length=0),      # open note
        ChartNote(tick=576, fret=3, length=96),
    ]
    cd.expert_drums = [
        ChartNote(tick=0, fret=0, length=0),
        ChartNote(tick=0, fret=66, length=0),       # 2x kick flag
        ChartNote(tick=192, fret=1, length=0),
        ChartNote(tick=384, fret=2, length=0),
        ChartNote(tick=768, fret=4, length=0),
    ]
    cd.sp_guitar = [StarPhrase(tick=0, length=768), StarPhrase(tick=1536, length=768)]
    cd.sp_drums = [StarPhrase(tick=0, length=768)]
    cd.events = [
        SectionEvent(tick=0, label="intro"),
        SectionEvent(tick=768, label="verse 1"),
        SectionEvent(tick=1536, label="chorus"),
    ]
    return cd


def _sample_meta() -> ChartMeta:
    return ChartMeta(
        name="Round Trip Test",
        artist="Starter",
        album="The Jar",
        year="2026",
        charter="autochart",
        offset=0.0,
        difficulty=4,
        preview_start_ms=5000,
        preview_end_ms=25000,
        genre="Rock",
        media_type="cd",
        music_stream="song.ogg",
    )


def test_round_trip_preserves_all_sections(tmp_path: Path):
    cd = _sample_chart()
    meta = _sample_meta()
    out = write_chart(tmp_path / "notes.chart", cd, meta)
    assert out.exists(), "write_chart must create the file"

    text = out.read_text(encoding="utf-8")
    sections = _parse_sections(text)
    assert set(sections) >= {"Song", "SyncTrack", "Events", "ExpertSingle", "ExpertDrums"}

    song = _parse_song_meta(sections["Song"])
    assert song["Name"] == meta.name
    assert song["Artist"] == meta.artist
    assert song["Album"] == meta.album
    assert song["Charter"] == meta.charter
    assert song["Difficulty"] == str(meta.difficulty)
    assert song["PreviewStart"] == str(meta.preview_start_ms)
    assert song["PreviewEnd"] == str(meta.preview_end_ms)
    assert song["MusicStream"] == meta.music_stream
    assert song["Resolution"] == "192"

    sync = _parse_sync(sections["SyncTrack"])
    assert sorted(sync) == sorted(cd.sync), f"sync mismatch: {sync} != {cd.sync}"

    events = _parse_events(sections["Events"])
    assert [(e.tick, e.label) for e in events] == [(e.tick, e.label) for e in cd.events]

    gnotes, gsp = _parse_notes(sections["ExpertSingle"])
    assert sorted((n.tick, n.fret, n.length) for n in gnotes) == \
           sorted((n.tick, n.fret, n.length) for n in cd.expert_single)
    assert sorted((p.tick, p.length) for p in gsp) == \
           sorted((p.tick, p.length) for p in cd.sp_guitar)

    dnotes, dsp = _parse_notes(sections["ExpertDrums"])
    assert sorted((n.tick, n.fret, n.length) for n in dnotes) == \
           sorted((n.tick, n.fret, n.length) for n in cd.expert_drums)
    assert sorted((p.tick, p.length) for p in dsp) == \
           sorted((p.tick, p.length) for p in cd.sp_drums)


def test_notes_section_is_monotonic_by_tick(tmp_path: Path):
    """Clone Hero's scanner requires events inside a section to be in
    monotonically increasing tick order."""
    cd = _sample_chart()
    out = write_chart(tmp_path / "notes.chart", cd, _sample_meta())
    body = _parse_sections(out.read_text(encoding="utf-8"))["ExpertSingle"]
    ticks = [int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s*=", body, re.MULTILINE)]
    assert ticks == sorted(ticks), f"ExpertSingle ticks not sorted: {ticks}"


def test_omits_drums_section_when_no_drum_notes(tmp_path: Path):
    cd = _sample_chart()
    cd.expert_drums = []
    out = write_chart(tmp_path / "notes.chart", cd, _sample_meta())
    sections = _parse_sections(out.read_text(encoding="utf-8"))
    assert "ExpertDrums" not in sections


def test_section_events_deduplicated_at_same_tick(tmp_path: Path):
    cd = _sample_chart()
    cd.events = [
        SectionEvent(tick=0, label="intro"),
        SectionEvent(tick=0, label="intro-dup"),       # same tick, must drop
        SectionEvent(tick=192, label="verse"),
    ]
    out = write_chart(tmp_path / "notes.chart", cd, _sample_meta())
    events = _parse_events(_parse_sections(out.read_text(encoding="utf-8"))["Events"])
    ticks = [e.tick for e in events]
    assert ticks == sorted(ticks)
    assert len(ticks) == len(set(ticks)), "events must be unique by tick"


def test_write_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "nested" / "deep" / "notes.chart"
    assert not target.parent.exists()
    write_chart(target, _sample_chart(), _sample_meta())
    assert target.exists()


def test_downchart_sections_written(tmp_path: Path):
    cd = _sample_chart()
    cd.hard_single = [ChartNote(tick=0, fret=0, length=0)]
    cd.medium_single = [ChartNote(tick=0, fret=1, length=0)]
    cd.easy_single = [ChartNote(tick=0, fret=2, length=0)]
    out = write_chart(tmp_path / "notes.chart", cd, _sample_meta())
    sections = _parse_sections(out.read_text(encoding="utf-8"))
    assert "HardSingle" in sections
    assert "MediumSingle" in sections
    assert "EasySingle" in sections
    hnotes, hsp = _parse_notes(sections["HardSingle"])
    assert len(hnotes) == 1 and hnotes[0].fret == 0
    mnotes, msp = _parse_notes(sections["MediumSingle"])
    assert len(mnotes) == 1 and mnotes[0].fret == 1
    enotes, esp = _parse_notes(sections["EasySingle"])
    assert len(enotes) == 1 and enotes[0].fret == 2


@pytest.mark.integration
def test_moonscraper_cli_validation(tmp_path: Path):
    """
    Hands the written chart to Moonscraper's CLI validator if it exists on the
    machine (env var MOONSCRAPER_CLI). Skips otherwise so CI stays green on
    runners without the binary.
    """
    import os
    import shutil
    import subprocess

    cli = os.environ.get("MOONSCRAPER_CLI") or shutil.which("chart.exe")
    if not cli:
        pytest.skip("Moonscraper CLI not available (set MOONSCRAPER_CLI env var)")

    out = write_chart(tmp_path / "notes.chart", _sample_chart(), _sample_meta())
    result = subprocess.run([cli, "--validate", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, f"validator failed: {result.stdout}\n{result.stderr}"
