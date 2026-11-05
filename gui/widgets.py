import customtkinter as ctk

from gui import theme as T


class ProgressRow(ctk.CTkFrame):
    def __init__(self, master, title: str):
        super().__init__(master, fg_color=T.PANEL, corner_radius=10)
        self.grid_columnconfigure(1, weight=1)
        self.title_lbl = ctk.CTkLabel(self, text=title, text_color=T.TEXT,
                                      font=T.FONT_H2, anchor="w")
        self.title_lbl.grid(row=0, column=0, columnspan=2, sticky="ew", padx=T.PAD, pady=(T.PAD, 4))
        self.bar = ctk.CTkProgressBar(self, progress_color=T.ACCENT, height=10)
        self.bar.set(0.0)
        self.bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=T.PAD)
        self.msg = ctk.CTkLabel(self, text="queued", text_color=T.MUTED, font=T.FONT_BODY, anchor="w")
        self.msg.grid(row=2, column=0, columnspan=2, sticky="ew", padx=T.PAD, pady=(4, T.PAD))

    def update_progress(self, pct: float, message: str, color: str = T.ACCENT) -> None:
        self.bar.set(max(0.0, min(1.0, pct)))
        self.bar.configure(progress_color=color)
        self.msg.configure(text=message, text_color=color if color != T.ACCENT else T.MUTED)


class ResultCard(ctk.CTkFrame):
    def __init__(self, master, idx: int, title: str, subtitle: str, duration_s: int,
                 on_toggle):
        super().__init__(master, fg_color=T.PANEL, corner_radius=8)
        self.idx = idx
        self.var = ctk.BooleanVar(value=False)
        self.on_toggle = on_toggle
        self.grid_columnconfigure(1, weight=1)
        self.check = ctk.CTkCheckBox(self, text="", variable=self.var,
                                     command=self._fire, fg_color=T.ACCENT,
                                     hover_color=T.ACCENT_HOVER)
        self.check.grid(row=0, column=0, rowspan=2, padx=(T.PAD, 8), pady=T.PAD)
        self.title_lbl = ctk.CTkLabel(self, text=title, text_color=T.TEXT,
                                      font=T.FONT_H2, anchor="w")
        self.title_lbl.grid(row=0, column=1, sticky="ew")
        mins, secs = divmod(max(0, duration_s), 60)
        self.sub_lbl = ctk.CTkLabel(
            self, text=f"{subtitle}  ·  {mins}:{secs:02d}",
            text_color=T.MUTED, font=T.FONT_BODY, anchor="w",
        )
        self.sub_lbl.grid(row=1, column=1, sticky="ew")

    def _fire(self) -> None:
        self.on_toggle(self.idx, self.var.get())


class LogView(ctk.CTkTextbox):
    def __init__(self, master):
        super().__init__(master, fg_color=T.PANEL, text_color=T.TEXT,
                         font=T.FONT_MONO, border_width=0, wrap="none")
        self.configure(state="disabled")

    def append(self, line: str) -> None:
        self.configure(state="normal")
        self.insert("end", line + "\n")
        self.see("end")
        self.configure(state="disabled")
