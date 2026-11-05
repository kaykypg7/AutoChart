# Clone Hero Auto-Charter

Standalone Windows desktop app. Paste a YouTube or Spotify URL (or any search query), it downloads the audio, analyzes the track with librosa + a heuristic drum transcriber, and drops a playable Clone Hero chart folder into `C:\Users\Kayky\Documents\Clone Hero\Songs\`.

## Features (v1)

- YouTube URL, YouTube playlist, YouTube search text
- Spotify track / playlist / album URL (metadata-resolved → downloaded from YouTube)
- `[ExpertSingle]` guitar chart (pitch class → fret, 16th grid, HOPO, chords, open notes, Star Power)
- `[ExpertDrums]` chart from band-filtered onset detection (kick / snare / hat / cymbal)
- Section events in `[Events]` from novelty-curve segmentation
- Album art fetched from YouTube thumbnail → Spotify art → iTunes Search API fallback
- Modern dark CustomTkinter GUI with queued multi-song pipeline + cancel

## Build it (one shot)

```powershell
cd C:\Users\Kayky\Documents\CloneHeroAutoChart
.\build\make_release.ps1
```

Script auto-creates `.venv`, installs `requirements.txt`, downloads ffmpeg into `resources/`, runs PyInstaller. Output: `dist\CloneHeroAutoChart.exe` (~250 MB with libraries).

## Run from source during development

```powershell
cd C:\Users\Kayky\Documents\CloneHeroAutoChart
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# place ffmpeg.exe and ffprobe.exe into resources/ (or have them on PATH)
python main.py
```

## First-run setup

1. Click **Settings**.
2. Set **Output folder** (default is already Clone Hero Songs).
3. For Spotify URL support, register a free app at <https://developer.spotify.com/dashboard>, copy the Client ID + Secret into Settings.
4. Click **Save**.

Then paste a URL or search text in the input bar and hit **Go**.

## v2 upgrades (not shipped)

- **Demucs** stem separation → per-stem onsets (guitar/bass stem splitting; +~2 GB torch dep).
- **ADTOF** TensorFlow CNN drum transcriber replacing the heuristic in `core/drum_transcriber.py`.
- `.sng` binary packaging.
- Background video.mp4 synced from YouTube video stream.
- Auto Hard / Medium / Easy difficulties.
- Lyrics track from Spotify / Musixmatch.
- Chord detection via chordino.

## Project layout

```
main.py                  entry point
requirements.txt
gui/
  app.py                 CTk root, job queue, event drain
  screens.py             Input / Results / Progress / Settings
  widgets.py             ProgressRow, ResultCard, LogView
  theme.py               colors + fonts
core/
  resolver.py            classify URL vs search
  spotify_client.py      spotipy wrapper
  search.py              yt-dlp ytsearch + playlist
  downloader.py          yt-dlp -> .ogg
  art.py                 album art fetch
  analyzer.py            librosa tempo / onsets / chroma / sections
  drum_transcriber.py    band-filtered drum onsets (heuristic)
  charter.py             onsets+chroma -> ChartNote list
  chart_writer.py        notes.chart text emitter
  ini_writer.py          song.ini writer
  packager.py            folder assembly
  pipeline.py            orchestration
  config.py              settings + runtime env
  errors.py              typed exceptions
build/
  build.spec             PyInstaller spec
  make_release.ps1       one-shot build script
resources/
  ffmpeg.exe, ffprobe.exe  (populated by build script)
```

## Honesty disclaimer

Auto-generated charts will not match hand-charted ones. Baker's existing Playboi Carti charts in the Songs folder (Magnolia, EVILJ0RDAN, Fell In Luv, Mileage, NewwChoppa) are hand-charted by community charters and will always feel better. The v1 output is for drafting / jamming / adding scaffolding you then tune in Moonscraper.
