from tkinter import filedialog
from typing import Callable, List, Optional

import customtkinter as ctk

from gui import theme as T
from gui.widgets import LogView, ProgressRow, ResultCard
from core.config import Settings


class InputScreen(ctk.CTkFrame):
    def __init__(self, master, on_submit: Callable[[str], None]):
        super().__init__(master, fg_color=T.BG)
        self.on_submit = on_submit
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        container = ctk.CTkFrame(self, fg_color=T.BG)
        container.grid(row=1, column=0, sticky="n", padx=40, pady=40)

        ctk.CTkLabel(
            container, text="Clone Hero Auto-Charter", font=T.FONT_H1,
            text_color=T.TEXT,
        ).pack(pady=(0, 4))
        ctk.CTkLabel(
            container, text="Paste a YouTube URL, a Spotify URL, or a search query",
            font=T.FONT_BODY, text_color=T.MUTED,
        ).pack(pady=(0, T.PAD))

        self.entry = ctk.CTkEntry(
            container, width=600, height=46, placeholder_text="https://... or search text",
            fg_color=T.PANEL, text_color=T.TEXT, font=T.FONT_H2,
            border_color=T.PANEL_HI, corner_radius=23,
        )
        self.entry.pack(pady=(0, T.PAD))
        self.entry.bind("<Return>", lambda _: self._fire())

        self.btn = ctk.CTkButton(
            container, text="Go", width=180, height=42,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color="#000",
            font=T.FONT_H2, corner_radius=21, command=self._fire,
        )
        self.btn.pack()

    def _fire(self) -> None:
        v = self.entry.get().strip()
        if v:
            self.on_submit(v)


class ResultsScreen(ctk.CTkFrame):
    def __init__(self, master, on_confirm: Callable[[List[int]], None],
                 on_back: Callable[[], None]):
        super().__init__(master, fg_color=T.BG)
        self.on_confirm = on_confirm
        self.on_back = on_back
        self._selected: set = set()
        self.results = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(self, fg_color=T.BG)
        bar.grid(row=0, column=0, sticky="ew", padx=T.PAD, pady=T.PAD)
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(bar, text="< Back", width=80, fg_color=T.PANEL,
                      hover_color=T.PANEL_HI, text_color=T.TEXT,
                      command=self.on_back).grid(row=0, column=0)
        self.header = ctk.CTkLabel(bar, text="Results", font=T.FONT_H2,
                                   text_color=T.TEXT, anchor="w")
        self.header.grid(row=0, column=1, sticky="ew", padx=T.PAD)
        self.go_btn = ctk.CTkButton(
            bar, text="Chart Selected (0)", width=180,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color="#000",
            command=self._fire, state="disabled",
        )
        self.go_btn.grid(row=0, column=2)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color=T.BG)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=T.PAD, pady=(0, T.PAD))
        self.scroll.grid_columnconfigure(0, weight=1)

    def set_results(self, results) -> None:
        for w in self.scroll.winfo_children():
            w.destroy()
        self._selected.clear()
        self.results = results
        self.header.configure(text=f"Results ({len(results)})")
        self.go_btn.configure(text="Chart Selected (0)", state="disabled")
        for i, r in enumerate(results):
            card = ResultCard(
                self.scroll, i, title=r.title,
                subtitle=r.uploader, duration_s=r.duration_s,
                on_toggle=self._on_toggle,
            )
            card.grid(row=i, column=0, sticky="ew", pady=4)

    def _on_toggle(self, idx: int, selected: bool) -> None:
        if selected:
            self._selected.add(idx)
        else:
            self._selected.discard(idx)
        n = len(self._selected)
        self.go_btn.configure(text=f"Chart Selected ({n})",
                              state="normal" if n else "disabled")

    def _fire(self) -> None:
        self.on_confirm(sorted(self._selected))


class ProgressScreen(ctk.CTkFrame):
    def __init__(self, master, on_cancel: Callable[[], None],
                 on_open_folder: Callable[[], None],
                 on_new_chart: Optional[Callable[[], None]] = None):
        super().__init__(master, fg_color=T.BG)
        self.on_cancel = on_cancel
        self.on_open_folder = on_open_folder
        self.on_new_chart = on_new_chart
        self.rows: List[ProgressRow] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(self, fg_color=T.BG)
        bar.grid(row=0, column=0, sticky="ew", padx=T.PAD, pady=T.PAD)
        bar.grid_columnconfigure(0, weight=1)
        self.header = ctk.CTkLabel(bar, text="Working...", font=T.FONT_H2,
                                   text_color=T.TEXT, anchor="w")
        self.header.grid(row=0, column=0, sticky="ew")
        self.cancel_btn = ctk.CTkButton(
            bar, text="Cancel", width=100, fg_color=T.PANEL,
            hover_color=T.ERR, text_color=T.TEXT, command=self.on_cancel,
        )
        self.cancel_btn.grid(row=0, column=1, padx=(T.PAD, 0))
        self.open_btn = ctk.CTkButton(
            bar, text="Open Songs Folder", width=180,
            fg_color=T.OK, hover_color="#86efac", text_color="#000",
            command=self.on_open_folder,
        )
        self.open_btn.grid(row=0, column=2, padx=(T.PAD, 0))
        self.open_btn.grid_remove()
        self.new_btn = ctk.CTkButton(
            bar, text="+ Chart More", width=130,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color="#000",
            command=self._on_new_chart_clicked,
        )
        self.new_btn.grid(row=0, column=3, padx=(T.PAD, 0))
        self.new_btn.grid_remove()

        self.scroll = ctk.CTkScrollableFrame(self, fg_color=T.BG)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=T.PAD, pady=(0, T.PAD))
        self.scroll.grid_columnconfigure(0, weight=1)

        self.log = LogView(self)
        self.log.grid(row=2, column=0, sticky="ew", padx=T.PAD, pady=(0, T.PAD))
        self.log.configure(height=120)

    def _on_new_chart_clicked(self) -> None:
        if self.on_new_chart:
            self.on_new_chart()

    def reset(self, titles: List[str]) -> None:
        for w in self.scroll.winfo_children():
            w.destroy()
        self.rows.clear()
        for i, t in enumerate(titles):
            row = ProgressRow(self.scroll, title=t)
            row.grid(row=i, column=0, sticky="ew", pady=6)
            self.rows.append(row)
        self.open_btn.grid_remove()
        self.new_btn.grid_remove()
        self.cancel_btn.configure(state="normal")
        self.header.configure(text=f"Working... (0 of {len(titles)})")

    def update_row(self, idx: int, pct: float, message: str, stage: str) -> None:
        if 0 <= idx < len(self.rows):
            color = T.ACCENT
            if stage == "done": color = T.OK
            elif stage == "failed": color = T.ERR
            self.rows[idx].update_progress(pct, message, color)
        self.log.append(f"[{idx+1}] {stage}: {message}")

    def mark_complete(self, success: int, total: int) -> None:
        self.header.configure(text=f"Done — {success}/{total} succeeded")
        self.cancel_btn.configure(state="disabled")
        self.open_btn.grid()
        if self.on_new_chart:
            self.new_btn.grid()


class SettingsScreen(ctk.CTkToplevel):
    def __init__(self, master, settings: Settings, on_save: Callable[[Settings], None]):
        super().__init__(master)
        self.title("Settings")
        self.geometry("520x600")
        self.configure(fg_color=T.BG)
        self.settings = settings
        self.on_save = on_save

        row = 0
        def _label(text):
            nonlocal row
            ctk.CTkLabel(self, text=text, text_color=T.TEXT,
                         font=T.FONT_BODY, anchor="w").grid(row=row, column=0,
                                                             sticky="ew", padx=T.PAD,
                                                             pady=(T.PAD, 2))
            row += 1

        def _entry(initial, show=None):
            nonlocal row
            e = ctk.CTkEntry(self, width=480, fg_color=T.PANEL,
                             text_color=T.TEXT, show=show or "")
            e.insert(0, str(initial))
            e.grid(row=row, column=0, sticky="ew", padx=T.PAD)
            row += 1
            return e

        _label("Output folder (Clone Hero Songs)")
        self.e_out = _entry(settings.output_folder)
        _label("Charter name")
        self.e_charter = _entry(settings.charter)
        _label("Spotify Client ID (optional, for Spotify URLs)")
        self.e_sid = _entry(settings.spotify_client_id)
        _label("Spotify Client Secret")
        self.e_ssec = _entry(settings.spotify_client_secret, show="*")
        _label("Audio quality (OGG q, 0-10)")
        self.e_q = _entry(settings.audio_quality)
        _label("Manual offset (ms, -500 to 500)")
        self.e_offset = _entry(settings.manual_offset_ms)
        _label("Browser cookies (firefox / none — chrome blocked by app-bound encryption)")
        self.e_browser = _entry(settings.browser_cookies)

        _label("cookies.txt path (optional — wins over browser cookies; export via a cookies.txt extension)")
        row_frame = ctk.CTkFrame(self, fg_color=T.BG)
        row_frame.grid(row=row, column=0, sticky="ew", padx=T.PAD)
        row_frame.grid_columnconfigure(0, weight=1)
        self.e_cookies_txt = ctk.CTkEntry(row_frame, fg_color=T.PANEL, text_color=T.TEXT)
        self.e_cookies_txt.insert(0, str(settings.cookies_txt_path))
        self.e_cookies_txt.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            row_frame, text="Browse...", width=90, fg_color=T.PANEL,
            hover_color=T.PANEL_HI, text_color=T.TEXT,
            command=self._browse_cookies_txt,
        ).grid(row=0, column=1, padx=(T.PAD // 2, 0))
        row += 1

        self.v_drums = ctk.BooleanVar(value=settings.include_drums)
        self.v_video = ctk.BooleanVar(value=settings.download_video)
        self.v_open = ctk.BooleanVar(value=settings.open_folder_after_chart)
        ctk.CTkCheckBox(self, text="Transcribe drums track",
                        variable=self.v_drums, text_color=T.TEXT,
                        fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER
                       ).grid(row=row, column=0, sticky="w", padx=T.PAD, pady=(T.PAD, 2))
        row += 1
        ctk.CTkCheckBox(self, text="Download background video (increases size by 50-100MB per song)",
                        variable=self.v_video, text_color=T.TEXT,
                        fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER
                       ).grid(row=row, column=0, sticky="w", padx=T.PAD, pady=(2, 2))
        row += 1
        ctk.CTkCheckBox(self, text="Open folder after chart",
                        variable=self.v_open, text_color=T.TEXT,
                        fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER
                       ).grid(row=row, column=0, sticky="w", padx=T.PAD)
        row += 1

        ctk.CTkButton(
            self, text="Save", height=40, fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER, text_color="#000",
            command=self._save,
        ).grid(row=row, column=0, sticky="ew", padx=T.PAD, pady=T.PAD * 2)

    def _save(self) -> None:
        try:
            self.settings.output_folder = self.e_out.get().strip() or self.settings.output_folder
            self.settings.charter = self.e_charter.get().strip() or "autochart"
            self.settings.spotify_client_id = self.e_sid.get().strip()
            self.settings.spotify_client_secret = self.e_ssec.get().strip()
            self.settings.audio_quality = int(self.e_q.get() or 5)
            self.settings.manual_offset_ms = int(self.e_offset.get() or 0)
            self.settings.browser_cookies = self.e_browser.get().strip().lower() or "firefox"
            self.settings.cookies_txt_path = self.e_cookies_txt.get().strip()
            self.settings.include_drums = bool(self.v_drums.get())
            self.settings.download_video = bool(self.v_video.get())
            self.settings.open_folder_after_chart = bool(self.v_open.get())
            self.settings.save()
            self.on_save(self.settings)
            self.destroy()
        except ValueError:
            pass

    def _browse_cookies_txt(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select cookies.txt",
            filetypes=[("Netscape cookies", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.e_cookies_txt.delete(0, "end")
            self.e_cookies_txt.insert(0, path)
