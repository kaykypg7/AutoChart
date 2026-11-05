from dataclasses import dataclass
from pathlib import Path

from core.charter import ChartData, RESOLUTION


@dataclass
class ChartMeta:
    name: str
    artist: str
    album: str = ""
    year: str = ""
    charter: str = "autochart"
    offset: float = 0.0
    difficulty: int = 3
    preview_start_ms: int = 0
    preview_end_ms: int = 30000
    genre: str = "Custom"
    media_type: str = "cd"
    music_stream: str = "song.ogg"


def _song_block(m: ChartMeta) -> list:
    def q(s): return f'"{s}"'
    return [
        "[Song]",
        "{",
        f"  Name = {q(m.name)}",
        f"  Artist = {q(m.artist)}",
        f"  Charter = {q(m.charter)}",
        f"  Album = {q(m.album)}",
        f"  Year = {q(', ' + m.year) if m.year else q('')}",
        f"  Offset = {m.offset}",
        f"  Resolution = {RESOLUTION}",
        "  Player2 = bass",
        f"  Difficulty = {m.difficulty}",
        f"  PreviewStart = {m.preview_start_ms}",
        f"  PreviewEnd = {m.preview_end_ms}",
        f"  Genre = {q(m.genre)}",
        f"  MediaType = {q(m.media_type)}",
        f"  MusicStream = {q(m.music_stream)}",
        "}",
    ]


def _sync_block(cd: ChartData) -> list:
    # Sort by tick ascending; TS before B at same tick (hand-charted convention).
    rank = {"TS": 0, "B": 1}
    sync = sorted(cd.sync, key=lambda e: (int(e[0]), rank.get(e[1], 2)))
    lines = ["[SyncTrack]", "{"]
    for tick, kind, val in sync:
        lines.append(f"  {int(tick)} = {kind} {int(val)}")
    lines.append("}")
    return lines


def _events_block(cd: ChartData) -> list:
    """Combined [Events] block — section markers + lyric phrases.

    Clone Hero scans [Events] in tick-ascending order; within a tick, lyric
    sub-events must stay in their source order (`phrase_start`, then `lyric …`
    lines, then `phrase_end`). We preserve that by giving each row a two-part
    sort key: (tick, source_order_within_tick), where source_order is a stable
    index from the input list.
    """
    rows: list = []
    # Section events — dedup to one per tick (hand-charted convention).
    seen_sections = set()
    for ev in sorted(cd.events, key=lambda e: int(e.tick)):
        t = int(ev.tick)
        if t in seen_sections:
            continue
        seen_sections.add(t)
        rows.append((t, 0, 0, f'section {ev.label}'))

    # Lyric events — rank 1 so they fall AFTER a section marker that lands on
    # the same tick (section headers lead the measure).
    for idx, (tick, payload) in enumerate(cd.lyrics_events):
        rows.append((int(tick), 1, idx, payload))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    lines = ["[Events]", "{"]
    for tick, _, _, payload in rows:
        lines.append(f'  {tick} = E "{payload}"')
    lines.append("}")
    return lines


def _notes_block(name: str, notes, sp) -> list:
    # Clone Hero's scanner requires events within a section to be in
    # monotonically increasing tick order. At the same tick, N comes before S
    # (matches the hand-charted convention). Sort key: (tick, type_rank).
    merged = []
    for n in notes:
        t = int(n.tick)
        if t < 0:
            continue
        merged.append((t, 0, f"{t} = N {int(n.fret)} {int(n.length)}"))
    for p in sp:
        t = int(p.tick)
        if t < 0:
            continue
        merged.append((t, 1, f"{t} = S 2 {int(p.length)}"))
    merged.sort(key=lambda x: (x[0], x[1]))
    lines = [f"[{name}]", "{"]
    for _, _, body in merged:
        lines.append(f"  {body}")
    lines.append("}")
    return lines


def write_chart(path: Path, cd: ChartData, meta: ChartMeta) -> Path:
    lines: list = []
    lines += _song_block(meta)
    lines += _sync_block(cd)
    lines += _events_block(cd)
    lines += _notes_block("ExpertSingle", cd.expert_single, cd.sp_guitar)
    if cd.hard_single:
        lines += _notes_block("HardSingle", cd.hard_single, cd.sp_guitar)
    if cd.medium_single:
        lines += _notes_block("MediumSingle", cd.medium_single, cd.sp_guitar)
    if cd.easy_single:
        lines += _notes_block("EasySingle", cd.easy_single, cd.sp_guitar)
    if cd.expert_drums:
        lines += _notes_block("ExpertDrums", cd.expert_drums, cd.sp_drums)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
