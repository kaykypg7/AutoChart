import os
import queue
import subprocess
import threading
import traceback
from pathlib import Path
from typing import List, Optional

import customtkinter as ctk

from gui import theme as T
from gui.screens import InputScreen, ProgressScreen, ResultsScreen, SettingsScreen
from core import pipeline as pipeline_mod
from core.config import Settings
from core.errors import InputError, SpotifyAuthError
from core.resolver import InputKind, resolve
from core.search import SearchResult, yt_playlist, yt_search
from core.spotify_client import SpotifyClient, TrackMeta


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.title("Clone Hero Auto-Charter")
        self.geometry("980x680")
        self.configure(fg_color=T.BG)

        self.settings = Settings.load()
        self._event_q: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._latest_folder: Optional[Path] = None

        self._build_top_bar()
        self._container = ctk.CTkFrame(self, fg_color=T.BG)
        self._container.pack(fill="both", expand=True)
        self._container.grid_columnconfigure(0, weight=1)
        self._container.grid_rowconfigure(0, weight=1)

        self.input_screen = InputScreen(self._container, on_submit=self._on_submit_input)
        self.results_screen = ResultsScreen(self._container,
                                            on_confirm=self._on_confirm_results,
                                            on_back=self._show_input)
        self.progress_screen = ProgressScreen(self._container,
                                              on_cancel=self._cancel_queue,
                                              on_open_folder=self._open_output_folder,
                                              on_new_chart=self._show_input)
        self._show_input()

        self._pending_results: List[SearchResult] = []
        self._pending_spotify: List[TrackMeta] = []

        self.after(80, self._drain_events)

    def _build_top_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=T.PANEL, height=48, corner_radius=0)
        bar.pack(fill="x", side="top")
        ctk.CTkLabel(bar, text="Clone Hero Auto-Charter",
                     font=T.FONT_H2, text_color=T.TEXT).pack(side="left", padx=T.PAD, pady=10)
        ctk.CTkButton(
            bar, text="Open Songs Folder", width=160,
            fg_color=T.PANEL_HI, hover_color=T.ACCENT, text_color=T.TEXT,
            command=self._open_output_folder,
        ).pack(side="right", padx=(0, T.PAD), pady=8)
        ctk.CTkButton(
            bar, text="Settings", width=100,
            fg_color=T.PANEL_HI, hover_color=T.ACCENT, text_color=T.TEXT,
            command=self._open_settings,
        ).pack(side="right", padx=4, pady=8)

    def _show(self, which: ctk.CTkFrame) -> None:
        for w in (self.input_screen, self.results_screen, self.progress_screen):
            w.grid_forget()
        which.grid(row=0, column=0, sticky="nsew")

    def _show_input(self) -> None: self._show(self.input_screen)

    def _open_settings(self) -> None:
        SettingsScreen(self, self.settings, on_save=self._on_save_settings)

    def _on_save_settings(self, s: Settings) -> None:
        self.settings = s

    def _open_output_folder(self) -> None:
        target = str(self._latest_folder) if (self._latest_folder and self._latest_folder.exists()) \
                 else self.settings.output_folder
        try:
            os.makedirs(target, exist_ok=True)
            subprocess.Popen(["explorer", target])
        except OSError:
            pass

    # ---- input flow ----

    def _on_submit_input(self, raw: str) -> None:
        try:
            parsed = resolve(raw)
        except InputError as e:
            self._toast(str(e))
            return

        if parsed.kind == InputKind.YT_VIDEO:
            self._pending_results = []
            self._pending_spotify = []
            job = pipeline_mod.Job(url=parsed.raw)
            self._start_queue([job])
        elif parsed.kind == InputKind.YT_PLAYLIST:
            self._fetch_async(
                lambda: yt_playlist(parsed.raw, browser_cookies=self.settings.browser_cookies),
                self._show_search_results,
            )
        elif parsed.kind == InputKind.SEARCH_TEXT:
            self._fetch_async(
                lambda: yt_search(parsed.raw, limit=12,
                                  browser_cookies=self.settings.browser_cookies),
                self._show_search_results,
            )
        elif parsed.kind in (InputKind.SPOTIFY_TRACK, InputKind.SPOTIFY_PLAYLIST, InputKind.SPOTIFY_ALBUM):
            self._fetch_async(lambda: self._resolve_spotify(parsed),
                              self._show_spotify_tracks)
        else:
            self._toast("Unrecognized input.")

    def _resolve_spotify(self, parsed) -> List[TrackMeta]:
        if not (self.settings.spotify_client_id and self.settings.spotify_client_secret):
            raise SpotifyAuthError(
                "Open Settings and paste a Spotify Client ID + Secret to use Spotify URLs."
            )
        sp = SpotifyClient(self.settings.spotify_client_id,
                           self.settings.spotify_client_secret)
        if parsed.kind == InputKind.SPOTIFY_TRACK:
            return [sp.track(parsed.id)]
        if parsed.kind == InputKind.SPOTIFY_PLAYLIST:
            return sp.playlist(parsed.id)
        return sp.album(parsed.id)

    def _fetch_async(self, fn, done_cb) -> None:
        def _run():
            try:
                data = fn()
                self._event_q.put(("fetch_done", done_cb, data))
            except Exception as e:
                self._event_q.put(("toast", None, str(e)))
        threading.Thread(target=_run, daemon=True).start()
        self._toast("Searching...", persist=False)

    def _show_search_results(self, results: List[SearchResult]) -> None:
        self._pending_results = results
        self._pending_spotify = []
        self.results_screen.set_results(results)
        self._show(self.results_screen)

    def _show_spotify_tracks(self, tracks: List[TrackMeta]) -> None:
        self._pending_spotify = tracks
        self._pending_results = []
        wrapped = [
            SearchResult(url="", title=t.title,
                         uploader=f"{t.artist} · {t.album}",
                         duration_s=int(t.duration_ms / 1000),
                         thumbnail=t.image_url)
            for t in tracks
        ]
        self.results_screen.set_results(wrapped)
        self._show(self.results_screen)

    def _on_confirm_results(self, indices: List[int]) -> None:
        jobs: List[pipeline_mod.Job] = []
        if self._pending_spotify:
            for i in indices:
                t = self._pending_spotify[i]
                query = f"ytsearch1:{t.artist} - {t.title}"
                jobs.append(pipeline_mod.Job(
                    url=query, title_hint=t.title, artist_hint=t.artist,
                    album_hint=t.album, year_hint=t.year,
                    image_url_hint=t.image_url,
                    search_query_itunes=f"{t.artist} {t.album}",
                ))
        elif self._pending_results:
            for i in indices:
                r = self._pending_results[i]
                jobs.append(pipeline_mod.Job(url=r.url, title_hint=r.title))
        if jobs:
            self._start_queue(jobs)

    # ---- pipeline ----

    def _start_queue(self, jobs: List[pipeline_mod.Job]) -> None:
        self._cancel.clear()
        titles = [j.title_hint or j.url for j in jobs]
        self.progress_screen.reset(titles)
        self._show(self.progress_screen)

        pipe = pipeline_mod.Pipeline(self.settings, cancel=self._cancel)

        def _run():
            success = 0
            for i, job in enumerate(jobs):
                if self._cancel.is_set():
                    break
                def _cb(p, idx=i):
                    self._event_q.put(("progress", idx, p))
                res = pipe.run_job(job, _cb)
                if res.folder:
                    self._event_q.put(("folder", None, res.folder))
                if res.stage == "done":
                    success += 1
            self._event_q.put(("queue_done", success, len(jobs)))

        self._worker = threading.Thread(target=_run, daemon=True)
        self._worker.start()

    def _cancel_queue(self) -> None:
        self._cancel.set()

    def _drain_events(self) -> None:
        try:
            while True:
                evt = self._event_q.get_nowait()
                kind = evt[0]
                if kind == "progress":
                    _, idx, p = evt
                    self.progress_screen.update_row(idx, p.pct, p.message, p.stage)
                elif kind == "queue_done":
                    _, success, total = evt
                    self.progress_screen.mark_complete(success, total)
                    if self.settings.open_folder_after_chart and self._latest_folder:
                        self._open_output_folder()
                elif kind == "fetch_done":
                    _, cb, data = evt
                    try: cb(data)
                    except Exception as e: self._toast(str(e))
                elif kind == "toast":
                    _, _, msg = evt
                    self._toast(msg)
                elif kind == "folder":
                    _, _, folder = evt
                    self._latest_folder = folder
        except queue.Empty:
            pass
        self.after(80, self._drain_events)

    def _toast(self, message: str, persist: bool = True) -> None:
        try:
            self.title(f"Clone Hero Auto-Charter — {message[:60]}")
        except Exception:
            pass


def run() -> None:
    try:
        app = App()
        app.mainloop()
    except Exception:
        traceback.print_exc()
        raise
