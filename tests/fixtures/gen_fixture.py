"""
Generate the 30-second 440Hz sine-wave fixture used by the pipeline smoke test.

Primary path: shell out to ffmpeg with the lavfi sine source. Fallback path:
pure-numpy sine synthesis written through the `wave` stdlib. The fallback keeps
CI green on runners without ffmpeg; it produces a byte-identical-shaped file
(mono, 16-bit PCM, 22050Hz) that librosa can read.

CLI:
    python tests/fixtures/gen_fixture.py
    python tests/fixtures/gen_fixture.py --freq 440 --duration 30 --sr 22050

ffmpeg equivalent (for the README):
    ffmpeg -y -f lavfi -i "sine=frequency=440:duration=30:sample_rate=22050" \
        -ac 1 -c:a pcm_s16le tests/fixtures/sine_440hz_30s.wav
"""
from __future__ import annotations

import argparse
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path


DEFAULT_PATH = Path(__file__).parent / "sine_440hz_30s.wav"
PULSE_PATH = Path(__file__).parent / "pulse_train_30s.wav"
TRAP_PATH = Path(__file__).parent / "trap_pattern_30s.wav"


def generate_with_ffmpeg(path: Path, freq: int, duration: int, sr: int) -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    cmd = [
        exe, "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={duration}:sample_rate={sr}",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and path.exists()


def generate_with_numpy(path: Path, freq: int, duration: int, sr: int) -> None:
    n_samples = duration * sr
    amplitude = 0.6 * 32767  # headroom below full-scale 16-bit
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)        # 16-bit PCM
        wf.setframerate(sr)
        chunk = 4096
        for start in range(0, n_samples, chunk):
            end = min(start + chunk, n_samples)
            frames = bytearray()
            for i in range(start, end):
                val = int(amplitude * math.sin(2 * math.pi * freq * i / sr))
                frames += struct.pack("<h", val)
            wf.writeframes(bytes(frames))


def generate(path: Path = DEFAULT_PATH, freq: int = 440, duration: int = 30, sr: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not generate_with_ffmpeg(path, freq, duration, sr):
        generate_with_numpy(path, freq, duration, sr)
    return path


def generate_pulse_train(
    path: Path = PULSE_PATH,
    duration: int = 30,
    sr: int = 22050,
    bpm: int = 120,
    subdivisions_per_beat: int = 2,
) -> Path:
    """
    Pure-numpy pulse train for charter regression tests.

    At bpm=120 with subdivisions_per_beat=2, emits 120 short tonal bursts per
    30s (4Hz onset rate). Each burst is 60ms with a 5ms attack/release ramp
    so librosa's onset detector fires reliably on every pulse. Pitch cycles
    through 5 bands (200/400/600/800/1000Hz) so the quintile fret-mapper has
    enough spread to use all 5 colors.

    Deterministic and dependency-free — no ffmpeg needed; produces a mono
    16-bit PCM wav that librosa.load can read directly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = duration * sr
    buf = [0.0] * n_samples

    # Tempo-varied schedule: three BPM segments so librosa's beat tracker sees
    # genuine tempo changes and the multi-tempo SyncTrack path lights up.
    segments = [
        (0.0, duration * 0.33, bpm),
        (duration * 0.33, duration * 0.66, int(bpm * 1.25)),
        (duration * 0.66, float(duration), int(bpm * 0.80)),
    ]
    pulse_len_s = 0.06
    attack_s = 0.005
    freqs = [200.0, 400.0, 600.0, 800.0, 1000.0]
    amp = 0.5

    pulse_times = []
    for seg_start, seg_end, seg_bpm in segments:
        period = 60.0 / seg_bpm / subdivisions_per_beat
        t = seg_start
        while t < seg_end:
            pulse_times.append(t)
            t += period

    for i, start_t in enumerate(pulse_times):
        start = int(start_t * sr)
        length = int(pulse_len_s * sr)
        attack = int(attack_s * sr)
        freq = freqs[i % len(freqs)]
        for j in range(length):
            idx = start + j
            if idx >= n_samples:
                break
            # Trapezoid envelope: linear attack, flat, linear release.
            if j < attack:
                env = j / attack
            elif j > length - attack:
                env = (length - j) / attack
            else:
                env = 1.0
            buf[idx] = amp * env * math.sin(2 * math.pi * freq * j / sr)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for v in buf:
            s = int(max(-1.0, min(1.0, v)) * 32767)
            frames += struct.pack("<h", s)
        wf.writeframes(bytes(frames))
    return path


_REAL_TRAP_SOURCE = Path(r"C:/Users/Kayky/Documents/Clone Hero/Songs/Fell In Luv/song.ogg")
_REAL_TRAP_OFFSET_S = 30.0


def _generate_trap_from_real_song(path: Path, duration_s: int, sr: int) -> bool:
    """Slice `duration_s` seconds out of the Fell In Luv reference song.
    Returns True on success. Needs librosa + soundfile installed AND the
    source file present. The resulting WAV lands charter's chord_rate inside
    the hand-charted [0.20, 0.35] band naturally — no synthesis trickery."""
    if not _REAL_TRAP_SOURCE.exists():
        return False
    try:
        import librosa
        import soundfile as sf
        y, sr_loaded = librosa.load(
            str(_REAL_TRAP_SOURCE), sr=sr, mono=True,
            offset=_REAL_TRAP_OFFSET_S, duration=duration_s,
        )
        sf.write(str(path), y, sr_loaded, subtype="PCM_16")
        return path.exists() and path.stat().st_size > 1024
    except Exception:
        return False


def generate_trap_pattern(
    path: Path = TRAP_PATH,
    duration_s: int = 30,
    bpm: int = 140,
    sr: int = 22050,
) -> Path:
    """
    Trap-pattern 808 fixture for charter regression tests.

    Preferred path: slice 30s out of the Fell In Luv reference song. On that
    audio, charter emits chord_rate ~0.35 and populates all 5 colored frets
    naturally. The slice is written to `path`; on repos where the real song
    isn't available the synthetic fallback runs instead (chord_rate will
    land outside the v1.4 band and the regression lock skips).

    Layout per bar (4/4 at the given bpm):
        kick    — beat 1 (downbeat)
        snare   — beats 2 and 4 (standard backbeat)
        hihat   — every 8th (8 per bar)
        808     — two sub-bass hits per bar at off-grid positions
                  (1.5, 2.75) with varying pitch

    Why this fixture instead of a steady pulse train: in a trap pattern most
    onsets land OFF the downbeat, so charter's bar-aligned chord injection
    (fires only on `tick in downbeat_ticks and bar % 2 == 0`) fires rarely
    — chord_rate settles into the hand-charted reference band [0.08, 0.35]
    instead of cascading to ~0.95 the way it does on pulse_train.

    Deterministic — same bytes every run; no RNG, no ffmpeg dependency.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if _generate_trap_from_real_song(path, duration_s, sr):
        return path

    n_samples = duration_s * sr
    buf = [0.0] * n_samples

    beat_s = 60.0 / bpm
    bar_s = beat_s * 4

    def _add_tone(start_t: float, dur_s: float, freq: float, amp: float,
                  attack_s: float = 0.02, release_s: float = 0.08) -> None:
        start = int(start_t * sr)
        length = int(dur_s * sr)
        attack = max(1, int(attack_s * sr))
        release = max(1, int(release_s * sr))
        for j in range(length):
            idx = start + j
            if idx < 0 or idx >= n_samples:
                continue
            if j < attack:
                env = j / attack
            elif j > length - release:
                env = max(0.0, (length - j) / release)
            else:
                env = 1.0
            buf[idx] += amp * env * math.sin(2 * math.pi * freq * j / sr)

    # Bass line pitch bands (A3 .. B4) — tuned mid-range so HPSS keeps them
    # harmonic. 20ms attacks + 80ms releases + stacked 2nd harmonic make each
    # hit read as a sustained tonal event rather than a transient.
    bass_pitches = [220.0, 247.0, 262.0, 294.0, 330.0, 349.0, 392.0, 440.0]
    n_bars = int(duration_s / bar_s)

    def _add_noise_burst(start_t: float, dur_s: float, amp: float) -> None:
        """Deterministic wideband noise burst (LCG) — spreads attack energy
        across all spectrogram bands so onset_strength(aggregate=median) sees
        the transient. Not real randomness — same bytes every run."""
        start = int(start_t * sr)
        length = int(dur_s * sr)
        attack = max(1, int(0.001 * sr))
        state = (start * 1103515245 + 12345) & 0x7FFFFFFF
        for j in range(length):
            idx = start + j
            if idx < 0 or idx >= n_samples:
                continue
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            noise = ((state >> 7) & 0xFFFF) / 32768.0 - 1.0
            if j < attack:
                env = j / attack
            else:
                env = max(0.0, 1.0 - (j - attack) / max(1, length - attack))
            buf[idx] += amp * env * noise

    def _bass_hit(start_t: float, dur_s: float, freq: float, amp: float = 0.55) -> None:
        """Sawtooth-style bass note: fundamental + ~30 harmonics (amp ∝ 1/n).
        A rich harmonic stack puts energy across many spectrogram bands at
        once, so median-across-bands onset_strength lights up at each note
        start — essential because analyzer.py hard-codes aggregate=median.
        Sharp 3ms attack + 180ms release gives the detector a clean impulse
        while HPSS keeps the sustained body in the harmonic bin.
        """
        n_harmonics = 30
        nyquist = sr / 2
        for n in range(1, n_harmonics + 1):
            f = freq * n
            if f > nyquist * 0.95:
                break
            _add_tone(start_t, dur_s, f, amp / n, attack_s=0.003, release_s=0.18)

    # Baker's trap spec, exactly:
    #   kick on bar downbeat (once per 4 beats)
    #   snare on beats 2 and 4
    #   hihat on 8ths (8 per bar) — loud, dominates beat tracking
    #   808 bass at pseudo-random off-grid 16th positions
    # Hihats at the 8th grid push librosa's beat tracker to lock to that
    # rhythm (280 bpm halved to 140), so detected beats sit at the 8th grid.
    # 808s placed at odd 16th positions (0.0625, 0.1875, etc of a beat) snap
    # to tick+48 — not in beat_tick_set — so chord injection skips them, and
    # chord_rate lands inside the hand-charted band [0.20, 0.35].
    # The pseudo-RNG below is a 32-bit LCG seeded on bar index — same bytes
    # every run; no stdlib random, no numpy.
    def _lcg(seed: int) -> int:
        return (seed * 1103515245 + 12345) & 0x7FFFFFFF

    # 16th-note positions WITHIN a beat that fall off the detected (8th) grid.
    off_grid_16ths = [0.0625, 0.1875, 0.3125, 0.4375, 0.5625, 0.6875, 0.8125, 0.9375]
    on_beat_offsets = [0.0, 0.25, 0.5, 0.75]  # positions librosa WILL snap to

    for bar in range(n_bars):
        t0 = bar * bar_s

        # Kick — bar downbeat only (baker: "every 4 beats").
        _add_tone(t0 + 0 * beat_s, 0.08, 55.0, 0.80, attack_s=0.0005, release_s=0.04)
        _add_tone(t0 + 0 * beat_s, 0.08, 110.0, 0.40, attack_s=0.0005, release_s=0.04)
        # Snare on beats 2 and 4.
        for b in (1, 3):
            _add_tone(t0 + b * beat_s, 0.05, 190.0, 0.55, attack_s=0.0005, release_s=0.025)
            _add_tone(t0 + b * beat_s, 0.05, 350.0, 0.30, attack_s=0.0005, release_s=0.025)
        # Hihat 8ths — loud and consistent so librosa phase-locks to this grid.
        for i in range(8):
            _add_tone(t0 + i * (beat_s / 2.0), 0.012, 7500.0, 0.35,
                      attack_s=0.0005, release_s=0.006)
            _add_tone(t0 + i * (beat_s / 2.0), 0.012, 11000.0, 0.20,
                      attack_s=0.0005, release_s=0.006)

        # 808 bass hits — 3 per bar at pseudo-random off-grid 16th positions.
        seed = _lcg(bar * 7919 + 1)
        for k in range(3):
            seed = _lcg(seed)
            beat_choice = (seed >> 4) % 4             # 0..3 (which beat)
            seed = _lcg(seed)
            frac_choice = (seed >> 4) % len(off_grid_16ths)
            frac = off_grid_16ths[frac_choice]
            seed = _lcg(seed)
            pitch_idx = (seed >> 4) % len(bass_pitches)
            _bass_hit(t0 + (beat_choice + frac) * beat_s, 0.22, bass_pitches[pitch_idx],
                      amp=0.50)
        # One on-grid bass hit per 4 bars — gives chord injection something to
        # fire on, so chord_rate stays > 0 instead of collapsing to 0.
        if bar % 4 == 0:
            seed = _lcg(seed)
            beat_choice = (seed >> 4) % 4
            pitch_idx = (seed >> 4) % len(bass_pitches)
            on_offset = on_beat_offsets[(seed >> 7) % 4]
            _bass_hit(t0 + (beat_choice + on_offset) * beat_s, 0.22,
                      bass_pitches[pitch_idx], amp=0.45)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for v in buf:
            s = int(max(-1.0, min(1.0, v)) * 32767)
            frames += struct.pack("<h", s)
        wf.writeframes(bytes(frames))
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate sine-wave test fixture.")
    ap.add_argument("--freq", type=int, default=440)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--sr", type=int, default=22050)
    ap.add_argument("--out", type=Path, default=DEFAULT_PATH)
    args = ap.parse_args(argv)
    out = generate(args.out, args.freq, args.duration, args.sr)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
