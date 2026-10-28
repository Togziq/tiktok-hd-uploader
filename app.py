"""TikTok HD Uploader: prepare videos so TikTok serves them in original quality."""

import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from itertools import count
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image

import core

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DnDMixin = TkinterDnD.DnDWrapper  # the Tk root isn't a BaseWidget, so it needs the mixin
except ImportError:
    TkinterDnD = None
    DnDMixin = object

__version__ = "1.1.0"

STUDIO_URL = "https://www.tiktok.com/tiktokstudio/upload"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/"
ICON = os.path.join("assets", "icon.ico")

BG = "#0d0d10"
CARD = "#17171c"
CARD_HOVER = "#1c1c23"
BORDER = "#2a2a33"
TEXT = "#f2f2f5"
MUTED = "#8b8b98"
PINK = "#fe2c55"
PINK_HOVER = "#e0244a"
CYAN = "#25f4ee"
GREEN = "#3ddc84"
AMBER = "#ffb84d"
RED = "#ff5c6c"
SECONDARY = "#24242c"
SECONDARY_HOVER = "#30303a"

BADGES = {
    "patch": ("Оригинал", "#10331f", GREEN),
    "remux": ("Перепаковка", "#0e2f33", CYAN),
    "encode": ("Перекодировка", "#3a1220", PINK),
}


def resource(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def shorten(text: str, limit: int = 52) -> str:
    if len(text) <= limit:
        return text
    keep = (limit - 1) // 2
    return text[:keep] + "…" + text[-keep:]


def font(size: int, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal")


def reveal(path: str):
    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])


class VideoRow(ctk.CTkFrame):
    def __init__(self, master, src: str, on_error):
        super().__init__(master, fg_color=CARD, corner_radius=14)
        self.src = src
        self.out: str | None = None
        self.details = ""
        self.state = "queued"
        self._on_error = on_error

        self.grid_columnconfigure(1, weight=1)
        self.dot = ctk.CTkLabel(self, text="●", font=font(18), text_color=MUTED, width=18)
        self.dot.grid(row=0, column=0, rowspan=2, padx=(18, 10))

        self.name = ctk.CTkLabel(self, text=shorten(Path(src).name), font=font(14, True),
                                 text_color=TEXT, anchor="w")
        self.name.grid(row=0, column=1, sticky="sw", pady=(14, 0))
        self.meta = ctk.CTkLabel(self, text="В очереди", font=font(12), text_color=MUTED,
                                 anchor="w", justify="left")
        self.meta.grid(row=1, column=1, sticky="nw", pady=(0, 14))

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=2, rowspan=2, padx=(8, 14))
        self.badge = ctk.CTkLabel(right, text="", font=font(11, True), corner_radius=8,
                                  fg_color="transparent", height=24)
        self.badge.pack(side="left", padx=(0, 10))
        self.btn_play = self._small_button(right, "▶  Смотреть", self._preview)
        self.btn_folder = self._small_button(right, "Папка", self._reveal)
        self.btn_upload = ctk.CTkButton(
            right, text="Загрузить ↗", width=112, height=34, corner_radius=10,
            font=font(12, True), fg_color=PINK, hover_color=PINK_HOVER,
            text_color="#ffffff", text_color_disabled="#7a5560", command=self._upload)
        self.btn_upload.pack(side="left", padx=(6, 0))
        self.btn_upload.configure(state="disabled", fg_color="#3a1a22")

        self.progress = ctk.CTkProgressBar(self, mode="indeterminate", height=3,
                                           progress_color=CYAN, fg_color=SECONDARY,
                                           corner_radius=2)

    def _small_button(self, master, text, command):
        btn = ctk.CTkButton(master, text=text, width=10, height=34, corner_radius=10,
                            font=font(12), fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
                            text_color=TEXT, command=command)
        btn.pack(side="left", padx=(6, 0))
        return btn

    # ----- state changes -----

    def set_working(self, info, mode):
        self.state = "working"
        self.details = core.describe(info)
        self.dot.configure(text_color=CYAN)
        self._set_badge(mode)
        busy = "Перекодирую…" if mode == "encode" else "Обрабатываю…"
        self.meta.configure(text=f"{self.details}\n{busy}" if self.details else busy)
        self.progress.grid(row=2, column=0, columnspan=3, sticky="ew", padx=18, pady=(0, 12))
        self.progress.start()

    def set_done(self, out, mode):
        self.state = "done"
        self.out = out
        self._stop_progress()
        self.dot.configure(text_color=GREEN)
        self._set_badge(mode)
        ready = f"Готово → {shorten(Path(out).name, 40)}"
        self.meta.configure(text=f"{self.details}\n{ready}" if self.details else ready,
                            text_color=MUTED)
        self.btn_upload.configure(state="normal", fg_color=PINK)

    def set_error(self, message):
        self.state = "error"
        self._stop_progress()
        self.dot.configure(text_color=RED)
        lines = [line.strip() for line in message.splitlines() if line.strip()]
        summary = " ".join(lines) or "неизвестная ошибка"
        self.meta.configure(text=f"Ошибка: {shorten(summary, 80)}", text_color=RED)

    def _set_badge(self, mode):
        text, bg, fg = BADGES[mode]
        self.badge.configure(text=f"  {text}  ", fg_color=bg, text_color=fg)

    def _stop_progress(self):
        self.progress.stop()
        self.progress.grid_forget()

    # ----- actions -----

    def _preview(self):
        try:
            core.preview(self.out or self.src)
        except Exception as exc:
            self._on_error(str(exc))

    def _reveal(self):
        reveal(self.out or self.src)

    def _upload(self):
        webbrowser.open(STUDIO_URL)
        if self.out:
            reveal(self.out)


class App(ctk.CTk, DnDMixin):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.dnd = False
        if TkinterDnD:
            try:
                TkinterDnD._require(self)
                self.dnd = True
            except Exception:
                pass

        self.events: queue.Queue = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()
        self.rows: dict[int, VideoRow] = {}
        self._ids = count()
        self.force_encode = ctk.BooleanVar(value=False)
        self.keep_4k = ctk.BooleanVar(value=False)

        self.title("TikTok HD Uploader")
        self.geometry("900x720")
        self.minsize(780, 560)
        try:
            self.iconbitmap(resource(ICON))
        except Exception:
            pass

        self._build_header()
        self._build_dropzone()
        self._build_list()
        self._build_footer()

        threading.Thread(target=self._worker, daemon=True).start()
        self.after(100, self._poll)

    # ---------- layout ----------

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(22, 14))
        try:
            logo = ctk.CTkImage(Image.open(resource(ICON)), size=(44, 44))
            ctk.CTkLabel(header, image=logo, text="").pack(side="left", padx=(0, 14))
        except Exception:
            pass
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="TikTok HD Uploader", font=font(22, True),
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(titles, text="Видео в оригинальном качестве — без пережатия TikTok",
                     font=font(12), text_color=MUTED).pack(anchor="w")
        ctk.CTkButton(header, text="Открыть TikTok Studio  ↗", height=40, corner_radius=12,
                      font=font(13, True), fg_color=PINK, hover_color=PINK_HOVER,
                      command=lambda: webbrowser.open(STUDIO_URL)).pack(side="right")

    def _build_dropzone(self):
        self.zone = ctk.CTkFrame(self, fg_color=CARD, corner_radius=18, border_width=2,
                                 border_color=BORDER)
        self.zone.pack(fill="x", padx=24)
        inner = ctk.CTkFrame(self.zone, fg_color="transparent")
        inner.pack(pady=22)

        self.zone_inner = inner
        self.zone_icon = icon = ctk.CTkLabel(
            inner, text="⤓", font=font(28, True), text_color=CYAN,
            fg_color=SECONDARY, corner_radius=28, width=56, height=56)
        icon.pack(pady=(0, 10))
        headline = "Перетащи видео сюда" if self.dnd else "Добавь видео"
        self.zone_title = title = ctk.CTkLabel(inner, text=headline, font=font(17, True),
                                               text_color=TEXT)
        title.pack()
        self.zone_hint = hint = ctk.CTkLabel(
            inner, text="MP4 · MOV · MKV · WEBM — готовый файл появится рядом с исходником",
            font=font(12), text_color=MUTED)
        hint.pack(pady=(2, 10))
        self.zone_compact = False
        ctk.CTkButton(inner, text="Выбрать файлы", width=150, height=34, corner_radius=10,
                      font=font(12, True), fg_color="transparent", border_width=1,
                      border_color=CYAN, text_color=CYAN, hover_color="#12292b",
                      command=self._pick_files).pack()

        for widget in (self.zone, inner, icon, title, hint):
            widget.bind("<Button-1>", lambda _e: self._pick_files())
            widget.configure(cursor="hand2")

        if self.dnd:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<DropEnter>>", lambda e: self._highlight(True) or e.action)
            self.dnd_bind("<<DropLeave>>", lambda e: self._highlight(False) or e.action)
            self.dnd_bind("<<Drop>>", self._on_drop)

    def _build_list(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=(20, 6))
        self.count_label = ctk.CTkLabel(bar, text="Видео", font=font(15, True), text_color=TEXT)
        self.count_label.pack(side="left")
        ctk.CTkButton(bar, text="Очистить готовые", width=10, height=28, corner_radius=8,
                      font=font(12), fg_color="transparent", hover_color=SECONDARY,
                      text_color=MUTED, command=self._clear).pack(side="right")
        ctk.CTkSwitch(bar, text="Всегда перекодировать", variable=self.force_encode,
                      font=font(12), text_color=MUTED, progress_color=PINK,
                      button_color=TEXT, button_hover_color="#ffffff",
                      fg_color=SECONDARY, switch_width=36, switch_height=18
                      ).pack(side="right", padx=(0, 14))
        ctk.CTkSwitch(bar, text="Сохранять 4K (эксперимент)", variable=self.keep_4k,
                      font=font(12), text_color=MUTED, progress_color=CYAN,
                      button_color=TEXT, button_hover_color="#ffffff",
                      fg_color=SECONDARY, switch_width=36, switch_height=18
                      ).pack(side="right", padx=(0, 14))

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0,
                                           scrollbar_button_color=SECONDARY,
                                           scrollbar_button_hover_color=SECONDARY_HOVER)
        self.list.pack(fill="both", expand=True, padx=(18, 12))
        self.empty = ctk.CTkLabel(self.list, text="Пока пусто — добавь ролик, и он появится здесь",
                                  font=font(13), text_color="#55555f")
        self.empty.pack(pady=40)

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=24, pady=(6, 14))
        self.ffmpeg_label = ctk.CTkLabel(footer, text="", font=font(11))
        self.ffmpeg_label.pack(side="left")
        self.ffmpeg_button = ctk.CTkButton(footer, text="Установить ffmpeg", width=10, height=26,
                                           corner_radius=8, font=font(11, True),
                                           fg_color=SECONDARY, hover_color=SECONDARY_HOVER,
                                           text_color=AMBER, command=self._install_ffmpeg)
        self.log_button = ctk.CTkButton(footer, text="Журнал ▾", width=10, height=26,
                                        corner_radius=8, font=font(11), fg_color="transparent",
                                        hover_color=SECONDARY, text_color=MUTED,
                                        command=self._toggle_log)
        self.log_button.pack(side="right")
        ctk.CTkLabel(footer, text=f"v{__version__}", font=font(11),
                     text_color="#55555f").pack(side="right", padx=(0, 8))
        self._refresh_ffmpeg()
        self.log_box = ctk.CTkTextbox(self, height=120, fg_color=CARD, text_color=MUTED,
                                      font=ctk.CTkFont(family="Consolas", size=11),
                                      corner_radius=12, border_width=0)
        self.log_box.configure(state="disabled")

    # ---------- ffmpeg ----------

    def _refresh_ffmpeg(self) -> bool:
        found = core.find_tool("ffmpeg") is not None
        if found:
            self.ffmpeg_label.configure(text="●  ffmpeg подключён", text_color=GREEN)
            self.ffmpeg_button.pack_forget()
        else:
            self.ffmpeg_label.configure(
                text="●  ffmpeg не найден — сработают только MP4 из CapCut", text_color=AMBER)
            self.ffmpeg_button.pack(side="left", padx=(10, 0))
        return found

    def _install_ffmpeg(self):
        if not shutil.which("winget"):
            webbrowser.open(FFMPEG_URL)
            self._show_error("winget не найден. Скачай ffmpeg с открывшейся страницы и положи "
                             "ffmpeg.exe, ffprobe.exe и ffplay.exe рядом с программой.")
            return
        subprocess.Popen(
            ["cmd", "/c", "winget install --id Gyan.FFmpeg -e --accept-source-agreements "
                          "--accept-package-agreements & echo. & pause"],
            creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.ffmpeg_button.configure(state="disabled", text="Устанавливаю…")
        self._wait_for_ffmpeg(tries=200)

    def _wait_for_ffmpeg(self, tries: int):
        if self._refresh_ffmpeg():
            self._log("ffmpeg установлен")
            return
        if tries > 0:
            self.after(3000, self._wait_for_ffmpeg, tries - 1)
        else:
            self.ffmpeg_button.configure(state="normal", text="Установить ffmpeg")

    # ---------- helpers ----------

    def _highlight(self, on: bool):
        self.zone.configure(border_color=PINK if on else BORDER,
                            fg_color=CARD_HOVER if on else CARD)

    def _toggle_log(self):
        if self.log_box.winfo_ismapped():
            self.log_box.pack_forget()
            self.log_button.configure(text="Журнал ▾")
        else:
            self.log_box.pack(fill="x", padx=24, pady=(0, 16))
            self.log_button.configure(text="Журнал ▴")

    def _log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _show_error(self, text: str):
        self._log(f"ошибка: {text}")
        if not self.log_box.winfo_ismapped():
            self._toggle_log()

    def _set_zone_compact(self, compact: bool):
        """Shrink the drop zone once there are videos, so more cards fit."""
        if compact == self.zone_compact:
            return
        self.zone_compact = compact
        if compact:
            self.zone_icon.pack_forget()
            self.zone_hint.pack_forget()
            self.zone_title.pack_configure(pady=(0, 8))
            self.zone_inner.pack_configure(pady=14)
        else:
            self.zone_icon.pack(before=self.zone_title, pady=(0, 10))
            self.zone_hint.pack(after=self.zone_title, pady=(2, 10))
            self.zone_title.pack_configure(pady=0)
            self.zone_inner.pack_configure(pady=22)

    def _update_count(self):
        self._set_zone_compact(bool(self.rows))
        if self.rows:
            self.empty.pack_forget()
            done = sum(r.state == "done" for r in self.rows.values())
            self.count_label.configure(text=f"Видео  ·  {done}/{len(self.rows)} готово")
        else:
            self.empty.pack(pady=40)
            self.count_label.configure(text="Видео")

    # ---------- adding files ----------

    def _pick_files(self):
        exts = " ".join(f"*{e}" for e in sorted(core.VIDEO_EXTS))
        paths = filedialog.askopenfilenames(
            title="Выбери видео", filetypes=[("Видео", exts), ("Все файлы", "*.*")])
        self.add_files(paths)

    def _on_drop(self, event):
        self._highlight(False)
        self.add_files(self.tk.splitlist(event.data))
        return event.action

    def add_files(self, paths):
        for path in paths:
            p = Path(path)
            if p.is_dir() or p.suffix.lower() not in core.VIDEO_EXTS:
                self._log(f"пропускаю {p.name}: не видео")
                continue
            if p.stem.endswith("_tiktok"):
                self._log(f"пропускаю {p.name}: он уже обработан")
                continue
            rid = next(self._ids)
            row = VideoRow(self.list, str(p), on_error=self._show_error)
            row.pack(fill="x", pady=5, padx=(0, 6))
            self.rows[rid] = row
            self.jobs.put((rid, str(p), self.force_encode.get(), self.keep_4k.get()))
        self._update_count()

    # ---------- background work ----------

    def _worker(self):
        while True:
            rid, src, force, keep_4k = self.jobs.get()
            name = Path(src).name
            log = lambda msg, n=name: self.events.put(("log", f"[{n}] {msg}"))
            plan = lambda info, mode, _reason, r=rid: self.events.put(("plan", r, info, mode))
            try:
                out, mode = core.process(src, force_encode=force, log=log, on_plan=plan,
                                         keep_4k=keep_4k)
                self.events.put(("done", rid, out, mode))
            except Exception as exc:
                self.events.put(("log", f"[{name}] ошибка: {exc}"))
                self.events.put(("error", rid, str(exc)))

    def _poll(self):
        while not self.events.empty():
            kind, *args = self.events.get()
            if kind == "log":
                self._log(args[0])
                continue
            row = self.rows.get(args[0])
            if row is None:
                continue
            if kind == "plan":
                row.set_working(args[1], args[2])
            elif kind == "done":
                row.set_done(args[1], args[2])
            elif kind == "error":
                row.set_error(args[1])
            self._update_count()
        self.after(100, self._poll)

    def _clear(self):
        for rid, row in list(self.rows.items()):
            if row.state in ("done", "error"):
                row.destroy()
                del self.rows[rid]
        self._update_count()


def main():
    ctk.set_appearance_mode("dark")
    app = App()
    app.add_files(sys.argv[1:])  # files dropped onto the .exe icon
    app.mainloop()


if __name__ == "__main__":
    main()
