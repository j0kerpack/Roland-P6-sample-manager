import time as _time
_t_start = _time.perf_counter()

# Startup profiling counters - populated by the rounded-widget drawing code 
# so we can see exactly where the layout pass spends its time.
_PERF = {
    "panel_redraws": 0, "panel_redraw_time": 0.0,
    "button_draws": 0, "button_draw_time": 0.0,
    "dropdown_draws": 0, "dropdown_draw_time": 0.0,
}


def _log_timing(label):
    print(f"[startup] {label}: {_time.perf_counter() - _t_start:6.3f}s elapsed")


def _log_perf_counters():
    print("[startup] --- drawing breakdown ---")
    print(f"[startup]   RoundedPanel._redraw : {_PERF['panel_redraws']:5d} calls, "
          f"{_PERF['panel_redraw_time']:6.3f}s total")
    print(f"[startup]   RoundedButton._draw  : {_PERF['button_draws']:5d} calls, "
          f"{_PERF['button_draw_time']:6.3f}s total")
    print(f"[startup]   RoundedDropdown._draw: {_PERF['dropdown_draws']:5d} calls, "
          f"{_PERF['dropdown_draw_time']:6.3f}s total")


import os
import sys
import shutil
import wave
import contextlib
import json
import subprocess
import threading
import queue
_log_timing("stdlib imports (batch 1)")

import tkinter as tk
from tkinter import ttk
_log_timing("tkinter imported")

import sounddevice as sd
_log_timing("sounddevice imported (incl. audio device enumeration)")

import soundfile as sf
_log_timing("soundfile imported")

import numpy as np
_log_timing("numpy imported")

import time
import uuid

def resource_path(relative_path):
    """Resolves a path next to the script - and also works after bundling
    with PyInstaller (onefile or onedir), which extracts bundled data files
    into a temp folder at runtime and exposes it via sys._MEIPASS."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


try:
    from pydub import AudioSegment
    from pydub.silence import detect_leading_silence
    from pydub.effects import normalize as pydub_normalize

    # Priority: a copy bundled alongside the app (e.g. via PyInstaller
    # --add-binary, so the app is self-contained and needs no system-wide
    # ffmpeg install) > whatever is on PATH > the Linux convenience fallback.
    _bundle_ffmpeg_name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    _bundle_ffprobe_name = "ffprobe.exe" if sys.platform.startswith("win") else "ffprobe"
    _bundled_ffmpeg = resource_path(_bundle_ffmpeg_name)
    _bundled_ffprobe = resource_path(_bundle_ffprobe_name)

    if os.path.exists(_bundled_ffmpeg):
        _ffmpeg_path = _bundled_ffmpeg
    else:
        _ffmpeg_path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    if os.path.exists(_bundled_ffprobe):
        _ffprobe_path = _bundled_ffprobe
    else:
        _ffprobe_path = shutil.which("ffprobe") or "/usr/bin/ffprobe"

    if os.path.exists(_ffmpeg_path):
        AudioSegment.converter = _ffmpeg_path
        AudioSegment.ffmpeg = _ffmpeg_path
    if os.path.exists(_ffprobe_path):
        AudioSegment.ffprobe = _ffprobe_path
    PYDUB_AVAILABLE = True
    # Reflects whether ffmpeg is *actually* reachable (bundled, on PATH, or
    # at the fallback path), not just whether some candidate was guessed.
    FFMPEG_AVAILABLE = os.path.exists(_ffmpeg_path)
except ImportError:
    PYDUB_AVAILABLE = False
    FFMPEG_AVAILABLE = False

_log_timing("pydub import block done")

_pydub_warning_shown = False  # only nag once per session if pydub is missing


def warn_pydub_missing_once():
    """Shows a one-time notice if the user changes a setting (rate, pitch,
    mono) that requires pydub/ffmpeg to actually take effect at export time."""
    global _pydub_warning_shown
    if PYDUB_AVAILABLE or _pydub_warning_shown:
        return
    _pydub_warning_shown = True
    dark_showwarning(
        "pydub/ffmpeg missing",
        "Sample rate, pitch and forced mono require pydub + ffmpeg.\n"
        "These settings are saved, but currently have no effect on "
        "Play/Preview/Export while pydub/ffmpeg is missing."
    )

CONFIG_FILE = os.path.expanduser("~/.p6tool_config.json")
LAST_SAMPLE_DIR = None


def guess_default_import_root():
    """Try to auto-detect a mounted Roland P-6 IMPORT folder across platforms.
    Falls back to the user's home directory if nothing is found, rather than
    a hardcoded, user- or OS-specific path that would break on other machines."""
    import sys
    import glob

    candidates = []
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            candidates.append(f"{letter}:\\IMPORT")
            candidates.append(f"{letter}:\\P-6\\IMPORT")
    elif sys.platform == "darwin":
        candidates += glob.glob("/Volumes/*/IMPORT")
        candidates += glob.glob("/Volumes/*/P-6/IMPORT")
    else:
        # Linux: typical auto-mount locations, independent of username
        candidates += glob.glob("/run/media/*/*/IMPORT")
        candidates += glob.glob("/run/media/*/P-6/IMPORT")
        candidates += glob.glob("/media/*/*/IMPORT")
        candidates += glob.glob("/media/*/P-6/IMPORT")

    for path in candidates:
        if os.path.isdir(path):
            return path

    return os.path.expanduser("~")

BANKS = [chr(c) for c in range(ord("A"), ord("H") + 1)]
PADS = list(range(1, 7))
TARGET_RATES = [44100, 22050, 14700, 11025]
SLICE_COUNTS = [1, 2, 4, 8, 16, 24, 32, 48, 64]
PITCH_MIN_CENTS = -1200
PITCH_MAX_CENTS = 1200
PITCH_STEP_CENTS = 100

MAX_SECONDS = {
    (44100, 1): 5.9, (44100, 2): 2.95,
    (22050, 1): 11.8, (22050, 2): 5.9,
    (14700, 1): 17.8, (14700, 2): 8.9,
    (11025, 1): 23.7, (11025, 2): 11.85,
}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB soft limit per upload

_CONFIG_FILE_PATH = CONFIG_FILE


def _load_theme_preference():
    """Reads just the theme preference directly (load_config() isn't defined
    yet at this point in the module - colors must be resolved before any
    widget classes below are defined, since several use them as default
    parameter values)."""
    try:
        with open(_CONFIG_FILE_PATH, "r") as f:
            return json.load(f).get("theme", "dark")
    except Exception:
        return "dark"


THEME = _load_theme_preference()

# "Segoe UI" only exists on Windows. Asking for it on Linux/macOS makes the
# font system search its whole database for a family that isn't there and
# then resolve a substitute - and that happens during the layout pass, for
# every distinct size/weight combination. On X11 this is slow enough to add
# many seconds to startup, so pick a family that actually exists instead.
if sys.platform.startswith("win"):
    UI_FAMILY = "Segoe UI"
elif sys.platform == "darwin":
    UI_FAMILY = "Helvetica Neue"
else:
    UI_FAMILY = "DejaVu Sans"  # present on virtually every Linux distribution


if THEME == "bright":
    BG_DARK = "#DEDEE4"
    BG_PANEL = "#FFFFFF"
    BG_INPUT = "#ECECF1"
    FG_TEXT = "#202024"
    FG_MUTED = "#6B6B75"
    ACCENT_BLUE = "#3D7A94"
    ACCENT_GREEN = "#4C8250"
    ACCENT_RED = "#A34D4A"
    ACCENT_ORANGE = "#B87830"
    ACCENT_PURPLE = "#764A82"
    SELECT_GREEN = "#BEE3BE"
    BORDER_COLOR = "#C3C3CB"
    BORDER_LIGHT = "#9C9CA8"
    WAVE_BG = "#D3D3DB"  # a clear, but still light, gray - distinct from the white panels
    HOVER_BG = "#DCDCE3"  # slightly darker than BG_INPUT, for hover feedback
else:
    BG_DARK = "#1E1E24"
    BG_PANEL = "#2A2A33"
    BG_INPUT = "#33333E"
    FG_TEXT = "#E8E8ED"
    FG_MUTED = "#9A9AA5"
    ACCENT_BLUE = "#4A8FA8"
    ACCENT_GREEN = "#5C9E60"
    ACCENT_RED = "#C05C59"
    ACCENT_ORANGE = "#CC8A3D"
    ACCENT_PURPLE = "#8C5A99"
    SELECT_GREEN = "#3E7A3E"
    BORDER_COLOR = "#3D3D48"
    BORDER_LIGHT = "#5C5C6A"
    WAVE_BG = BG_INPUT
    HOVER_BG = "#3B3B47"  # slightly lighter than BG_INPUT, for hover feedback

WAVE_COLOR = "#1D7A9C"  # darker, theme-independent blue for the waveform trace itself

MAIN_MIN_W = 1000
MAIN_MIN_H = 835 if sys.platform.startswith("win") else 815
CHOP_MIN_W, CHOP_MIN_H = 1000, 815
PREVIEW_MIN_W, PREVIEW_MIN_H = 560, 480
AUDIO_PREVIEW_MIN_W, AUDIO_PREVIEW_MIN_H = 660, 580


def style_button(btn, bg, fg=FG_TEXT):
    btn.config(bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
               relief="flat", bd=0, padx=10, pady=5,
               font=(UI_FAMILY, 9, "bold"), cursor="hand2",
               highlightthickness=0)


def style_toplevel(win):
    win.configure(bg=BG_DARK)


def style_label(lbl, bg=BG_DARK, fg=FG_TEXT, **kw):
    lbl.config(bg=bg, fg=fg, **kw)


def style_frame(frm, bg=BG_DARK):
    frm.config(bg=bg)


def style_listbox(lb):
    lb.config(bg=BG_INPUT, fg=FG_TEXT, selectbackground=ACCENT_BLUE,
              selectforeground="#00131A", relief="flat", bd=0,
              highlightthickness=1, highlightbackground=BORDER_COLOR,
              highlightcolor=ACCENT_BLUE, font=(UI_FAMILY, 10))


_TREEVIEW_STYLE_READY = False


def ensure_dark_treeview_style():
    """Configures a dark ttk.Treeview style once per process. Using a real
    Treeview (native columns/headings) instead of faking columns with
    space-padded monospace text avoids the alignment drift that approach
    was prone to (font-metric/widget-padding differences between the
    header label and the listbox)."""
    global _TREEVIEW_STYLE_READY
    if _TREEVIEW_STYLE_READY:
        return
    style = ttk.Style()
    try:
        style.theme_use("clam")  # most reliably themeable base on all platforms
    except tk.TclError:
        pass
    style.configure("Dark.Treeview",
                     background=BG_INPUT, fieldbackground=BG_INPUT, foreground=FG_TEXT,
                     borderwidth=0, relief="flat", rowheight=22, font=(UI_FAMILY, 10))
    style.map("Dark.Treeview",
              background=[("selected", ACCENT_BLUE)],
              foreground=[("selected", "#00131A")])
    style.configure("Dark.Treeview.Heading",
                     background=BG_PANEL, foreground=FG_MUTED, relief="flat",
                     font=(UI_FAMILY, 9, "bold"))
    style.map("Dark.Treeview.Heading", background=[("active", BG_PANEL)])
    style.layout("Dark.Treeview", style.layout("Treeview"))
    _TREEVIEW_STYLE_READY = True


def style_checkbutton(cb):
    cb.config(bg=BG_DARK, fg=FG_TEXT, selectcolor=BG_INPUT,
              activebackground=BG_DARK, activeforeground=FG_TEXT,
              disabledforeground=FG_MUTED,
              relief="flat", bd=0, highlightthickness=0,
              font=(UI_FAMILY, 9))


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text="", command=None, bg=ACCENT_BLUE, fg="#FFFFFF",
                 parent_bg=None, width=110, height=32, radius=10,
                 font=(UI_FAMILY, 9, "bold"), state="normal"):
        parent_bg = parent_bg or parent.cget("bg")
        super().__init__(parent, width=width, height=height, bg=parent_bg,
                          highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.bg_color = bg
        self.fg_color = fg
        self.text = text
        self.font = font
        self.radius = min(radius, height // 2)
        self.width = width
        self.height = height
        self._state = state
        self._draw()
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        self.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90, style="pieslice", **kw)
        self.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90, style="pieslice", **kw)
        self.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90, style="pieslice", **kw)
        self.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90, style="pieslice", **kw)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, **kw)
        self.create_rectangle(x1, y1 + r, x2, y2 - r, **kw)

    def _draw(self, hover=False):
        _t0 = time.time()
        self.delete("all")
        fill = self._lighten(self.bg_color) if hover and self._state == "normal" else self.bg_color
        if self._state == "disabled":
            fill = BORDER_COLOR
        self._round_rect(1, 1, self.width - 1, self.height - 1, self.radius, fill=fill, outline=fill)
        text_fg = self.fg_color if self._state == "normal" else FG_MUTED
        self.create_text(self.width / 2, self.height / 2, text=self.text,
                          fill=text_fg, font=self.font)
        _PERF["button_draws"] += 1
        _PERF["button_draw_time"] += time.time() - _t0

    def _lighten(self, hex_color, amount=18):
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        r, g, b = (min(255, c + amount) for c in (r, g, b))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _on_enter(self, event):
        if self._state == "normal":
            self._draw(hover=True)
            self.config(cursor="hand2")

    def _on_leave(self, event):
        self._draw(hover=False)

    def _on_click(self, event):
        if self._state == "normal" and self.command:
            self.command()

    def config_state(self, state):
        self._state = state
        self._draw()


class RoundedDropdown(tk.Canvas):
    def __init__(self, parent, variable, values, command=None, parent_bg=None,
                 width=110, height=30, radius=10, font=(UI_FAMILY, 9, "bold")):
        parent_bg = parent_bg or parent.cget("bg")
        super().__init__(parent, width=width, height=height, bg=parent_bg,
                          highlightthickness=0, bd=0, cursor="hand2")
        self.variable = variable
        self.values = values
        self.command = command
        self.width = width
        self.height = height
        self.radius = min(radius, height // 2)
        self.font = font
        self._draw()
        self.bind("<Button-1>", self._open_menu)
        self.bind("<Enter>", lambda e: self._draw(hover=True))
        self.bind("<Leave>", lambda e: self._draw(hover=False))
        self.variable.trace_add("write", lambda *a: self._draw())

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        self.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90, style="pieslice", **kw)
        self.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90, style="pieslice", **kw)
        self.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90, style="pieslice", **kw)
        self.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90, style="pieslice", **kw)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, **kw)
        self.create_rectangle(x1, y1 + r, x2, y2 - r, **kw)

    def _draw(self, hover=False):
        _t0 = time.time()
        self.delete("all")
        fill = HOVER_BG if hover else BG_INPUT
        self._round_rect(1, 1, self.width - 1, self.height - 1, self.radius, fill=fill, outline=fill)
        self.create_text(14, self.height / 2, text=str(self.variable.get()),
                          fill=FG_TEXT, font=self.font, anchor="w")
        self.create_text(self.width - 14, self.height / 2, text="\u25be",
                          fill=ACCENT_BLUE, font=(UI_FAMILY, 8), anchor="e")
        _PERF["dropdown_draws"] += 1
        _PERF["dropdown_draw_time"] += time.time() - _t0

    def _open_menu(self, event):
        menu = tk.Menu(self, tearoff=0, bg=BG_INPUT, fg=FG_TEXT,
                        activebackground=ACCENT_BLUE, activeforeground="#00131A",
                        font=self.font, bd=0, relief="flat")
        for v in self.values:
            menu.add_command(label=str(v), command=lambda val=v: self._select(val))
        menu.tk_popup(event.x_root, event.y_root)

    def _select(self, val):
        self.variable.set(val)
        self._draw()
        if self.command:
            self.command(val)


class RoundedPanel(tk.Frame):
    """A Canvas-backed panel with rounded corners, a lighter border, and a
    title label, used in place of tk.LabelFrame (which cannot have rounded
    corners). Add child widgets to `.body`, not to the panel itself.

    Both the canvas (background) and body (content) occupy the same grid
    cell of `self`, so `self`'s size is naturally driven by body's packed
    content (exactly like a LabelFrame would size itself) -- no manual
    width/height math needed."""

    def __init__(self, parent, title="", parent_bg=None, panel_bg=BG_PANEL,
                 border=BORDER_LIGHT, radius=14, title_fg=ACCENT_BLUE,
                 title_font=(UI_FAMILY, 10, "bold")):
        parent_bg = parent_bg or parent.cget("bg")
        super().__init__(parent, bg=parent_bg)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._panel_bg = panel_bg
        self._border = border
        self._radius = radius
        self._title = title
        self._title_fg = title_fg
        self._title_font = title_font

        self.canvas = tk.Canvas(self, bg=parent_bg, highlightthickness=0, bd=0, width=1, height=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.body = tk.Frame(self, bg=panel_bg)
        self.body.grid(row=0, column=0, sticky="nsew", padx=14, pady=(30, 12))

        self.canvas.bind("<Configure>", lambda e: self._redraw())

    def _round_rect(self, x1, y1, x2, y2, r, fill=None, outline=None, width=1):
        # 1) Filled background: pieslices/rectangles use fill as their own
        #    outline color too, so no stray radius/seam lines are visible.
        self.canvas.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90, style="pieslice", fill=fill, outline=fill)
        self.canvas.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90, style="pieslice", fill=fill, outline=fill)
        self.canvas.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90, style="pieslice", fill=fill, outline=fill)
        self.canvas.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90, style="pieslice", fill=fill, outline=fill)
        self.canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)
        self.canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill)

        # 2) Border: drawn once on top, as pure arcs (no radius lines) + straight edges.
        if outline:
            self.canvas.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90, style="arc", outline=outline, width=width)
            self.canvas.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90, style="arc", outline=outline, width=width)
            self.canvas.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90, style="arc", outline=outline, width=width)
            self.canvas.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90, style="arc", outline=outline, width=width)
            self.canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width)
            self.canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width)
            self.canvas.create_line(x1, y1 + r, x1, y2 - r, fill=outline, width=width)
            self.canvas.create_line(x2, y1 + r, x2, y2 - r, fill=outline, width=width)

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 4 or h < 4:
            return
        # <Configure> fires repeatedly while the layout settles, often with a
        # size that hasn't actually changed. Redrawing ~14 canvas items each
        # time is pure waste (and on X11 every item is a server round-trip),
        # so bail out unless the size really differs.
        if getattr(self, "_last_size", None) == (w, h):
            return
        self._last_size = (w, h)

        _t0 = time.time()
        self.canvas.delete("all")
        r = min(self._radius, w // 2, h // 2)
        self._round_rect(1, 1, w - 1, h - 1, r, fill=self._panel_bg, outline=self._border)
        if self._title:
            self.canvas.create_text(16, 16, text=self._title, anchor="w",
                                     fill=self._title_fg, font=self._title_font)
        _PERF["panel_redraws"] += 1
        _PERF["panel_redraw_time"] += time.time() - _t0


def center_toplevel_on_parent(win, parent):
    """Centers a Toplevel over its parent (or the screen, if no parent) -
    without this, some platforms (notably Windows) default new Toplevels to
    the top-left corner of the screen instead of somewhere sensible."""
    try:
        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        if parent is not None:
            x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        else:
            x = (win.winfo_screenwidth() - w) // 2
            y = (win.winfo_screenheight() - h) // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass


class _DarkMessageDialog(tk.Toplevel):
    """Dark-themed replacement for tkinter.messagebox popups, since those
    always render in the native OS light style regardless of app theme."""

    _ICONS = {
        "info": ("\u2139", ACCENT_BLUE),
        "warning": ("\u26a0", ACCENT_ORANGE),
        "error": ("\u2715", ACCENT_RED),
        "question": ("?", ACCENT_BLUE),
    }

    def __init__(self, parent, title, message, kind="info", buttons="ok"):
        super().__init__(parent)
        self.title(title or "")
        style_toplevel(self)
        self.resizable(False, False)
        self.result = None

        icon_char, icon_color = self._ICONS.get(kind, self._ICONS["info"])

        body = tk.Frame(self, bg=BG_DARK, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        top_row = tk.Frame(body, bg=BG_DARK)
        top_row.pack(fill="both", expand=True)

        icon_label = tk.Label(top_row, text=icon_char, font=(UI_FAMILY, 20, "bold"))
        style_label(icon_label, fg=icon_color)
        icon_label.pack(side="left", padx=(0, 14), anchor="n")

        msg_label = tk.Label(top_row, text=str(message), justify="left", anchor="w", wraplength=380)
        style_label(msg_label, font=(UI_FAMILY, 10))
        msg_label.pack(side="left", fill="both", expand=True)

        btn_row = tk.Frame(body, bg=BG_DARK)
        btn_row.pack(fill="x", pady=(16, 0))

        if buttons == "yesno":
            no_btn = RoundedButton(btn_row, text="No", command=self._on_no,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=80)
            no_btn.pack(side="right", padx=4)
            yes_btn = RoundedButton(btn_row, text="Yes", command=self._on_yes,
                                     bg=ACCENT_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=80)
            yes_btn.pack(side="right", padx=4)
        else:
            ok_btn = RoundedButton(btn_row, text="OK", command=self._on_ok,
                                    bg=ACCENT_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=80)
            ok_btn.pack(side="right", padx=4)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.transient(parent)
        self.update_idletasks()
        self._center_on_parent(parent)
        self._safe_grab()

    def _center_on_parent(self, parent):
        center_toplevel_on_parent(self, parent)

    def _safe_grab(self, attempt=0):
        try:
            self.update_idletasks()
            self.grab_set()
        except tk.TclError:
            if attempt < 20:
                self.after(50, lambda: self._safe_grab(attempt + 1))
            return
        self.deiconify()
        self.lift()
        self.focus_force()
        self.wait_visibility()
        self.grab_set()
        self.focus_set()

    def _on_ok(self):
        self.result = True
        self.destroy()

    def _on_yes(self):
        self.result = True
        self.destroy()

    def _on_no(self):
        self.result = False
        self.destroy()

    def _on_close(self):
        self.destroy()


def _dark_msg_parent(parent):
    return parent or getattr(tk, "_default_root", None)


def dark_showinfo(title=None, message=None, parent=None, **kwargs):
    root = _dark_msg_parent(parent)
    dlg = _DarkMessageDialog(root, title, message, kind="info", buttons="ok")
    root.wait_window(dlg)


def dark_showwarning(title=None, message=None, parent=None, **kwargs):
    root = _dark_msg_parent(parent)
    dlg = _DarkMessageDialog(root, title, message, kind="warning", buttons="ok")
    root.wait_window(dlg)


def dark_showerror(title=None, message=None, parent=None, **kwargs):
    root = _dark_msg_parent(parent)
    dlg = _DarkMessageDialog(root, title, message, kind="error", buttons="ok")
    root.wait_window(dlg)


def dark_askyesno(title=None, message=None, parent=None, **kwargs):
    root = _dark_msg_parent(parent)
    dlg = _DarkMessageDialog(root, title, message, kind="question", buttons="yesno")
    root.wait_window(dlg)
    return bool(dlg.result)


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_last_import_root():
    data = load_config()
    path = data.get("import_root")
    if path and os.path.isdir(path):
        return path
    return guess_default_import_root()


def load_last_sample_dir():
    data = load_config()
    path = data.get("last_sample_dir")
    if path and os.path.isdir(path):
        return path
    return None


def save_config_value(key, value):
    data = load_config()
    data[key] = value
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Could not save configuration: {e}")


def save_last_import_root(path):
    save_config_value("import_root", path)


def save_last_sample_dir(path):
    save_config_value("last_sample_dir", path)


def load_default_autoplay():
    return bool(load_config().get("default_autoplay", False))


def load_default_slices():
    val = load_config().get("default_slices", 8)
    return val if val in SLICE_COUNTS else 8


def load_storage_warning_mb():
    val = load_config().get("storage_warning_mb", 10)
    try:
        val = float(val)
        return val if val > 0 else 10.0
    except (TypeError, ValueError):
        return 10.0


def load_ffmpeg_override():
    return load_config().get("ffmpeg_path") or ""


def load_ffprobe_override():
    return load_config().get("ffprobe_path") or ""


def apply_saved_ffmpeg_overrides():
    """Applies any manually-configured ffmpeg/ffprobe paths from Settings on
    top of the auto-detected ones from startup. Called once at launch,
    before the pydub/ffmpeg dependency check, so a working manual override
    from a previous session doesn't get flagged as missing."""
    global FFMPEG_AVAILABLE
    if not PYDUB_AVAILABLE:
        return
    ffmpeg_override = load_ffmpeg_override()
    ffprobe_override = load_ffprobe_override()
    if ffmpeg_override and os.path.exists(ffmpeg_override):
        AudioSegment.converter = ffmpeg_override
        AudioSegment.ffmpeg = ffmpeg_override
        FFMPEG_AVAILABLE = True
    if ffprobe_override and os.path.exists(ffprobe_override):
        AudioSegment.ffprobe = ffprobe_override


def apply_saved_storage_threshold():
    global MAX_UPLOAD_BYTES
    MAX_UPLOAD_BYTES = int(load_storage_warning_mb() * 1024 * 1024)


def get_wav_info(path):
    with contextlib.closing(wave.open(path, "r")) as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        channels = wf.getnchannels()
        duration = frames / float(rate)
        return duration, rate, channels


def get_wav_sample_width(path):
    """Bit depth in bytes (2 = 16-bit, 3 = 24-bit, 4 = 32-bit) of a wav file,
    or None if it can't be read. The P-6 requires 16-bit PCM."""
    try:
        with contextlib.closing(wave.open(path, "r")) as wf:
            return wf.getsampwidth()
    except Exception:
        return None


def check_duration_warning(path, target_rate=None, pitch_cents=0, force_mono=False):
    try:
        duration, rate, channels = get_wav_info(path)
    except Exception:
        return None
    if pitch_cents:
        duration = duration / pitch_speed_factor(pitch_cents)
    rate = target_rate or rate
    ch_key = 1 if (force_mono or channels == 1) else 2
    limit = MAX_SECONDS.get((rate, ch_key))
    if limit and duration > limit:
        pitch_note = f" (with pitch {pitch_cents:+d}c)" if pitch_cents else ""
        return (f"Sample is {duration:.1f}s long{pitch_note}, but at {rate}Hz/"
                f"{'Mono' if ch_key==1 else 'Stereo'} only {limit}s are possible.")
    return None


def _mp3_duration_via_ffprobe(path):
    """Reads just the duration from an mp3's container metadata via ffprobe,
    which is far faster than decoding the whole file just to measure it."""
    ffprobe = getattr(AudioSegment, "ffprobe", None) if PYDUB_AVAILABLE else None
    ffprobe = ffprobe or shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=5
        )
        return float(result.stdout.strip())
    except Exception:
        return None


_duration_cache = {}  # path -> (mtime, duration) - avoids rescanning folders on every open/navigation


def get_audio_duration_seconds(path):
    """Duration in seconds for a .wav or .mp3 file, or None if it can't be read.
    Cached per file (invalidated automatically if the file's mtime changes),
    since folder listings call this for every audio file, every time you
    open a browse dialog or navigate into a folder."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None

    cached = _duration_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    duration = None
    try:
        if path.lower().endswith(".wav"):
            duration, _, _ = get_wav_info(path)
        elif path.lower().endswith(".mp3"):
            duration = _mp3_duration_via_ffprobe(path)
            if duration is None and PYDUB_AVAILABLE:
                audio = AudioSegment.from_file(path)
                duration = len(audio) / 1000.0
    except Exception:
        duration = None

    _duration_cache[path] = (mtime, duration)
    return duration


def format_duration(seconds):
    if seconds is None:
        return "--:--"
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes}:{secs:04.1f}"


def format_size(num_bytes):
    if num_bytes is None:
        return "--"
    mb = num_bytes / (1024 * 1024)
    if mb >= 0.1:
        return f"{mb:.2f}MB"
    return f"{num_bytes / 1024:.1f}KB"


def draw_bracket_marker(canvas, x, height_px, color, side, width_px=3, arm_len=10, tag="marker"):
    """Draws a trim marker shaped like a square bracket ('[' or ']') - a
    thick vertical line with short horizontal arms at top/bottom pointing
    into the selected region, so it reads clearly as 'the edge of the
    selection' instead of a thin, easy-to-miss line.
    side: 'start' (arms point right, like '[') or 'end' (arms point left,
    like ']')."""
    canvas.create_line(x, 0, x, height_px, fill=color, width=width_px, tags=tag)
    direction = 1 if side == "start" else -1
    x2 = x + direction * arm_len
    canvas.create_line(x, 1, x2, 1, fill=color, width=width_px, tags=tag)
    canvas.create_line(x, height_px - 1, x2, height_px - 1, fill=color, width=width_px, tags=tag)


def draw_waveform_on_canvas(canvas, data, start_frac=0.0, end_frac=1.0,
                             width_px=480, height_px=80, color=WAVE_COLOR, tag="waveform",
                             y_offset=0, clear=True):
    """Draws a waveform directly on a Tkinter canvas as a single filled
    polygon (min/max envelope per pixel column). This replaces the previous
    matplotlib -> PNG file -> PhotoImage round-trip, which was by far the
    slowest part of the UI (figure creation, disk I/O, PNG decode on every
    redraw). Pure in-memory numpy + one canvas.create_polygon() call.

    y_offset/height_px let you draw into a sub-region of a taller canvas
    (used to stack left/right channels for stereo samples). clear=False lets
    you draw a second waveform under the same tag without wiping the first."""
    if clear:
        canvas.delete(tag)
    if data is None:
        return
    n = len(data)
    if n == 0:
        return
    start_i = max(0, min(int(start_frac * n), n - 1))
    end_i = max(start_i + 1, min(int(end_frac * n), n))
    segment = data[start_i:end_i]
    seg_len = len(segment)
    if seg_len == 0:
        return

    width_px = max(1, int(width_px))
    mid_y = y_offset + height_px / 2.0
    scale = (height_px / 2.0) * 0.95

    if seg_len <= width_px:
        # Too few samples to fill every column - just draw the raw points.
        points = []
        for i in range(seg_len):
            x = (i / max(seg_len - 1, 1)) * width_px
            y = mid_y - float(segment[i]) * scale
            points.extend([x, y])
        if len(points) >= 4:
            canvas.create_line(*points, fill=color, width=1, tags=tag)
        return

    samples_per_px = seg_len / width_px
    top_points = []
    bottom_points = []
    for px in range(width_px):
        lo = int(px * samples_per_px)
        hi = int((px + 1) * samples_per_px)
        hi = max(hi, lo + 1)
        hi = min(hi, seg_len)
        if lo >= seg_len:
            break
        chunk = segment[lo:hi]
        if len(chunk) == 0:
            v_min = v_max = 0.0
        else:
            v_min = float(chunk.min())
            v_max = float(chunk.max())
        top_points.append((px, mid_y - v_max * scale))
        bottom_points.append((px, mid_y - v_min * scale))

    if not top_points:
        return

    poly = []
    for x, y in top_points:
        poly.extend([x, y])
    for x, y in reversed(bottom_points):
        poly.extend([x, y])

    canvas.create_polygon(*poly, fill=color, outline=color, tags=tag)


def find_zero_crossing(data, target_idx, search_radius):
    """Finds the sample index closest to target_idx (within +/- search_radius)
    where the (mono-mixed) signal crosses zero. Falls back to target_idx
    unchanged if no crossing is found in range - callers should then fall
    back to a short fade instead."""
    n = len(data)
    if n < 2:
        return target_idx
    mono = data.mean(axis=1) if data.ndim > 1 else data
    target_idx = max(0, min(target_idx, n - 1))
    lo = max(0, target_idx - search_radius)
    hi = min(n - 2, target_idx + search_radius)
    if lo >= hi:
        return target_idx

    best_idx, best_dist = None, None
    for i in range(lo, hi + 1):
        if mono[i] == 0 or (mono[i] < 0) != (mono[i + 1] < 0):
            idx = i if abs(mono[i]) <= abs(mono[i + 1]) else i + 1
            dist = abs(idx - target_idx)
            if best_dist is None or dist < best_dist:
                best_idx, best_dist = idx, dist
    return best_idx if best_idx is not None else target_idx


def trim_wav_file(src_path, start_frac, end_frac, fade_ms=0.5, snap_ms=5):
    data, fs = sf.read(src_path, dtype="float32")
    n = len(data)
    start_i = max(0, min(int(start_frac * n), n - 1))
    end_i = max(start_i + 1, min(int(end_frac * n), n))

    # Prefer snapping cut points to natural zero-crossings (no volume change,
    # no lost attack) over fading; fall back to a tiny safety fade for
    # whatever a crossing search couldn't clean up.
    radius = int(fs * snap_ms / 1000)
    start_i = find_zero_crossing(data, start_i, radius)
    end_i = find_zero_crossing(data, end_i, radius)
    if end_i <= start_i:
        end_i = min(n, start_i + 1)

    trimmed = data[start_i:end_i]
    trimmed = apply_micro_fade(trimmed, fs, fade_ms=fade_ms)
    out_dir = os.path.join(os.path.expanduser("~"), ".p6_trim_tmp")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"trim_{uuid.uuid4().hex[:8]}.wav")
    sf.write(out_path, trimmed, fs, subtype="PCM_16")  # P-6 requires 16-bit PCM
    return out_path


def ensure_mono_wav(path):
    """Returns a mono version of the given wav file. If it's already mono,
    returns the path unchanged; otherwise mixes down to mono and writes a
    small temp file, returning that path instead."""
    try:
        data, fs = sf.read(path, dtype="float32")
    except Exception:
        return path
    if data.ndim == 1:
        return path
    mono = data.mean(axis=1)
    out_dir = os.path.join(os.path.expanduser("~"), ".p6_trim_tmp")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"mono_{uuid.uuid4().hex[:8]}.wav")
    try:
        sf.write(out_path, mono, fs, subtype="PCM_16")  # P-6 requires 16-bit PCM
    except Exception:
        return path
    return out_path


def apply_micro_fade(data, fs, fade_ms=2):
    """Wendet einen linearen Fade-In/Fade-Out von fade_ms Millisekunden an,
    um Klick-Artefakte an harten Schnittkanten zu vermeiden."""
    fade_len = int(fs * fade_ms / 1000)
    fade_len = min(fade_len, len(data) // 2)
    if fade_len <= 0:
        return data
    fade_in = np.linspace(0.0, 1.0, fade_len)
    fade_out = np.linspace(1.0, 0.0, fade_len)
    out = data.copy()
    if out.ndim == 1:
        out[:fade_len] *= fade_in
        out[-fade_len:] *= fade_out
    else:
        out[:fade_len] *= fade_in[:, None]
        out[-fade_len:] *= fade_out[:, None]
    return out


def snap_ms_backward_to_zero(audio_segment, target_ms, search_ms=5):
    """Finds a zero-crossing at or before target_ms (never after - used for
    Chop, where a slice must never exceed slice_ms) within search_ms.
    Returns target_ms unchanged if no crossing is found nearby."""
    fs = audio_segment.frame_rate
    target_idx = int(target_ms * fs / 1000)
    if target_idx <= 1:
        return target_ms
    samples = np.array(audio_segment.get_array_of_samples()).astype(np.float32)
    if audio_segment.channels > 1:
        samples = samples.reshape((-1, audio_segment.channels))
    n = len(samples)
    target_idx = min(target_idx, n - 1)
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    radius = int(fs * search_ms / 1000)
    lo = max(0, target_idx - radius)

    for i in range(target_idx - 1, lo - 1, -1):
        if mono[i] == 0 or (mono[i] < 0) != (mono[i + 1] < 0):
            idx = i if abs(mono[i]) <= abs(mono[i + 1]) else i + 1
            return int(idx * 1000 / fs)
    return target_ms


def pitch_speed_factor(cents):
    """Duration/speed multiplier for a "vari-speed" pitch shift of `cents`
    cents. >1 means faster playback / shorter duration (pitch up)."""
    return 2.0 ** (cents / 1200.0)


def apply_pitch_shift(audio_segment, cents):
    """Pitch-shifts a pydub AudioSegment by `cents` using the classic
    vari-speed trick: relabel the frame rate (changes pitch AND speed
    together, like a tape/turntable), then resample back to the original
    frame rate so it stays playable at a standard rate. Duration changes
    accordingly (shorter when pitched up, longer when pitched down) -
    exactly like slowing down or speeding up a physical sample."""
    if not cents:
        return audio_segment
    factor = pitch_speed_factor(cents)
    new_rate = int(audio_segment.frame_rate * factor)
    if new_rate <= 0:
        return audio_segment
    shifted = audio_segment._spawn(audio_segment.raw_data, overrides={"frame_rate": new_rate})
    return shifted.set_frame_rate(audio_segment.frame_rate)


def compute_export_ready_path(filepath, target_rate, pitch_cents=0, force_mono=False):
    """Same logic as SampleSlot.get_export_ready_path(), but works from plain
    values instead of a live widget - lets us export banks that aren't the
    currently displayed/active one, without needing a live Tk widget for them."""
    if not filepath or not PYDUB_AVAILABLE:
        return filepath
    try:
        _, orig_rate, orig_channels = get_wav_info(filepath)
    except Exception:
        return filepath
    orig_sample_width = get_wav_sample_width(filepath) or 2
    needs_mono = force_mono and orig_channels > 1
    needs_bit_depth_fix = orig_sample_width != 2  # P-6 requires 16-bit PCM
    if target_rate == orig_rate and not pitch_cents and not needs_mono and not needs_bit_depth_fix:
        return filepath
    audio = AudioSegment.from_wav(filepath)
    if pitch_cents:
        audio = apply_pitch_shift(audio, pitch_cents)
    if needs_mono:
        audio = audio.set_channels(1)
    audio = audio.set_frame_rate(target_rate)
    audio = audio.set_sample_width(2)  # always force 16-bit for the P-6
    suffix = f"_{target_rate}Hz"
    if pitch_cents:
        suffix += f"_{pitch_cents:+d}c"
    if needs_mono:
        suffix += "_mono"
    if needs_bit_depth_fix:
        suffix += "_16bit"
    temp_path = os.path.splitext(filepath)[0] + suffix + ".wav"
    audio.export(temp_path, format="wav")
    return temp_path


def convert_to_wav_if_needed(path):
    if path.lower().endswith(".wav"):
        return path, False
    if not PYDUB_AVAILABLE:
        dark_showerror("pydub missing", "MP3 conversion requires pydub + ffmpeg.")
        return path, False
    try:
        sound = AudioSegment.from_file(path)
        wav_path = os.path.splitext(path)[0] + "_converted.wav"
        sound.export(wav_path, format="wav")
        return wav_path, True
    except Exception as e:
        dark_showerror("Conversion Error", f"Details: {e}")
        return path, False


def build_chop_file(file_paths, rate, channels, num_slices, normalize_audio=False):
    if not PYDUB_AVAILABLE:
        raise RuntimeError("pydub is required for the Chop feature.")

    limit = MAX_SECONDS.get((rate, channels))
    if not limit:
        raise ValueError(f"No duration limit defined for {rate}Hz/{channels}ch.")

    # Slice boundaries are derived from one exact grid over the full allowed
    # length, and each slice's length is the DIFFERENCE between consecutive
    # boundaries. A single truncated per-slice length (int(limit/n*1000))
    # would instead lose a fraction of a millisecond on every slice, and
    # those losses accumulate - by the last slice the content could sit up to
    # ~44ms earlier than a fixed device-side grid would expect. This way the
    # total always lands exactly on `limit`, and any rounding stays below 1ms
    # and never adds up.
    total_ms = int(round(limit * 1000))
    boundaries = [int(round(i * total_ms / num_slices)) for i in range(num_slices + 1)]

    combined = AudioSegment.silent(duration=0, frame_rate=rate)
    if channels == 2:
        combined = combined.set_channels(2)
    else:
        combined = combined.set_channels(1)
    combined = combined.set_sample_width(2)  # P-6 requires 16-bit PCM

    for idx, path in enumerate(file_paths):
        slice_ms = boundaries[idx + 1] - boundaries[idx]
        audio = AudioSegment.from_file(path)
        audio = audio.set_frame_rate(rate)
        audio = audio.set_channels(channels)
        audio = audio.set_sample_width(2)  # normalize bit depth before combining

        trimmed_start = detect_leading_silence(audio)
        audio = audio[trimmed_start:]

        if len(audio) > slice_ms:
            # Snap the cut to a nearby zero-crossing (never later than
            # slice_ms, so the slice never grows past its slot) instead of
            # a hard cut - avoids clicks without touching the attack/volume.
            cut_ms = snap_ms_backward_to_zero(audio, slice_ms)
            audio = audio[:cut_ms]

        if len(audio) < slice_ms:
            # Snap the natural end of the audio to zero too, so the
            # transition into the silence padding is click-free.
            tail_ms = snap_ms_backward_to_zero(audio, len(audio))
            audio = audio[:tail_ms]
            pad = AudioSegment.silent(duration=slice_ms - len(audio), frame_rate=rate)
            pad = pad.set_channels(channels)
            pad = pad.set_sample_width(2)
            audio = audio + pad

        combined += audio

    # Fewer samples than slices -> fill the remaining slots with silence so
    # the total duration lands exactly on the allowed length. Taken straight
    # from the boundary grid (rather than multiplying a per-slice length), so
    # this stays exact no matter how the rounding fell for individual slices.
    if len(file_paths) < num_slices:
        remaining_ms = total_ms - boundaries[len(file_paths)]
        if remaining_ms > 0:
            silence = AudioSegment.silent(duration=remaining_ms, frame_rate=rate)
            silence = silence.set_channels(channels)
            silence = silence.set_sample_width(2)
            combined += silence

    if normalize_audio:
        combined = pydub_normalize(combined)

    return combined


class FolderNavMixin:
    """Shared address-bar + quick-access folder navigation. Any class using
    this must keep a `self.current_dir` and implement `self.refresh_list()`."""

    def _build_nav_bar(self, container_bg=BG_DARK):
        top = tk.Frame(self, padx=10, pady=10, bg=container_bg)
        top.pack(fill="x")
        up_btn = RoundedButton(top, text="\u2191 Up", command=self.go_up,
                                bg=BG_INPUT, fg=FG_TEXT, parent_bg=container_bg, width=60, height=28)
        up_btn.pack(side="left", padx=(0, 6))
        self.path_entry = tk.Entry(top, bg=BG_INPUT, fg=FG_TEXT,
                                    insertbackground=FG_TEXT, relief="flat",
                                    highlightthickness=1, highlightbackground=BORDER_COLOR,
                                    highlightcolor=ACCENT_BLUE, font=(UI_FAMILY, 9))
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.path_entry.bind("<Return>", self.go_to_typed_path)
        go_btn = RoundedButton(top, text="Go", command=self.go_to_typed_path,
                                bg=ACCENT_BLUE, fg="#FFFFFF", parent_bg=container_bg, width=50, height=28)
        go_btn.pack(side="left", padx=(6, 0))

        quick_row = tk.Frame(self, padx=10, bg=container_bg)
        quick_row.pack(fill="x", pady=(4, 0))
        quick_lbl = tk.Label(quick_row, text="Quick access:")
        style_label(quick_lbl, bg=container_bg, fg=FG_MUTED, font=(UI_FAMILY, 8))
        quick_lbl.pack(side="left", padx=(0, 6))
        for label, path in self._quick_access_locations():
            qb = RoundedButton(quick_row, text=label, command=lambda p=path: self.navigate_to(p),
                                bg=BG_INPUT, fg=FG_TEXT, parent_bg=container_bg, width=80, height=24,
                                font=(UI_FAMILY, 8, "bold"))
            qb.pack(side="left", padx=2)

    def _quick_access_locations(self):
        home = os.path.expanduser("~")
        candidates = [
            ("Home", home),
            ("Desktop", os.path.join(home, "Desktop")),
            ("Downloads", os.path.join(home, "Downloads")),
        ]

        if os.name == "nt":
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    candidates.append((f"{letter}:\\", drive))
        else:
            candidates.append(("Root /", "/"))
            candidates.append(("Media", "/run/media"))
            candidates.append(("Mnt", "/mnt"))
            candidates.append(("Volumes", "/Volumes"))  # macOS

        return [(label, path) for label, path in candidates if os.path.isdir(path)]

    def navigate_to(self, path):
        if os.path.isdir(path):
            self.current_dir = path
            self.refresh_list()
        else:
            dark_showwarning("Nicht gefunden", f"Ordner existiert nicht:\n{path}", parent=self)

    def go_up(self):
        parent_dir = os.path.dirname(self.current_dir.rstrip(os.sep)) or os.sep
        self.navigate_to(parent_dir)

    def go_to_typed_path(self, event=None):
        typed = self.path_entry.get().strip()
        if not typed:
            return
        typed = os.path.expanduser(typed)
        self.navigate_to(typed)

    def _update_path_entry(self):
        if hasattr(self, "path_entry"):
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, self.current_dir)


class FolderPickerDialog(FolderNavMixin, tk.Toplevel):
    """Dark-themed replacement for filedialog.askdirectory(), since native OS
    folder dialogs cannot be restyled through Tkinter. Used where a dedicated
    folder-selection step makes sense on its own (e.g. the IMPORT root)."""

    def __init__(self, parent, initial_dir=None, title="Select Folder"):
        super().__init__(parent)
        self.title(title)
        self.geometry(f"{PREVIEW_MIN_W}x{PREVIEW_MIN_H}")
        self.minsize(PREVIEW_MIN_W, PREVIEW_MIN_H)
        style_toplevel(self)
        self.selected_dir = None
        self.current_dir = initial_dir if initial_dir and os.path.isdir(initial_dir) else os.path.expanduser("~")

        self._build_nav_bar(container_bg=BG_DARK)

        list_frame = tk.Frame(self, padx=10, pady=6, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        style_listbox(self.listbox)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self.on_navigate)
        self.listbox.bind("<Return>", self.on_navigate)
        self.listbox.bind("<BackSpace>", lambda e: self.go_up())

        hint = tk.Label(self, text="Doppelklick/Enter: Ordner öffnen  \u2022  \u2191 Up oder Backspace: hoch  "
                                    "\u2022  Pfad oben eintippen/einfügen + Enter",
                         anchor="w")
        style_label(hint, fg=FG_MUTED, font=(UI_FAMILY, 8))
        hint.pack(fill="x", padx=10)

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        cancel_btn.pack(side="right", padx=4)
        select_btn = RoundedButton(btn_row, text="Diesen Ordner wählen", command=self.on_confirm,
                                    bg=ACCENT_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=170)
        select_btn.pack(side="right", padx=4)

        self.refresh_list()
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _safe_grab(self, attempt=0):
        try:
            self.update_idletasks()
            self.grab_set()
        except tk.TclError:
            if attempt < 20:
                self.after(50, lambda: self._safe_grab(attempt + 1))
            return
        self.deiconify()
        self.lift()
        self.focus_force()
        self.wait_visibility()
        self.grab_set()

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        self._update_path_entry()
        try:
            entries = sorted(
                e for e in os.listdir(self.current_dir)
                if os.path.isdir(os.path.join(self.current_dir, e))
            )
        except Exception as e:
            entries = []
            print(f"Could not read folder: {e}")
        self.listbox.insert(tk.END, "..")
        for entry in entries:
            self.listbox.insert(tk.END, entry)

    def on_navigate(self, event):
        sel = self.listbox.curselection()
        if not sel:
            return
        entry = self.listbox.get(sel[0])
        if entry == "..":
            self.go_up()
        else:
            self.navigate_to(os.path.join(self.current_dir, entry))

    def on_confirm(self):
        self.selected_dir = self.current_dir
        self.destroy()

    def on_cancel(self):
        self.selected_dir = None
        self.destroy()

class AudioPreviewDialog(FolderNavMixin, tk.Toplevel):
    def __init__(self, parent, initial_dir=None):
        super().__init__(parent)
        self.title("Select Sample (with Preview)")
        self.geometry(f"{AUDIO_PREVIEW_MIN_W}x{AUDIO_PREVIEW_MIN_H}")
        self.minsize(AUDIO_PREVIEW_MIN_W, AUDIO_PREVIEW_MIN_H)
        style_toplevel(self)
        self.selected_path = None
        self.current_dir = initial_dir or os.path.expanduser("~")
        self.autoplay_var = tk.BooleanVar(value=load_default_autoplay())

        self._build_nav_bar(container_bg=BG_DARK)

        ensure_dark_treeview_style()
        list_frame = tk.Frame(self, padx=10, pady=6, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = ttk.Treeview(list_frame, columns=("length", "size"), show="tree headings",
                                     selectmode="browse", yscrollcommand=scrollbar.set,
                                     style="Dark.Treeview")
        self.listbox.heading("#0", text="Name", anchor="w")
        self.listbox.heading("length", text="Length", anchor="e")
        self.listbox.heading("size", text="Size", anchor="e")
        self.listbox.column("#0", anchor="w", width=380, stretch=True)
        self.listbox.column("length", anchor="e", width=80, stretch=False)
        self.listbox.column("size", anchor="e", width=80, stretch=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<<TreeviewSelect>>", self.on_select)
        self.listbox.bind("<Double-Button-1>", self.on_confirm)
        self.listbox.bind("<BackSpace>", lambda e: self.go_up())

        autoplay_row = tk.Frame(self, padx=10, bg=BG_DARK)
        autoplay_row.pack(fill="x")
        autoplay_cb = tk.Checkbutton(autoplay_row, text="Autoplay (play sound on click)",
                              variable=self.autoplay_var)
        style_checkbutton(autoplay_cb)
        autoplay_cb.pack(side="left")

        zoom_row = tk.Frame(autoplay_row, bg=BG_DARK)
        zoom_row.pack(side="left", padx=(16, 0))
        zoom_out_btn = RoundedButton(zoom_row, text="\u2212", command=self.zoom_out,
                                      bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                      width=28, height=22, font=(UI_FAMILY, 10, "bold"))
        zoom_out_btn.pack(side="left", padx=1)
        self.zoom_label = tk.Label(zoom_row, text="1.0x")
        style_label(self.zoom_label, bg=BG_DARK, fg=FG_MUTED, font=(UI_FAMILY, 8, "bold"))
        self.zoom_label.pack(side="left", padx=4)
        zoom_in_btn = RoundedButton(zoom_row, text="+", command=self.zoom_in,
                                     bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                     width=28, height=22, font=(UI_FAMILY, 10, "bold"))
        zoom_in_btn.pack(side="left", padx=1)
        zoom_reset_btn = RoundedButton(zoom_row, text="Reset", command=self.zoom_reset,
                                        bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                        width=55, height=22, font=(UI_FAMILY, 8, "bold"))
        zoom_reset_btn.pack(side="left", padx=(6, 0))

        self.duration_label = tk.Label(autoplay_row, text="")
        style_label(self.duration_label, bg=BG_DARK, fg=ACCENT_BLUE, font=(UI_FAMILY, 9, "bold"))
        self.duration_label.pack(side="right")

        self.wave_width = 480
        self.wave_height = 144
        self.wave_canvas = tk.Canvas(self, bg=WAVE_BG, width=self.wave_width,
                                      height=self.wave_height, highlightthickness=0,
                                      cursor="sb_h_double_arrow")
        self.wave_canvas.pack(fill="both", expand=True, padx=10, pady=(8, 2))
        self.wave_canvas.bind("<Configure>", self._on_wave_canvas_resize)
        self.wave_scrollbar = tk.Scrollbar(self, orient="horizontal", command=self.on_wave_scroll)
        # Not packed here on purpose - only shown once zoomed in.
        self.waveform_img = None
        self._wave_data_stereo = None  # (n, channels) raw data when the sample is stereo
        self.trim_start_frac = 0.0
        self.trim_end_frac = 1.0
        self.drag_target = None
        self.is_playing = False
        self.play_start_time = None
        self.play_duration = 0.0
        self.current_audio_path = None
        self._wave_data = None
        self._wave_fs = None
        self.zoom_factor = 1.0
        self.view_start_frac = 0.0
        self.view_span_frac = 1.0
        self.center_frac = 0.5   # what the zoomed view is focused on

        self.wave_canvas.bind("<ButtonPress-1>", self.on_wave_press)
        self.wave_canvas.bind("<B1-Motion>", self.on_wave_drag)
        self.wave_canvas.bind("<ButtonRelease-1>", self.on_wave_release)
        self.wave_canvas.bind("<MouseWheel>", self.on_wave_mousewheel)
        self.wave_canvas.bind("<Button-4>", self.on_wave_mousewheel)
        self.wave_canvas.bind("<Button-5>", self.on_wave_mousewheel)

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        preview_btn = RoundedButton(btn_row, text="Preview", command=self.preview_selected,
                                     bg=ACCENT_BLUE, fg="#FFFFFF", parent_bg=BG_DARK)
        preview_btn.pack(side="left", padx=4)
        stop_btn = RoundedButton(btn_row, text="Stop", command=self.stop_preview,
                                  bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        stop_btn.pack(side="left", padx=4)
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        cancel_btn.pack(side="right", padx=4)
        select_btn = RoundedButton(btn_row, text="Select", command=self.on_confirm,
                                    bg=ACCENT_GREEN, fg="#FFFFFF", parent_bg=BG_DARK)
        select_btn.pack(side="right", padx=4)

        self.refresh_list()
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _safe_grab(self, attempt=0):
        try:
            self.update_idletasks()
            self.grab_set()
        except tk.TclError:
            if attempt < 20:
                self.after(50, lambda: self._safe_grab(attempt + 1))
            return
        self.deiconify()
        self.lift()
        self.focus_force()
        self.wait_visibility()
        self.grab_set()

    def refresh_list(self):
        for item in self.listbox.get_children():
            self.listbox.delete(item)
        self._update_path_entry()
        try:
            entries = sorted(os.listdir(self.current_dir))
        except Exception as e:
            entries = []
            print(f"Could not read folder: {e}")
        self._entries = [".."]
        self.listbox.insert("", tk.END, iid="0", text="..")
        for entry in entries:
            full = os.path.join(self.current_dir, entry)
            idx = len(self._entries)
            if os.path.isdir(full):
                self._entries.append(f"[Folder] {entry}")
                self.listbox.insert("", tk.END, iid=str(idx), text=f"[Folder] {entry}")
            elif entry.lower().endswith((".wav", ".mp3")):
                duration = get_audio_duration_seconds(full)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = None
                self._entries.append(entry)
                self.listbox.insert("", tk.END, iid=str(idx), text=entry,
                                     values=(format_duration(duration), format_size(size)))

    def get_selected_entry(self):
        sel = self.listbox.selection()
        if not sel:
            return None
        idx = int(sel[0])
        if idx >= len(self._entries):
            return None
        return self._entries[idx]

    def on_select(self, event):
        entry = self.get_selected_entry()
        if entry and not entry.startswith("[Folder]") and entry != "..":
            self.selected_path = os.path.join(self.current_dir, entry)
            self.show_waveform(self.selected_path)
            if self.autoplay_var.get():
                self.preview_selected()
        else:
            self.selected_path = None
            self.wave_canvas.delete("all")

    def show_waveform(self, path):
        wav_path = path
        if path.lower().endswith(".mp3") and PYDUB_AVAILABLE:
            try:
                sound = AudioSegment.from_file(path)
                wav_path = os.path.join(os.path.expanduser("~"), ".p6_waveform_src.wav")
                sound.export(wav_path, format="wav")
            except Exception:
                self._wave_data = None
                self.wave_canvas.delete("all")
                self.wave_canvas.create_text(self.wave_width // 2, self.wave_height // 2,
                                              text="(No preview)", fill=FG_MUTED)
                return

        self.current_audio_path = wav_path
        self.trim_start_frac = 0.0
        self.trim_end_frac = 1.0
        self.zoom_factor = 1.0
        self.center_frac = 0.5

        try:
            data, fs = sf.read(wav_path, dtype="float32")
            if data.ndim > 1 and data.shape[1] >= 2:
                self._wave_data_stereo = data
                self._wave_data = data.mean(axis=1)
            else:
                self._wave_data_stereo = None
                self._wave_data = data.reshape(-1) if data.ndim > 1 else data
            self._wave_fs = fs
            self.play_duration = len(data) / float(fs) if fs else 0.0
        except Exception:
            self._wave_data = None
            self._wave_data_stereo = None
            self._wave_fs = None
            self.play_duration = 0.0

        self.render_and_draw_wave()

    def _update_view_window(self):
        self.zoom_factor = max(1.0, self.zoom_factor)
        self.view_span_frac = 1.0 / self.zoom_factor
        start = self.center_frac - self.view_span_frac / 2.0
        start = max(0.0, min(start, 1.0 - self.view_span_frac))
        self.view_start_frac = start

    def frac_to_x(self, frac):
        if self.view_span_frac <= 0:
            return 0
        return (frac - self.view_start_frac) / self.view_span_frac * self.wave_width

    def x_to_frac(self, x):
        return self.view_start_frac + (x / self.wave_width) * self.view_span_frac

    def render_and_draw_wave(self):
        """Used after zoom changes or a marker drag: re-centers the view on
        the markers, then renders."""
        self._update_view_window()
        self._render_wave_at_current_view()

    def _on_wave_canvas_resize(self, event=None):
        """The canvas is packed to fill available space and can end up a
        different size than the fixed wave_width/wave_height used for
        drawing/marker/zoom math - keep them in sync so the waveform scales
        with the window instead of leaving empty space or getting clipped."""
        new_width = event.width if event else self.wave_canvas.winfo_width()
        new_height = event.height if event else self.wave_canvas.winfo_height()
        changed = False
        if new_width > 10 and new_width != self.wave_width:
            self.wave_width = new_width
            changed = True
        if new_height > 10 and new_height != self.wave_height:
            self.wave_height = new_height
            changed = True
        if changed and self._wave_data is not None:
            self._render_wave_at_current_view()


    def _render_wave_at_current_view(self):
        """Renders at whatever view_start_frac/view_span_frac currently are,
        without recentering - used for manual scrollbar/mousewheel panning."""
        if hasattr(self, "zoom_label"):
            self.zoom_label.config(text=f"{self.zoom_factor:.1f}x")
        self._update_scrollbar_visibility()

        self.wave_canvas.delete("all")
        if self._wave_data is None:
            self.wave_canvas.create_text(self.wave_width // 2, self.wave_height // 2,
                                          text="(No preview)", fill=FG_MUTED)
            return

        end_frac = self.view_start_frac + self.view_span_frac
        if self._wave_data_stereo is not None:
            half_h = self.wave_height / 2.0
            draw_waveform_on_canvas(self.wave_canvas, self._wave_data_stereo[:, 0],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=0, clear=True)
            draw_waveform_on_canvas(self.wave_canvas, self._wave_data_stereo[:, 1],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=half_h, clear=False)
            self.wave_canvas.create_line(0, half_h, self.wave_width, half_h,
                                          fill=BORDER_COLOR, width=1, tags="waveform")
        else:
            draw_waveform_on_canvas(self.wave_canvas, self._wave_data, self.view_start_frac,
                                     end_frac, self.wave_width, self.wave_height)
        self.redraw_markers()

    def _update_scrollbar_visibility(self):
        if self.zoom_factor > 1.0 and self._wave_data is not None:
            if not self.wave_scrollbar.winfo_ismapped():
                self.wave_scrollbar.pack(fill="x", padx=10, pady=(0, 4), after=self.wave_canvas)
            first = self.view_start_frac
            last = self.view_start_frac + self.view_span_frac
            self.wave_scrollbar.set(first, last)
        else:
            if self.wave_scrollbar.winfo_ismapped():
                self.wave_scrollbar.pack_forget()

    def _pan_to(self, new_start_frac):
        new_start_frac = max(0.0, min(new_start_frac, 1.0 - self.view_span_frac))
        self.view_start_frac = new_start_frac
        self.center_frac = self.view_start_frac + self.view_span_frac / 2.0
        self._render_wave_at_current_view()

    def on_wave_scroll(self, *args):
        if not args or self.view_span_frac >= 1.0:
            return
        action = args[0]
        if action == "moveto":
            self._pan_to(float(args[1]))
        elif action == "scroll":
            amount = float(args[1])
            unit = args[2] if len(args) > 2 else "units"
            step = self.view_span_frac * (0.1 if unit == "units" else 0.9)
            self._pan_to(self.view_start_frac + amount * step)

    def on_wave_mousewheel(self, event):
        if self.view_span_frac >= 1.0:
            return
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        step = self.view_span_frac * 0.1
        self._pan_to(self.view_start_frac + delta * step)

    def zoom_in(self):
        self.zoom_factor = min(self.zoom_factor * 1.6, 30.0)
        self.render_and_draw_wave()

    def zoom_out(self):
        self.zoom_factor = max(self.zoom_factor / 1.6, 1.0)
        self.render_and_draw_wave()

    def zoom_reset(self):
        self.zoom_factor = 1.0
        self.render_and_draw_wave()

    def redraw_markers(self):
        self.wave_canvas.delete("marker")
        self.wave_canvas.delete("playhead")
        x_start = self.frac_to_x(self.trim_start_frac)
        x_end = self.frac_to_x(self.trim_end_frac)
        if x_start > 0:
            self.wave_canvas.create_rectangle(0, 0, x_start, self.wave_height,
                                               fill=BG_DARK, stipple="gray50", outline="",
                                               tags="marker")
        if x_end < self.wave_width:
            self.wave_canvas.create_rectangle(x_end, 0, self.wave_width, self.wave_height,
                                               fill=BG_DARK, stipple="gray50", outline="",
                                               tags="marker")
        draw_bracket_marker(self.wave_canvas, x_start, self.wave_height, ACCENT_GREEN, "start")
        draw_bracket_marker(self.wave_canvas, x_end, self.wave_height, ACCENT_RED, "end")
        self.update_duration_label()
            
    def update_duration_label(self):
         if not hasattr(self, "duration_label"):
             return
         if self.play_duration <= 0:
             self.duration_label.config(text="")
             return
         region_duration = self.play_duration * (self.trim_end_frac - self.trim_start_frac)
         self.duration_label.config(text=f"Selection: {region_duration:.2f}s")

    def on_wave_press(self, event):
        x_start = self.frac_to_x(self.trim_start_frac)
        x_end = self.frac_to_x(self.trim_end_frac)
        if abs(event.x - x_start) <= 9:
            self.drag_target = "start"
        elif abs(event.x - x_end) <= 9:
            self.drag_target = "end"
        else:
            self.drag_target = None

    def on_wave_drag(self, event):
        if not self.drag_target:
            return
        frac = max(0.0, min(self.x_to_frac(event.x), 1.0))
        if self.drag_target == "start":
            self.trim_start_frac = min(frac, self.trim_end_frac - 0.01)
        elif self.drag_target == "end":
            self.trim_end_frac = max(frac, self.trim_start_frac + 0.01)
        # Cheap during drag; the waveform image itself (which may re-center
        # when zoomed) is only redrawn once the mouse is released.
        self.redraw_markers()

    def on_wave_release(self, event):
        dragged = self.drag_target
        self.drag_target = None
        if dragged and self.zoom_factor > 1.0:
            self.center_frac = self.trim_start_frac if dragged == "start" else self.trim_end_frac
            self.render_and_draw_wave()

    def update_playhead(self):
        if not self.is_playing:
            return
        elapsed = time.time() - self.play_start_time
        region_duration = self.play_duration * (self.trim_end_frac - self.trim_start_frac)
        frac_in_region = min(elapsed / region_duration, 1.0) if region_duration > 0 else 1.0
        abs_frac = self.trim_start_frac + frac_in_region * (self.trim_end_frac - self.trim_start_frac)
        x = self.frac_to_x(abs_frac)
        self.wave_canvas.delete("playhead")
        if 0 <= x <= self.wave_width:
            self.wave_canvas.create_line(x, 0, x, self.wave_height, fill=ACCENT_BLUE, width=2, tags="playhead")
        if frac_in_region < 1.0:
            self.after(30, self.update_playhead)
        else:
            self.is_playing = False
            self.wave_canvas.delete("playhead")

    def get_trimmed_export_path(self):
        if self.trim_start_frac <= 0.001 and self.trim_end_frac >= 0.999:
            return None
        if not self.current_audio_path:
            return None
        return trim_wav_file(self.current_audio_path, self.trim_start_frac, self.trim_end_frac)

    def on_confirm(self, event=None):
        entry = self.get_selected_entry()
        if entry is None:
            return
        if entry == "..":
            self.go_up()
            return
        if entry.startswith("[Folder] "):
            folder_name = entry.replace("[Folder] ", "", 1)
            self.navigate_to(os.path.join(self.current_dir, folder_name))
            return

        original_path = os.path.join(self.current_dir, entry)
        trimmed_path = self.get_trimmed_export_path()
        self.selected_path = trimmed_path if trimmed_path else original_path

        self.stop_preview()
        self.destroy()

    def preview_selected(self):
        entry = self.get_selected_entry()
        if not entry or entry == ".." or entry.startswith("[Folder]"):
            return
        path = os.path.join(self.current_dir, entry)
        try:
            sd.stop()
            play_path = path
            if path.lower().endswith(".mp3"):
                if not PYDUB_AVAILABLE:
                    dark_showerror("pydub missing", "Previewing MP3 requires pydub + ffmpeg.", parent=self)
                    return
                sound = AudioSegment.from_file(path)
                tmp_preview = os.path.join(os.path.expanduser("~"), ".p6_preview_tmp.wav")
                sound.export(tmp_preview, format="wav")
                play_path = tmp_preview

            data, fs = sf.read(play_path, dtype="float32")
            n = len(data)
            start_i = int(self.trim_start_frac * n)
            end_i = int(self.trim_end_frac * n)
            segment = data[start_i:end_i]
            segment = apply_micro_fade(segment, fs, fade_ms=2)
            sd.play(segment, fs)

            self.is_playing = True
            self.play_start_time = time.time()
            self.update_playhead()
        except Exception as e:
            dark_showerror("Error During Preview", str(e), parent=self)

    def stop_preview(self):
        try:
            sd.stop()
        except Exception:
            pass
        self.is_playing = False
        self.wave_canvas.delete("playhead")

    def on_cancel(self):
        self.stop_preview()
        self.selected_path = None
        self.destroy()



class ChopDialog(FolderNavMixin, tk.Toplevel):
    """All-in-one Chop window: a Browse pane (left) to find samples, a
    Selected pane (right) showing the chop order, and a shared waveform view
    with drag markers so you can add a trimmed region of a Browse sample
    straight into the selection."""

    def __init__(self, parent, initial_dir=None):
        super().__init__(parent)
        self.title("Chop - Build Multisample")
        self.geometry(f"{CHOP_MIN_W}x{CHOP_MIN_H}")
        self.minsize(CHOP_MIN_W, CHOP_MIN_H)
        style_toplevel(self)
        self.result_path = None
        self.current_dir = initial_dir or os.path.expanduser("~")
        self.selected_files = []
        self.selected_display_names = []
        self._entries = []
        self.autoplay_var = tk.BooleanVar(value=load_default_autoplay())

        # Shared waveform/trim state -----------------------------------
        self.wave_width = 960
        self.wave_height = 162
        self.wave_mode = "browse"          # "browse" (draggable markers) or "selected" (view only)
        self.current_audio_path = None     # decoded-to-wav path backing the waveform/trim/preview
        self.browse_source_path = None     # original file behind the currently loaded BROWSE item
        self.trim_start_frac = 0.0
        self.trim_end_frac = 1.0
        self.drag_target = None
        self.is_playing = False
        self.play_start_time = None
        self.play_duration = 0.0
        self._wave_data = None             # cached mono float32 samples of current_audio_path
        self._wave_data_stereo = None      # (n, channels) raw data when the sample is stereo
        self._wave_fs = None
        self.zoom_factor = 1.0             # 1.0 = whole file visible
        self.view_start_frac = 0.0         # left edge of the visible window (fraction of full duration)
        self.view_span_frac = 1.0          # width of the visible window (fraction of full duration)
        self.center_frac = 0.5             # what the zoomed view is focused on

        self._build_nav_bar(container_bg=BG_DARK)

        # ----- two panes: Browse | Selected -----
        panes = tk.Frame(self, bg=BG_DARK)
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        panes.grid_columnconfigure(0, weight=1)
        panes.grid_columnconfigure(1, weight=1)
        panes.grid_rowconfigure(0, weight=1)

        # --- LEFT: Browse ---
        left = tk.Frame(panes, bg=BG_DARK)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left_title = tk.Label(left, text="Browse")
        style_label(left_title, bg=BG_DARK, fg=FG_MUTED, font=(UI_FAMILY, 8, "bold"))
        left_title.pack(anchor="w")

        ensure_dark_treeview_style()
        left_list_frame = tk.Frame(left, bg=BG_DARK)
        left_list_frame.pack(fill="both", expand=True, pady=(2, 4))
        left_scrollbar = tk.Scrollbar(left_list_frame)
        left_scrollbar.pack(side="right", fill="y")
        self.listbox = ttk.Treeview(left_list_frame, columns=("length", "size"), show="tree headings",
                                     selectmode="extended", yscrollcommand=left_scrollbar.set,
                                     style="Dark.Treeview")
        self.listbox.heading("#0", text="Name", anchor="w")
        self.listbox.heading("length", text="Length", anchor="e")
        self.listbox.heading("size", text="Size", anchor="e")
        self.listbox.column("#0", anchor="w", width=180, stretch=True)
        self.listbox.column("length", anchor="e", width=60, stretch=False)
        self.listbox.column("size", anchor="e", width=65, stretch=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        left_scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self.on_double_click)
        self.listbox.bind("<<TreeviewSelect>>", self.on_browse_select)
        self.listbox.bind("<BackSpace>", lambda e: self.go_up())

        left_btn_row = tk.Frame(left, bg=BG_DARK)
        left_btn_row.pack(fill="x")
        browse_preview_btn = RoundedButton(left_btn_row, text="Preview", command=self.preview_current,
                                            bg=ACCENT_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=80, height=26)
        browse_preview_btn.pack(side="left", padx=2)
        browse_stop_btn = RoundedButton(left_btn_row, text="Stop", command=self.stop_preview,
                                         bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=70, height=26)
        browse_stop_btn.pack(side="left", padx=2)
        add_btn = RoundedButton(left_btn_row, text="Add to Selection \u2192", command=self.add_selected,
                                 bg=ACCENT_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=160, height=26)
        add_btn.pack(side="right", padx=2)

        # --- RIGHT: Selected ---
        right = tk.Frame(panes, bg=BG_DARK)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.selected_title = tk.Label(right, text="Selected Samples (Chop Order): 0")
        style_label(self.selected_title, bg=BG_DARK, fg=ACCENT_BLUE, font=(UI_FAMILY, 8, "bold"))
        self.selected_title.pack(anchor="w")

        right_list_frame = tk.Frame(right, bg=BG_DARK)
        right_list_frame.pack(fill="both", expand=True, pady=(2, 4))
        right_scrollbar = tk.Scrollbar(right_list_frame)
        right_scrollbar.pack(side="right", fill="y")
        self.selected_listbox = ttk.Treeview(right_list_frame, columns=("length", "size"),
                                              show="tree headings", selectmode="extended",
                                              yscrollcommand=right_scrollbar.set, style="Dark.Treeview")
        self.selected_listbox.heading("#0", text="Name", anchor="w")
        self.selected_listbox.heading("length", text="Length", anchor="e")
        self.selected_listbox.heading("size", text="Size", anchor="e")
        self.selected_listbox.column("#0", anchor="w", width=180, stretch=True)
        self.selected_listbox.column("length", anchor="e", width=60, stretch=False)
        self.selected_listbox.column("size", anchor="e", width=65, stretch=False)
        self.selected_listbox.tag_configure("toolong", foreground=ACCENT_ORANGE)
        self.selected_listbox.pack(side="left", fill="both", expand=True)
        right_scrollbar.config(command=self.selected_listbox.yview)
        self.selected_listbox.bind("<Double-Button-1>", lambda e: self.preview_current())
        self.selected_listbox.bind("<<TreeviewSelect>>", self.on_selected_select)
        self.selected_listbox.bind("<Alt-Up>", lambda e: self.move_up())
        self.selected_listbox.bind("<Alt-Down>", lambda e: self.move_down())

        right_btn_row = tk.Frame(right, bg=BG_DARK)
        right_btn_row.pack(fill="x")
        move_up_btn = RoundedButton(right_btn_row, text="\u2191", command=self.move_up,
                                     bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=36, height=26)
        move_up_btn.pack(side="left", padx=2)
        move_down_btn = RoundedButton(right_btn_row, text="\u2193", command=self.move_down,
                                       bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=36, height=26)
        move_down_btn.pack(side="left", padx=2)
        remove_btn = RoundedButton(right_btn_row, text="Remove", command=self.remove_from_selection,
                                    bg=ACCENT_RED, fg="#FFFFFF", parent_bg=BG_DARK, width=90, height=26)
        remove_btn.pack(side="right", padx=2)

        # ----- shared waveform / trim view -----
        wave_wrap = tk.Frame(self, bg=BG_DARK)
        wave_wrap.pack(fill="x", padx=10)

        wave_header = tk.Frame(wave_wrap, bg=BG_DARK)
        wave_header.pack(fill="x")
        self.wave_name_label = tk.Label(wave_header, text="No sample loaded")
        style_label(self.wave_name_label, bg=BG_DARK, fg=FG_MUTED, font=(UI_FAMILY, 9))
        self.wave_name_label.pack(side="left")
        autoplay_cb = tk.Checkbutton(wave_header, text="Autoplay on click", variable=self.autoplay_var)
        style_checkbutton(autoplay_cb)
        autoplay_cb.pack(side="left", padx=(16, 0))

        zoom_row = tk.Frame(wave_header, bg=BG_DARK)
        zoom_row.pack(side="left", padx=(16, 0))
        zoom_out_btn = RoundedButton(zoom_row, text="\u2212", command=self.zoom_out,
                                      bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                      width=28, height=22, font=(UI_FAMILY, 10, "bold"))
        zoom_out_btn.pack(side="left", padx=1)
        self.zoom_label = tk.Label(zoom_row, text="1.0x")
        style_label(self.zoom_label, bg=BG_DARK, fg=FG_MUTED, font=(UI_FAMILY, 8, "bold"))
        self.zoom_label.pack(side="left", padx=4)
        zoom_in_btn = RoundedButton(zoom_row, text="+", command=self.zoom_in,
                                     bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                     width=28, height=22, font=(UI_FAMILY, 10, "bold"))
        zoom_in_btn.pack(side="left", padx=1)
        zoom_reset_btn = RoundedButton(zoom_row, text="Reset", command=self.zoom_reset,
                                        bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                        width=55, height=22, font=(UI_FAMILY, 8, "bold"))
        zoom_reset_btn.pack(side="left", padx=(6, 0))

        self.duration_label = tk.Label(wave_header, text="")
        style_label(self.duration_label, bg=BG_DARK, fg=ACCENT_BLUE, font=(UI_FAMILY, 9, "bold"))
        self.duration_label.pack(side="right")

        self.wave_canvas = tk.Canvas(wave_wrap, bg=WAVE_BG, width=self.wave_width,
                                      height=self.wave_height, highlightthickness=0,
                                      cursor="sb_h_double_arrow")
        self.wave_canvas.pack(fill="x", pady=(4, 2))
        self.wave_canvas.bind("<Configure>", self._on_wave_canvas_resize)
        self.waveform_img = None
        self.wave_canvas.bind("<ButtonPress-1>", self.on_wave_press)
        self.wave_canvas.bind("<B1-Motion>", self.on_wave_drag)
        self.wave_canvas.bind("<ButtonRelease-1>", self.on_wave_release)
        self.wave_canvas.bind("<MouseWheel>", self.on_wave_mousewheel)   # Windows / macOS
        self.wave_canvas.bind("<Button-4>", self.on_wave_mousewheel)     # Linux scroll up
        self.wave_canvas.bind("<Button-5>", self.on_wave_mousewheel)     # Linux scroll down

        self.wave_scrollbar = tk.Scrollbar(wave_wrap, orient="horizontal", command=self.on_wave_scroll)
        # Not packed here on purpose - only shown once zoomed in (see _update_scrollbar_visibility).

        hint = tk.Label(wave_wrap,
                         text="Sample aus \"Browse\" anklicken, gr\u00fcnen/roten Marker ziehen, dann "
                              "\"Add to Selection\" \u2014 f\u00fcgt nur den markierten Ausschnitt hinzu. "
                              "Ohne Markieren wird die ganze Datei hinzugef\u00fcgt.",
                         anchor="w", justify="left", wraplength=CHOP_MIN_W - 20)
        style_label(hint, bg=BG_DARK, fg=FG_MUTED, font=(UI_FAMILY, 8))
        hint.pack(fill="x", pady=(0, 4))

        # ----- chop build options -----
        opts = tk.Frame(self, padx=10, pady=8, bg=BG_DARK)
        opts.pack(fill="x")

        lbl1 = tk.Label(opts, text="Slices:")
        style_label(lbl1)
        lbl1.grid(row=0, column=0, sticky="w")
        self.slices_var = tk.IntVar(value=load_default_slices())
        om1 = RoundedDropdown(opts, self.slices_var, SLICE_COUNTS, parent_bg=BG_DARK, width=70,
                               command=lambda _v: self.on_options_changed())
        om1.grid(row=0, column=1, padx=6)

        lbl2 = tk.Label(opts, text="Sample Rate:")
        style_label(lbl2)
        lbl2.grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.rate_var = tk.IntVar(value=44100)
        om2 = RoundedDropdown(opts, self.rate_var, TARGET_RATES, parent_bg=BG_DARK, width=90,
                               command=lambda _v: self.on_options_changed())
        om2.grid(row=0, column=3, padx=6)

        self.stereo_var = tk.BooleanVar(value=False)
        self.stereo_cb = tk.Checkbutton(opts, text="Stereo", variable=self.stereo_var,
                                         command=self.on_options_changed)
        style_checkbutton(self.stereo_cb)
        self.stereo_cb.grid(row=0, column=4, padx=(16, 0))

        self.normalize_var = tk.BooleanVar(value=False)
        norm_cb = tk.Checkbutton(opts, text="Normalize", variable=self.normalize_var)
        style_checkbutton(norm_cb)
        norm_cb.grid(row=0, column=5, padx=(10, 0))

        self.limits_label = tk.Label(self, text="", anchor="w")
        style_label(self.limits_label, fg=FG_MUTED, font=(UI_FAMILY, 8, "bold"))
        self.limits_label.pack(fill="x", padx=10, pady=(0, 4))

        self.info_label = tk.Label(self, text="", wraplength=CHOP_MIN_W - 20, justify="left")
        style_label(self.info_label, fg=ACCENT_RED)
        self.info_label.pack(fill="x", padx=10)

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        cancel_btn.pack(side="right", padx=4)
        build_btn = RoundedButton(btn_row, text="Build Multisample", command=self.on_build,
                                   bg=ACCENT_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=150)
        build_btn.pack(side="right", padx=4)
        clear_btn = RoundedButton(btn_row, text="Clear Selection", command=self.clear_selection,
                                   bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=130)
        clear_btn.pack(side="left", padx=4)

        self.refresh_list()
        self.refresh_selected_list()
        self.update_limits_label()
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _safe_grab(self, attempt=0):
        try:
            self.update_idletasks()
            self.grab_set()
        except tk.TclError:
            if attempt < 20:
                self.after(50, lambda: self._safe_grab(attempt + 1))
            return
        self.deiconify()
        self.lift()
        self.focus_force()
        self.wait_visibility()
        self.grab_set()

    # ----- Browse pane -----

    def refresh_list(self):
        for item in self.listbox.get_children():
            self.listbox.delete(item)
        self._update_path_entry()
        try:
            entries = sorted(os.listdir(self.current_dir))
        except Exception as e:
            entries = []
            print(f"Could not read folder: {e}")
        self._entries = [".."]
        self.listbox.insert("", tk.END, iid="0", text="..")
        for entry in entries:
            full = os.path.join(self.current_dir, entry)
            idx = len(self._entries)
            if os.path.isdir(full):
                self._entries.append(f"[Folder] {entry}")
                self.listbox.insert("", tk.END, iid=str(idx), text=f"[Folder] {entry}")
            elif entry.lower().endswith((".wav", ".mp3")):
                duration = get_audio_duration_seconds(full)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = None
                self._entries.append(entry)
                self.listbox.insert("", tk.END, iid=str(idx), text=entry,
                                     values=(format_duration(duration), format_size(size)))

    def _entry_at(self, index):
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None

    def on_double_click(self, event):
        sel = self.listbox.selection()
        if not sel:
            return
        entry = self._entry_at(int(sel[0]))
        if entry is None:
            return
        if entry == "..":
            self.go_up()
        elif entry.startswith("[Folder] "):
            folder_name = entry.replace("[Folder] ", "", 1)
            self.navigate_to(os.path.join(self.current_dir, folder_name))
        else:
            self.add_selected()

    def on_browse_select(self, event):
        sel = self.listbox.selection()
        if len(sel) != 1:
            return  # multi-select for batch add - don't disturb the waveform
        entry = self._entry_at(int(sel[0]))
        if not entry or entry == ".." or entry.startswith("[Folder]"):
            self.current_audio_path = None
            self.browse_source_path = None
            self.wave_canvas.delete("all")
            self.wave_name_label.config(text="No sample loaded")
            self.duration_label.config(text="")
            return
        full_path = os.path.join(self.current_dir, entry)
        self.browse_source_path = full_path
        self.load_waveform(full_path, mode="browse")
        if self.autoplay_var.get():
            self.preview_current()

    # ----- Selected pane -----

    def compute_slice_limit_seconds(self):
        rate = self.rate_var.get()
        channels = 2 if self.stereo_var.get() else 1
        num_slices = self.slices_var.get()
        limit = MAX_SECONDS.get((rate, channels))
        if not limit or num_slices <= 0:
            return None
        return limit / num_slices

    def update_limits_label(self):
        rate = self.rate_var.get()
        channels = 2 if self.stereo_var.get() else 1
        num_slices = self.slices_var.get()
        limit = MAX_SECONDS.get((rate, channels))
        if limit:
            per_slice = limit / num_slices
            ch_label = "Stereo" if channels == 2 else "Mono"
            self.limits_label.config(
                text=f"Max total: {limit:.2f}s @ {rate}Hz/{ch_label}   \u2022   "
                     f"Max per slice ({num_slices} slices): {per_slice:.2f}s"
            )
        else:
            self.limits_label.config(text="")

    def on_options_changed(self):
        self.update_limits_label()
        self.refresh_selected_list()
        if self.current_audio_path:
            self._reload_wave_channels()
            self.render_and_draw_wave()

    def refresh_selected_list(self):
        for item in self.selected_listbox.get_children():
            self.selected_listbox.delete(item)
        slice_limit = self.compute_slice_limit_seconds()
        for i, path in enumerate(self.selected_files):
            duration = get_audio_duration_seconds(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            too_long = bool(slice_limit and duration and duration > slice_limit)
            display_name = (self.selected_display_names[i] if i < len(self.selected_display_names)
                             else os.path.basename(path))
            self.selected_listbox.insert(
                "", tk.END, iid=str(i), text=display_name,
                values=(format_duration(duration), format_size(size)),
                tags=("toolong",) if too_long else ())
        self.selected_title.config(text=f"Selected Samples (Chop Order): {len(self.selected_files)}")
        self.update_stereo_lock()

    def update_stereo_lock(self):
        """Locks the Stereo checkbox once samples are selected, so you can't
        end up mixing mono- and stereo-normalized entries in one multisample
        by flipping it mid-selection. Unlocks again once the list is empty."""
        if not hasattr(self, "stereo_cb"):
            return
        if self.selected_files:
            self.stereo_cb.config(state="disabled", text="Stereo (locked)")
        else:
            self.stereo_cb.config(state="normal", text="Stereo")

    def on_selected_select(self, event):
        sel = self.selected_listbox.selection()
        if len(sel) != 1:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.selected_files):
            display_name = (self.selected_display_names[idx]
                             if idx < len(self.selected_display_names) else None)
            self.load_waveform(self.selected_files[idx], mode="selected", display_name=display_name)
            if self.autoplay_var.get():
                self.preview_current()

    def move_up(self):
        sel = sorted(int(i) for i in self.selected_listbox.selection())
        if not sel or sel[0] == 0:
            return
        for i in sel:
            self.selected_files[i - 1], self.selected_files[i] = \
                self.selected_files[i], self.selected_files[i - 1]
            self.selected_display_names[i - 1], self.selected_display_names[i] = \
                self.selected_display_names[i], self.selected_display_names[i - 1]
        self.refresh_selected_list()
        self.selected_listbox.selection_set([str(i - 1) for i in sel])
        self.selected_listbox.see(str(sel[0] - 1))

    def move_down(self):
        sel = sorted((int(i) for i in self.selected_listbox.selection()), reverse=True)
        if not sel or sel[0] == len(self.selected_files) - 1:
            return
        for i in sel:
            self.selected_files[i + 1], self.selected_files[i] = \
                self.selected_files[i], self.selected_files[i + 1]
            self.selected_display_names[i + 1], self.selected_display_names[i] = \
                self.selected_display_names[i], self.selected_display_names[i + 1]
        self.refresh_selected_list()
        self.selected_listbox.selection_set([str(i + 1) for i in sel])
        self.selected_listbox.see(str(sel[0] + 1))

    def remove_from_selection(self):
        sel = [int(i) for i in self.selected_listbox.selection()]
        if not sel:
            return
        for i in sorted(sel, reverse=True):
            if 0 <= i < len(self.selected_files):
                del self.selected_files[i]
                del self.selected_display_names[i]
        self.refresh_selected_list()

    def clear_selection(self):
        self.selected_files = []
        self.selected_display_names = []
        self.refresh_selected_list()

    def add_selected(self):
        sel = [int(i) for i in self.listbox.selection()]
        entries = []
        for i in sel:
            entry = self._entry_at(i)
            if entry and entry != ".." and not entry.startswith("[Folder]"):
                entries.append(entry)
        if not entries:
            return

        single_full_path = os.path.join(self.current_dir, entries[0]) if len(entries) == 1 else None
        region_marked = not (self.trim_start_frac <= 0.001 and self.trim_end_frac >= 0.999)

        if len(entries) == 1 and single_full_path == self.browse_source_path and region_marked \
                and self.current_audio_path:
            # Single file with a marked region -> add just that trimmed clip.
            try:
                trimmed_path = trim_wav_file(self.current_audio_path, self.trim_start_frac, self.trim_end_frac)
                if not self.stereo_var.get():
                    trimmed_path = ensure_mono_wav(trimmed_path)
                self.selected_files.append(trimmed_path)
                # trim_wav_file()/ensure_mono_wav() write to a randomly-named
                # temp file, so keep the ORIGINAL name around for display -
                # otherwise the list becomes a wall of unrecognizable
                # "mono_a1b2c3d4.wav" entries.
                self.selected_display_names.append(f"{entries[0]} (trim)")
            except Exception as e:
                dark_showerror("Trim Error", str(e), parent=self)
                return
        else:
            # Batch add: one or more full files (no trimming).
            for entry in entries:
                full_path = os.path.join(self.current_dir, entry)
                if not self.stereo_var.get():
                    full_path = ensure_mono_wav(full_path)
                self.selected_files.append(full_path)
                self.selected_display_names.append(entry)

        self.refresh_selected_list()

    # ----- Shared waveform / trim / preview -----

    def _decide_channels(self, data):
        """Decides mono vs stereo display/handling for `data` based on the
        Stereo checkbox: with Stereo off, everything is mixed down to mono
        (waveform, preview, and later what gets added to the selection)."""
        if data.ndim > 1 and data.shape[1] >= 2:
            if self.stereo_var.get():
                return data.mean(axis=1), data
            return data.mean(axis=1), None
        return (data.reshape(-1) if data.ndim > 1 else data), None

    def _reload_wave_channels(self):
        """Re-applies the mono/stereo decision for the currently loaded
        audio (e.g. after the Stereo checkbox is toggled), without touching
        trim markers/zoom."""
        if not self.current_audio_path:
            return
        try:
            data, fs = sf.read(self.current_audio_path, dtype="float32")
            self._wave_data, self._wave_data_stereo = self._decide_channels(data)
        except Exception:
            pass

    def load_waveform(self, path, mode, display_name=None):
        self.wave_mode = mode
        wav_path = path
        if path.lower().endswith(".mp3") and PYDUB_AVAILABLE:
            try:
                sound = AudioSegment.from_file(path)
                wav_path = os.path.join(os.path.expanduser("~"), ".p6_waveform_src.wav")
                sound.export(wav_path, format="wav")
            except Exception:
                self.current_audio_path = None
                self._wave_data = None
                self.wave_canvas.delete("all")
                self.wave_canvas.create_text(self.wave_width // 2, self.wave_height // 2,
                                              text="(No preview)", fill=FG_MUTED)
                return
        elif not PYDUB_AVAILABLE and path.lower().endswith(".mp3"):
            self.current_audio_path = None
            self._wave_data = None
            self.wave_canvas.delete("all")
            self.wave_canvas.create_text(self.wave_width // 2, self.wave_height // 2,
                                          text="(No preview - pydub missing)", fill=FG_MUTED)
            return

        self.current_audio_path = wav_path
        self.trim_start_frac = 0.0
        self.trim_end_frac = 1.0
        self.zoom_factor = 1.0
        self.center_frac = 0.5
        self.wave_name_label.config(text=display_name or os.path.basename(path))

        try:
            data, fs = sf.read(wav_path, dtype="float32")
            self._wave_data, self._wave_data_stereo = self._decide_channels(data)
            self._wave_fs = fs
            self.play_duration = len(data) / float(fs) if fs else 0.0
        except Exception:
            self._wave_data = None
            self._wave_data_stereo = None
            self._wave_fs = None
            self.play_duration = 0.0

        self.render_and_draw_wave()

    def _update_view_window(self):
        self.zoom_factor = max(1.0, self.zoom_factor)
        self.view_span_frac = 1.0 / self.zoom_factor
        center = self.center_frac if self.wave_mode == "browse" else 0.5
        start = center - self.view_span_frac / 2.0
        start = max(0.0, min(start, 1.0 - self.view_span_frac))
        self.view_start_frac = start

    def frac_to_x(self, frac):
        if self.view_span_frac <= 0:
            return 0
        return (frac - self.view_start_frac) / self.view_span_frac * self.wave_width

    def x_to_frac(self, x):
        return self.view_start_frac + (x / self.wave_width) * self.view_span_frac

    def _on_wave_canvas_resize(self, event=None):
        """The canvas is packed with fill='x' and can end up wider than the
        fixed wave_width used for drawing/marker/zoom math - keep them in
        sync so there's no empty gap on the right, at any window size."""
        new_width = event.width if event else self.wave_canvas.winfo_width()
        if new_width > 10 and new_width != self.wave_width:
            self.wave_width = new_width
            if self._wave_data is not None:
                self._render_wave_at_current_view()

    def render_and_draw_wave(self):
        """Used after zoom changes or a marker drag: re-centers the view on
        the markers (browse mode) or window middle (selected mode), then
        renders."""
        self._update_view_window()
        self._render_wave_at_current_view()

    def _render_wave_at_current_view(self):
        """Renders at whatever view_start_frac/view_span_frac currently are,
        without recentering - used for manual scrollbar/mousewheel panning."""
        if self.zoom_label is not None:
            self.zoom_label.config(text=f"{self.zoom_factor:.1f}x")
        self._update_scrollbar_visibility()

        self.wave_canvas.delete("all")
        if self._wave_data is None:
            self.wave_canvas.create_text(self.wave_width // 2, self.wave_height // 2,
                                          text="(No preview)", fill=FG_MUTED)
            return

        end_frac = self.view_start_frac + self.view_span_frac
        if self._wave_data_stereo is not None:
            half_h = self.wave_height / 2.0
            draw_waveform_on_canvas(self.wave_canvas, self._wave_data_stereo[:, 0],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=0, clear=True)
            draw_waveform_on_canvas(self.wave_canvas, self._wave_data_stereo[:, 1],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=half_h, clear=False)
            self.wave_canvas.create_line(0, half_h, self.wave_width, half_h,
                                          fill=BORDER_COLOR, width=1, tags="waveform")
        else:
            draw_waveform_on_canvas(self.wave_canvas, self._wave_data, self.view_start_frac,
                                     end_frac, self.wave_width, self.wave_height)
        self.redraw_markers()

    def _update_scrollbar_visibility(self):
        if self.zoom_factor > 1.0 and self._wave_data is not None:
            if not self.wave_scrollbar.winfo_ismapped():
                self.wave_scrollbar.pack(fill="x", pady=(0, 4), after=self.wave_canvas)
            first = self.view_start_frac
            last = self.view_start_frac + self.view_span_frac
            self.wave_scrollbar.set(first, last)
        else:
            if self.wave_scrollbar.winfo_ismapped():
                self.wave_scrollbar.pack_forget()

    def _pan_to(self, new_start_frac):
        new_start_frac = max(0.0, min(new_start_frac, 1.0 - self.view_span_frac))
        self.view_start_frac = new_start_frac
        self.center_frac = self.view_start_frac + self.view_span_frac / 2.0
        self._render_wave_at_current_view()

    def on_wave_scroll(self, *args):
        if not args or self.view_span_frac >= 1.0:
            return
        action = args[0]
        if action == "moveto":
            self._pan_to(float(args[1]))
        elif action == "scroll":
            amount = float(args[1])
            unit = args[2] if len(args) > 2 else "units"
            step = self.view_span_frac * (0.1 if unit == "units" else 0.9)
            self._pan_to(self.view_start_frac + amount * step)

    def on_wave_mousewheel(self, event):
        if self.view_span_frac >= 1.0:
            return  # not zoomed in - nothing to pan
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        step = self.view_span_frac * 0.1
        self._pan_to(self.view_start_frac + delta * step)

    def zoom_in(self):
        self.zoom_factor = min(self.zoom_factor * 1.6, 30.0)
        self.render_and_draw_wave()

    def zoom_out(self):
        self.zoom_factor = max(self.zoom_factor / 1.6, 1.0)
        self.render_and_draw_wave()

    def zoom_reset(self):
        self.zoom_factor = 1.0
        self.render_and_draw_wave()

    def redraw_markers(self):
        self.wave_canvas.delete("marker")
        self.wave_canvas.delete("playhead")
        self.wave_canvas.delete("truncate")
        x_start = self.frac_to_x(self.trim_start_frac)
        x_end = self.frac_to_x(self.trim_end_frac)
        slice_limit = self.compute_slice_limit_seconds()

        if self.wave_mode == "browse":
            if x_start > 0:
                self.wave_canvas.create_rectangle(0, 0, x_start, self.wave_height,
                                                   fill=BG_DARK, stipple="gray50", outline="",
                                                   tags="marker")
            if x_end < self.wave_width:
                self.wave_canvas.create_rectangle(x_end, 0, self.wave_width, self.wave_height,
                                                   fill=BG_DARK, stipple="gray50", outline="",
                                                   tags="marker")

            # Part of the *marked* region that would still get truncated at build time.
            if slice_limit and self.play_duration > 0:
                region_duration = self.play_duration * (self.trim_end_frac - self.trim_start_frac)
                if region_duration > slice_limit > 0:
                    cutoff_within_region = slice_limit / region_duration
                    cut_frac = (self.trim_start_frac + cutoff_within_region *
                                (self.trim_end_frac - self.trim_start_frac))
                    self._draw_truncate_overlay(self.frac_to_x(cut_frac), x_end)

            draw_bracket_marker(self.wave_canvas, x_start, self.wave_height, ACCENT_GREEN, "start")
            draw_bracket_marker(self.wave_canvas, x_end, self.wave_height, ACCENT_RED, "end")
        else:
            # "selected" mode: whole-file view, truncation relative to full duration.
            if slice_limit and self.play_duration > slice_limit > 0:
                cut_frac = slice_limit / self.play_duration
                self._draw_truncate_overlay(self.frac_to_x(cut_frac), self.frac_to_x(1.0))

        self.update_duration_label()

    def _draw_truncate_overlay(self, x_cut, x_right):
        """Dark background + orange tint over [x_cut, x_right], plus a dashed
        cutoff line, to show the part of the waveform that will be cut off
        because it exceeds the per-slice time limit."""
        if x_right <= x_cut:
            return
        self.wave_canvas.create_rectangle(x_cut, 0, x_right, self.wave_height,
                                           fill=BG_DARK, stipple="gray50", outline="",
                                           tags="truncate")
        self.wave_canvas.create_rectangle(x_cut, 0, x_right, self.wave_height,
                                           fill=ACCENT_ORANGE, stipple="gray25", outline="",
                                           tags="truncate")
        self.wave_canvas.create_line(x_cut, 0, x_cut, self.wave_height,
                                      fill=ACCENT_ORANGE, width=1, dash=(3, 2), tags="truncate")

    def update_duration_label(self):
        if self.play_duration <= 0:
            self.duration_label.config(text="")
            return
        slice_limit = self.compute_slice_limit_seconds()
        if self.wave_mode == "browse":
            region_duration = self.play_duration * (self.trim_end_frac - self.trim_start_frac)
            text = f"Selection: {region_duration:.2f}s"
            if slice_limit and region_duration > slice_limit:
                text += f"  (truncated to {slice_limit:.2f}s)"
                self.duration_label.config(text=text, fg=ACCENT_ORANGE)
            else:
                self.duration_label.config(text=text, fg=ACCENT_BLUE)
        else:
            rate = self.rate_var.get()
            ch_label = "Stereo" if self.stereo_var.get() else "Mono"
            text = f"Length: {self.play_duration:.2f}s   \u2022   Preview @ {rate}Hz/{ch_label}"
            if slice_limit and self.play_duration > slice_limit:
                text += f"  (truncated to {slice_limit:.2f}s)"
                self.duration_label.config(text=text, fg=ACCENT_ORANGE)
            else:
                self.duration_label.config(text=text, fg=ACCENT_BLUE)

    def on_wave_press(self, event):
        if self.wave_mode != "browse":
            self.drag_target = None
            return
        x_start = self.frac_to_x(self.trim_start_frac)
        x_end = self.frac_to_x(self.trim_end_frac)
        if abs(event.x - x_start) <= 9:
            self.drag_target = "start"
        elif abs(event.x - x_end) <= 9:
            self.drag_target = "end"
        else:
            self.drag_target = None

    def on_wave_drag(self, event):
        if not self.drag_target or self.wave_mode != "browse":
            return
        frac = max(0.0, min(self.x_to_frac(event.x), 1.0))
        if self.drag_target == "start":
            self.trim_start_frac = min(frac, self.trim_end_frac - 0.01)
        elif self.drag_target == "end":
            self.trim_end_frac = max(frac, self.trim_start_frac + 0.01)
        # Cheap: only redraw markers/overlays while dragging. The waveform
        # image itself (which may re-center when zoomed) is redrawn once the
        # mouse is released, so dragging stays smooth even at high zoom.
        self.redraw_markers()

    def on_wave_release(self, event):
        dragged = self.drag_target
        self.drag_target = None
        if dragged and self.wave_mode == "browse" and self.zoom_factor > 1.0:
            self.center_frac = self.trim_start_frac if dragged == "start" else self.trim_end_frac
            self.render_and_draw_wave()

    def update_playhead(self):
        if not self.is_playing:
            return
        elapsed = time.time() - self.play_start_time
        region_duration = self.play_duration * (self.trim_end_frac - self.trim_start_frac)
        frac_in_region = min(elapsed / region_duration, 1.0) if region_duration > 0 else 1.0
        abs_frac = self.trim_start_frac + frac_in_region * (self.trim_end_frac - self.trim_start_frac)
        x = self.frac_to_x(abs_frac)
        self.wave_canvas.delete("playhead")
        if 0 <= x <= self.wave_width:
            self.wave_canvas.create_line(x, 0, x, self.wave_height, fill=ACCENT_BLUE, width=2, tags="playhead")
        if frac_in_region < 1.0:
            self.after(30, self.update_playhead)
        else:
            self.is_playing = False
            self.wave_canvas.delete("playhead")

    def preview_current(self):
        if not self.current_audio_path:
            return
        try:
            sd.stop()

            if self.wave_mode == "selected" and PYDUB_AVAILABLE:
                # Preview at the actual target rate/channels (in-memory, no temp
                # file) so you can hear the quality it will have in the chop.
                rate = self.rate_var.get()
                channels = 2 if self.stereo_var.get() else 1
                audio = AudioSegment.from_file(self.current_audio_path)
                audio = audio.set_frame_rate(rate)
                audio = audio.set_channels(channels)
                samples = np.array(audio.get_array_of_samples()).astype(np.float32)
                max_val = float(1 << (8 * audio.sample_width - 1))
                samples /= max_val
                if channels > 1:
                    samples = samples.reshape((-1, channels))
                sd.play(samples, rate)
            else:
                data, fs = sf.read(self.current_audio_path, dtype="float32")
                if data.ndim > 1 and not self.stereo_var.get():
                    data = data.mean(axis=1)
                n = len(data)
                start_i = int(self.trim_start_frac * n)
                end_i = int(self.trim_end_frac * n)
                segment = data[start_i:end_i]
                segment = apply_micro_fade(segment, fs, fade_ms=2)
                sd.play(segment, fs)

            self.is_playing = True
            self.play_start_time = time.time()
            self.update_playhead()
        except Exception as e:
            dark_showerror("Error During Preview", str(e), parent=self)

    def stop_preview(self):
        try:
            sd.stop()
        except Exception:
            pass
        self.is_playing = False
        self.wave_canvas.delete("playhead")

    # ----- Build -----

    def on_build(self):
        if not PYDUB_AVAILABLE:
            dark_showerror("pydub missing", "The Chop feature requires pydub + ffmpeg.", parent=self)
            return
        if not self.selected_files:
            dark_showwarning("No Files", "Please add at least one sample first.", parent=self)
            return

        num_slices = self.slices_var.get()
        rate = self.rate_var.get()
        channels = 2 if self.stereo_var.get() else 1

        if len(self.selected_files) > num_slices:
            proceed = dark_askyesno(
                "Too Many Files",
                f"You selected {len(self.selected_files)} files but only {num_slices} slices "
                f"fit in one output file. Only the first {num_slices} will be used. Continue?",
                parent=self
            )
            if not proceed:
                return

        files_to_use = self.selected_files[:num_slices]

        # Building can take a noticeable moment - show a busy cursor so it
        # doesn't look like the app froze.
        try:
            self.config(cursor="watch")
            self.update_idletasks()
        except Exception:
            pass

        try:
            combined = build_chop_file(files_to_use, rate, channels, num_slices,
                                        normalize_audio=self.normalize_var.get())

            out_dir = os.path.join(os.path.expanduser("~"), ".p6_chop_tmp")
            os.makedirs(out_dir, exist_ok=True)
            unique_id = uuid.uuid4().hex[:8]
            out_name = f"chop_{num_slices}slices_{rate}Hz_{'stereo' if channels == 2 else 'mono'}_{unique_id}.wav"
            out_path = os.path.join(out_dir, out_name)
            combined.export(out_path, format="wav")
        except Exception as e:
            import traceback
            traceback.print_exc()
            dark_showerror(
                "Chop Error",
                f"An error occurred while building the chop sample:\n{e}",
                parent=self
            )
            return
        finally:
            try:
                self.config(cursor="")
            except Exception:
                pass

        self.result_path = out_path
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        padded_note = ""
        if len(files_to_use) < num_slices:
            padded_note = f" ({num_slices - len(files_to_use)} remaining slice(s) filled with silence)"
        dark_showinfo(
            "Chop Complete",
            f"Multisample created from {len(files_to_use)} file(s), "
            f"{num_slices} slices at {rate}Hz.{padded_note}",
            parent=self
        )
        self.attributes("-topmost", False)
        self.destroy()

    def on_cancel(self):
        self.stop_preview()
        self.result_path = None
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """Central place for things that used to be scattered top-bar buttons
    (Choose Folder) plus a few sensible defaults/overrides."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Settings")
        self.geometry("620x600")
        self.minsize(620, 600)
        style_toplevel(self)

        outer = tk.Frame(self, bg=BG_DARK, padx=16, pady=16)
        outer.pack(fill="both", expand=True)

        # ----- IMPORT folder -----
        import_panel = RoundedPanel(outer, title="IMPORT Folder", parent_bg=BG_DARK,
                                     panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                     title_fg=ACCENT_BLUE)
        import_panel.pack(fill="x", pady=(0, 12))
        import_row = tk.Frame(import_panel.body, bg=BG_PANEL)
        import_row.pack(fill="x")
        self.import_path_label = tk.Label(import_row, text=self.app.import_root, anchor="w",
                                           wraplength=380, justify="left")
        style_label(self.import_path_label, bg=BG_PANEL, fg=FG_TEXT, font=(UI_FAMILY, 9))
        self.import_path_label.pack(side="left", fill="x", expand=True)
        change_folder_btn = RoundedButton(import_row, text="Change...", command=self._change_import_folder,
                                           bg=ACCENT_PURPLE, fg="#FFFFFF", parent_bg=BG_PANEL, width=100)
        change_folder_btn.pack(side="right")

        # ----- Appearance -----
        appearance_panel = RoundedPanel(outer, title="Appearance", parent_bg=BG_DARK,
                                         panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                         title_fg=ACCENT_BLUE)
        appearance_panel.pack(fill="x", pady=(0, 12))

        theme_row = tk.Frame(appearance_panel.body, bg=BG_PANEL)
        theme_row.pack(fill="x")
        theme_lbl = tk.Label(theme_row, text="Theme:")
        style_label(theme_lbl, bg=BG_PANEL, font=(UI_FAMILY, 9))
        theme_lbl.pack(side="left")
        self.theme_var = tk.StringVar(value=THEME)
        theme_dd = RoundedDropdown(theme_row, self.theme_var, ["dark", "bright"],
                                    parent_bg=BG_PANEL, width=80, height=26)
        theme_dd.pack(side="left", padx=6)

        theme_hint = tk.Label(appearance_panel.body,
                               text="Requires an app restart to take effect.",
                               anchor="w")
        style_label(theme_hint, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        theme_hint.pack(fill="x", pady=(6, 0))

        # ----- Audio components -----
        comp_panel = RoundedPanel(outer, title="Audio Components (pydub / ffmpeg)", parent_bg=BG_DARK,
                                   panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                   title_fg=ACCENT_BLUE)
        comp_panel.pack(fill="x", pady=(0, 12))

        ffmpeg_row = tk.Frame(comp_panel.body, bg=BG_PANEL)
        ffmpeg_row.pack(fill="x")
        ffmpeg_lbl = tk.Label(ffmpeg_row, text="ffmpeg path:", width=12, anchor="w")
        style_label(ffmpeg_lbl, bg=BG_PANEL, font=(UI_FAMILY, 9))
        ffmpeg_lbl.pack(side="left")
        self.ffmpeg_entry = tk.Entry(ffmpeg_row, bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                                      relief="flat", highlightthickness=1, highlightbackground=BORDER_COLOR,
                                      highlightcolor=ACCENT_BLUE, font=(UI_FAMILY, 9))
        current_ffmpeg = load_ffmpeg_override() or getattr(AudioSegment, "converter", "") if PYDUB_AVAILABLE else ""
        self.ffmpeg_entry.insert(0, current_ffmpeg or "")
        self.ffmpeg_entry.pack(side="left", fill="x", expand=True, padx=6)

        ffprobe_row = tk.Frame(comp_panel.body, bg=BG_PANEL)
        ffprobe_row.pack(fill="x", pady=(6, 0))
        ffprobe_lbl = tk.Label(ffprobe_row, text="ffprobe path:", width=12, anchor="w")
        style_label(ffprobe_lbl, bg=BG_PANEL, font=(UI_FAMILY, 9))
        ffprobe_lbl.pack(side="left")
        self.ffprobe_entry = tk.Entry(ffprobe_row, bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                                       relief="flat", highlightthickness=1, highlightbackground=BORDER_COLOR,
                                       highlightcolor=ACCENT_BLUE, font=(UI_FAMILY, 9))
        current_ffprobe = load_ffprobe_override() or getattr(AudioSegment, "ffprobe", "") if PYDUB_AVAILABLE else ""
        self.ffprobe_entry.insert(0, current_ffprobe or "")
        self.ffprobe_entry.pack(side="left", fill="x", expand=True, padx=6)

        hint = tk.Label(comp_panel.body,
                         text="Leave blank for automatic detection. Only fill in if ffmpeg/ffprobe "
                              "aren't found automatically.",
                         anchor="w", justify="left", wraplength=560)
        style_label(hint, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        hint.pack(fill="x", pady=(6, 0))

        # ----- Defaults -----
        defaults_panel = RoundedPanel(outer, title="Defaults", parent_bg=BG_DARK,
                                      panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                      title_fg=ACCENT_BLUE)
        defaults_panel.pack(fill="x", pady=(0, 12))

        autoplay_row = tk.Frame(defaults_panel.body, bg=BG_PANEL)
        autoplay_row.pack(fill="x")
        self.default_autoplay_var = tk.BooleanVar(value=load_default_autoplay())
        autoplay_cb = tk.Checkbutton(autoplay_row, text="Autoplay on by default "
                                                          "(Sample selection & Chop windows)",
                                      variable=self.default_autoplay_var)
        style_checkbutton(autoplay_cb)
        autoplay_cb.config(bg=BG_PANEL, activebackground=BG_PANEL)
        autoplay_cb.pack(side="left")

        slices_row = tk.Frame(defaults_panel.body, bg=BG_PANEL)
        slices_row.pack(fill="x", pady=(8, 0))
        slices_lbl = tk.Label(slices_row, text="Default Slices (Chop):")
        style_label(slices_lbl, bg=BG_PANEL, font=(UI_FAMILY, 9))
        slices_lbl.pack(side="left")
        self.default_slices_var = tk.IntVar(value=load_default_slices())
        slices_dd = RoundedDropdown(slices_row, self.default_slices_var, SLICE_COUNTS,
                                     parent_bg=BG_PANEL, width=70, height=26)
        slices_dd.pack(side="left", padx=6)

        storage_row = tk.Frame(defaults_panel.body, bg=BG_PANEL)
        storage_row.pack(fill="x", pady=(8, 0))
        storage_lbl = tk.Label(storage_row, text="Storage Warning Threshold (MB):")
        style_label(storage_lbl, bg=BG_PANEL, font=(UI_FAMILY, 9))
        storage_lbl.pack(side="left")
        self.storage_mb_entry = tk.Entry(storage_row, width=8, bg=BG_INPUT, fg=FG_TEXT,
                                          insertbackground=FG_TEXT, relief="flat",
                                          highlightthickness=1, highlightbackground=BORDER_COLOR,
                                          highlightcolor=ACCENT_BLUE, font=(UI_FAMILY, 9), justify="center")
        self.storage_mb_entry.insert(0, str(load_storage_warning_mb()))
        self.storage_mb_entry.pack(side="left", padx=6)

        # ----- buttons -----
        btn_row = tk.Frame(outer, bg=BG_DARK)
        btn_row.pack(fill="x", pady=(4, 0))
        close_btn = RoundedButton(btn_row, text="Close", command=self.destroy,
                                   bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        close_btn.pack(side="right", padx=4)
        save_btn = RoundedButton(btn_row, text="Save", command=self._save,
                                  bg=ACCENT_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=90)
        save_btn.pack(side="right", padx=4)

        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _safe_grab(self, attempt=0):
        try:
            self.update_idletasks()
            self.grab_set()
        except tk.TclError:
            if attempt < 20:
                self.after(50, lambda: self._safe_grab(attempt + 1))
            return
        self.deiconify()
        self.lift()
        self.focus_force()
        self.wait_visibility()
        self.grab_set()

    def _change_import_folder(self):
        self.grab_release()
        self.app.choose_import_folder(parent_window=self)
        self.import_path_label.config(text=self.app.import_root)
        self.grab_set()
        self.lift()
        self.focus_force()

    def _save(self):
        global FFMPEG_AVAILABLE

        # Theme
        new_theme = self.theme_var.get()
        theme_changed = new_theme != THEME
        save_config_value("theme", new_theme)

        # Defaults
        save_config_value("default_autoplay", bool(self.default_autoplay_var.get()))
        save_config_value("default_slices", int(self.default_slices_var.get()))
        try:
            mb = float(self.storage_mb_entry.get())
            if mb <= 0:
                raise ValueError
        except ValueError:
            dark_showerror("Invalid Value", "The storage warning threshold must be a number > 0.",
                            parent=self)
            return
        save_config_value("storage_warning_mb", mb)
        apply_saved_storage_threshold()

        # ffmpeg/ffprobe overrides
        ffmpeg_path = self.ffmpeg_entry.get().strip()
        ffprobe_path = self.ffprobe_entry.get().strip()
        save_config_value("ffmpeg_path", ffmpeg_path)
        save_config_value("ffprobe_path", ffprobe_path)
        if PYDUB_AVAILABLE:
            if ffmpeg_path and os.path.exists(ffmpeg_path):
                AudioSegment.converter = ffmpeg_path
                AudioSegment.ffmpeg = ffmpeg_path
                FFMPEG_AVAILABLE = True
            if ffprobe_path and os.path.exists(ffprobe_path):
                AudioSegment.ffprobe = ffprobe_path

        if hasattr(self.app, "update_storage_display"):
            self.app.update_storage_display()

        if theme_changed:
            dark_showinfo("Saved", "Settings saved.\n\nRestart the app to apply the new theme.",
                           parent=self)
        else:
            dark_showinfo("Saved", "Settings saved.", parent=self)
        self.destroy()


class SampleSlot:
    def __init__(self, parent, pad_num, app):
        self.pad_num = pad_num
        self.app = app
        self.filepath = None
        self.target_rate = tk.IntVar(value=44100)
        self.pitch_cents = tk.IntVar(value=0)

        self.panel = RoundedPanel(parent, title=f"PAD_{pad_num}",
                                   parent_bg=parent.cget("bg"), panel_bg=BG_PANEL,
                                   border=BORDER_LIGHT, radius=14, title_fg=ACCENT_BLUE)
        self.panel.grid(row=(pad_num - 1) // 3, column=(pad_num - 1) % 3,
                         padx=6, pady=6, sticky="nsew")
        self.frame = self.panel.body

        self.label = tk.Label(self.frame, text="No sample loaded", width=30,
                               anchor="w")
        style_label(self.label, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 9))
        self.label.pack(fill="x")

        self.mini_wave_width = 240
        self.mini_wave_height = 50
        self.mini_wave_canvas = tk.Canvas(self.frame, bg=WAVE_BG,
                                           width=self.mini_wave_width, height=self.mini_wave_height,
                                           highlightthickness=0)
        self.mini_wave_canvas.pack(fill="x", pady=(2, 4))
        self.mini_wave_canvas.bind("<Configure>", self._redraw_mini_waveform_at_current_width)
        self.mini_wave_img = None
        self._mini_wave_cache = None

        rate_row = tk.Frame(self.frame, bg=BG_PANEL)
        rate_row.pack(fill="x", pady=4)
        rate_lbl = tk.Label(rate_row, text="Sample Rate:")
        style_label(rate_lbl, bg=BG_PANEL, font=(UI_FAMILY, 9))
        rate_lbl.pack(side="left")
        rate_menu = RoundedDropdown(rate_row, self.target_rate, TARGET_RATES,
                       command=self.on_rate_changed,
                       parent_bg=BG_PANEL, width=90, height=26, font=(UI_FAMILY, 9))
        rate_menu.pack(side="left", padx=4)

        self.mono_var = tk.BooleanVar(value=False)
        self.mono_cb = tk.Checkbutton(rate_row, text="Mono", variable=self.mono_var,
                                       command=self.on_mono_changed)
        style_checkbutton(self.mono_cb)
        self.mono_cb.config(bg=BG_PANEL, activebackground=BG_PANEL)
        self.mono_cb.pack(side="left", padx=(6, 0))

        pitch_row = tk.Frame(self.frame, bg=BG_PANEL)
        pitch_row.pack(fill="x", pady=(0, 4))
        pitch_lbl = tk.Label(pitch_row, text="Pitch:")
        style_label(pitch_lbl, bg=BG_PANEL, font=(UI_FAMILY, 9))
        pitch_lbl.pack(side="left")
        pitch_minus_btn = RoundedButton(pitch_row, text="\u2212", command=self.pitch_step_down,
                                         bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL,
                                         width=24, height=24, font=(UI_FAMILY, 9, "bold"))
        pitch_minus_btn.pack(side="left", padx=(4, 2))
        self.pitch_entry = tk.Entry(pitch_row, width=6, justify="center", bg=BG_INPUT, fg=FG_TEXT,
                                     insertbackground=FG_TEXT, relief="flat",
                                     highlightthickness=1, highlightbackground=BORDER_COLOR,
                                     highlightcolor=ACCENT_BLUE, font=(UI_FAMILY, 9))
        self.pitch_entry.insert(0, "0")
        self.pitch_entry.pack(side="left")
        self.pitch_entry.bind("<Return>", self.on_pitch_entry_commit)
        self.pitch_entry.bind("<FocusOut>", self.on_pitch_entry_commit)
        pitch_plus_btn = RoundedButton(pitch_row, text="+", command=self.pitch_step_up,
                                        bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL,
                                        width=24, height=24, font=(UI_FAMILY, 9, "bold"))
        pitch_plus_btn.pack(side="left", padx=(2, 4))
        cents_lbl = tk.Label(pitch_row, text="cents")
        style_label(cents_lbl, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        cents_lbl.pack(side="left")
        pitch_reset_btn = RoundedButton(pitch_row, text="Reset", command=self.pitch_reset,
                                         bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL,
                                         width=55, height=24, font=(UI_FAMILY, 8, "bold"))
        pitch_reset_btn.pack(side="left", padx=(6, 0))

        btn_row = tk.Frame(self.frame, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=4)
        load_btn = RoundedButton(btn_row, text="Load", command=self.load_sample,
                                  bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=56, height=28)
        load_btn.pack(side="left", padx=2)
        self.play_btn = RoundedButton(btn_row, text="\u25b6", command=self.play_sample,
                                       bg=ACCENT_GREEN, fg="#FFFFFF", parent_bg=BG_PANEL,
                                       width=36, height=28, state="disabled", font=(UI_FAMILY, 11, "bold"))
        self.play_btn.pack(side="left", padx=2)
        self.remove_btn = RoundedButton(btn_row, text="\u23cf", command=self.remove_sample,
                                         bg=ACCENT_RED, fg="#FFFFFF", parent_bg=BG_PANEL,
                                         width=36, height=28, state="disabled", font=(UI_FAMILY, 11, "bold"))
        self.remove_btn.pack(side="left", padx=2)
        chop_btn = RoundedButton(btn_row, text="Chop", command=self.open_chop,
                                  bg=ACCENT_ORANGE, fg="#FFFFFF", parent_bg=BG_PANEL,
                                  width=56, height=28)
        chop_btn.pack(side="left", padx=2)

    def _set_pitch(self, value):
        value = max(PITCH_MIN_CENTS, min(PITCH_MAX_CENTS, int(value)))
        self.pitch_cents.set(value)
        self.pitch_entry.delete(0, tk.END)
        self.pitch_entry.insert(0, str(value))
        self.update_warning()
        self.app.update_storage_display()
        if value != 0:
            warn_pydub_missing_once()

    def pitch_step_down(self):
        self._set_pitch(self.pitch_cents.get() - PITCH_STEP_CENTS)

    def pitch_step_up(self):
        self._set_pitch(self.pitch_cents.get() + PITCH_STEP_CENTS)

    def pitch_reset(self):
        self._set_pitch(0)

    def on_pitch_entry_commit(self, event=None):
        try:
            value = int(self.pitch_entry.get())
        except ValueError:
            value = self.pitch_cents.get()
        self._set_pitch(value)

    def set_file(self, path, from_sync=False):
        try:
            path, converted = convert_to_wav_if_needed(path)
        except Exception as e:
            print(f"Error in set_file for PAD_{self.pad_num}: {e}")
            return
        if not path.lower().endswith(".wav"):
            # Conversion failed (missing pydub/ffmpeg or a broken file). Loading
            # it anyway would end up copying a non-WAV file straight to the
            # device, which the P-6 cannot read - so refuse it here instead.
            dark_showerror(
                "Unsupported File",
                f"'{os.path.basename(path)}' could not be converted to WAV and "
                "cannot be used.\n\nMP3 support requires pydub + ffmpeg."
            )
            return
        self.filepath = path
        label_text = os.path.basename(path) + (" (converted)" if converted else "")
        if from_sync:
            label_text += " [on device]"
        self.label.config(text=label_text, fg=FG_TEXT)
        self.play_btn.config_state("normal")
        self.remove_btn.config_state("normal")

        try:
            _, detected_rate, _ = get_wav_info(path)
            closest_rate = min(TARGET_RATES, key=lambda r: abs(r - detected_rate))
            self.target_rate.set(closest_rate)
        except Exception as e:
            print(f"Could not detect sample rate for PAD_{self.pad_num}: {e}")

        self.pitch_cents.set(0)
        self.pitch_entry.delete(0, tk.END)
        self.pitch_entry.insert(0, "0")
        self.mono_var.set(False)

        self.update_warning()
        if hasattr(self.app, "update_storage_display"):
            self.app.update_storage_display()

    def update_warning(self):
        self.update_mini_waveform()
        if hasattr(self.app, "update_pad_warnings"):
            self.app.update_pad_warnings()
        if hasattr(self.app, "stop_and_refresh_waveform_for"):
            self.app.stop_and_refresh_waveform_for(self.filepath, self._current_max_seconds(),
                                                     self.pitch_cents.get())

    def get_export_ready_path(self):
        force_mono = self.effective_mono()
        return compute_export_ready_path(self.filepath, self.target_rate.get(), self.pitch_cents.get(), force_mono)

    def get_state(self):
        """Snapshot of this pad's current settings, used to remember it
        across bank switches (widgets are reused, not recreated)."""
        if not self.filepath:
            return None
        return {
            "filepath": self.filepath,
            "target_rate": self.target_rate.get(),
            "pitch_cents": self.pitch_cents.get(),
            "mono": self.mono_var.get(),
            "from_sync": "[on device]" in self.label.cget("text"),
        }

    def apply_state(self, state):
        """Restores a previously saved state (or clears the pad if None) -
        used when switching banks, instead of destroying/recreating widgets."""
        if not state or not state.get("filepath"):
            self.clear_pad()
            return
        self.set_file(state["filepath"], from_sync=state.get("from_sync", False))
        # set_file() auto-detects rate and resets pitch to 0 - restore the
        # saved values on top of that:
        rate = state.get("target_rate")
        if rate:
            self.target_rate.set(rate)
        cents = state.get("pitch_cents", 0)
        self.pitch_cents.set(cents)
        self.pitch_entry.delete(0, tk.END)
        self.pitch_entry.insert(0, str(cents))
        self.mono_var.set(state.get("mono", False))
        self.update_mono_lock()
        self.update_warning()

    def clear_pad(self):
        """Resets this pad's widgets to the empty state, without destroying
        them (they're reused across bank switches)."""
        self.filepath = None
        self.label.config(text="No sample loaded", fg=FG_MUTED)
        self.play_btn.config_state("disabled")
        self.remove_btn.config_state("disabled")
        self.mini_wave_canvas.delete("all")
        self.target_rate.set(44100)
        self.pitch_cents.set(0)
        self.pitch_entry.delete(0, tk.END)
        self.pitch_entry.insert(0, "0")
        self.mono_var.set(False)
        self.update_mono_lock()

    def load_sample(self):
        global LAST_SAMPLE_DIR
        if hasattr(self.app, "stop_playback_waveform"):
            self.app.stop_playback_waveform()
        if self.filepath and os.path.dirname(self.filepath):
            initial_dir = os.path.dirname(self.filepath)
        elif LAST_SAMPLE_DIR:
            initial_dir = LAST_SAMPLE_DIR
        else:
            initial_dir = None
        dialog = AudioPreviewDialog(self.app.root, initial_dir=initial_dir)
        self.app.root.wait_window(dialog)
        if dialog.selected_path:
            self.set_file(dialog.selected_path)
            chosen_dir = os.path.dirname(dialog.selected_path)
            LAST_SAMPLE_DIR = chosen_dir
            save_last_sample_dir(chosen_dir)

    def open_chop(self):
        global LAST_SAMPLE_DIR
        if hasattr(self.app, "stop_playback_waveform"):
            self.app.stop_playback_waveform()
        initial_dir = LAST_SAMPLE_DIR if LAST_SAMPLE_DIR else None
        dialog = ChopDialog(self.app.root, initial_dir=initial_dir)
        self.app.root.wait_window(dialog)
        if dialog.result_path:
            self.set_file(dialog.result_path)
            self.target_rate.set(dialog.rate_var.get())
            self.update_warning()
            chosen_dir = dialog.current_dir
            LAST_SAMPLE_DIR = chosen_dir
            save_last_sample_dir(chosen_dir)

    def effective_mono(self):
        """Whether this pad should be treated as mono - either because the
        global 'Force Mono (all pads)' switch is on, or because this pad's
        own Mono checkbox is checked."""
        global_force = self.app.force_mono_var.get() if hasattr(self.app, "force_mono_var") else False
        return global_force or self.mono_var.get()

    def update_mono_lock(self):
        """Greys out this pad's own Mono checkbox while the global Force
        Mono switch is on (it's already forced, so the individual choice
        doesn't matter until the global switch is off again)."""
        global_force = self.app.force_mono_var.get() if hasattr(self.app, "force_mono_var") else False
        self.mono_cb.config(state="disabled" if global_force else "normal")

    def on_mono_changed(self):
        self.update_warning()
        if hasattr(self.app, "update_storage_display"):
            self.app.update_storage_display()
        if self.mono_var.get():
            warn_pydub_missing_once()

    def on_rate_changed(self, _value=None):
        self.update_warning()
        if hasattr(self.app, "update_storage_display"):
            self.app.update_storage_display()
        try:
            _, orig_rate, _ = get_wav_info(self.filepath) if self.filepath else (None, None, None)
        except Exception:
            orig_rate = None
        if orig_rate is not None and self.target_rate.get() != orig_rate:
            warn_pydub_missing_once()

    def _current_max_seconds(self):
        """Max sample length this pad's current target rate allows (based on
        the original file's channel count, unless Force Mono overrides it),
        same rule as check_duration_warning."""
        if not self.filepath:
            return None
        try:
            _, _, channels = get_wav_info(self.filepath)
        except Exception:
            return None
        force_mono = self.effective_mono()
        ch_key = 1 if (force_mono or channels == 1) else 2
        return MAX_SECONDS.get((self.target_rate.get(), ch_key))

    def _mirror_to_main_waveform(self, samples, fs):
        if not hasattr(self.app, "show_playback_waveform"):
            return
        name = os.path.basename(self.filepath) if self.filepath else ""
        self.app.show_playback_waveform(samples, fs, name, self._current_max_seconds(),
                                         source_path=self.filepath)

    def play_sample(self):
        """Plays this pad's sample exactly as it will actually sound once
        exported: target rate, pitch, and mono all applied (if pydub is
        available)."""
        if not (self.filepath and os.path.exists(self.filepath)):
            return
        rate = self.target_rate.get()
        cents = self.pitch_cents.get()
        force_mono = self.effective_mono()

        try:
            _, orig_rate, _ = get_wav_info(self.filepath)
        except Exception:
            orig_rate = None

        needs_conversion = (orig_rate is not None and rate != orig_rate) or cents or force_mono

        try:
            sd.stop()
            if needs_conversion and PYDUB_AVAILABLE:
                audio = AudioSegment.from_file(self.filepath)
                if cents:
                    audio = apply_pitch_shift(audio, cents)
                if force_mono and audio.channels > 1:
                    audio = audio.set_channels(1)
                audio = audio.set_frame_rate(rate)
                samples = np.array(audio.get_array_of_samples()).astype(np.float32)
                max_val = float(1 << (8 * audio.sample_width - 1))
                samples /= max_val
                if audio.channels > 1:
                    samples = samples.reshape((-1, audio.channels))
                sd.play(samples, rate)
                self._mirror_to_main_waveform(samples, rate)
            else:
                # Nothing to convert (or pydub unavailable) - play as-is,
                # still respecting Force Mono (pure numpy, no pydub needed).
                data, fs = sf.read(self.filepath, dtype="float32")
                if force_mono and data.ndim > 1:
                    data = data.mean(axis=1)
                sd.play(data, fs)
                self._mirror_to_main_waveform(data, fs)
        except Exception as e:
            dark_showerror("Playback Error", str(e))

    def update_mini_waveform(self):
        """Small static waveform preview for this pad - no playhead, just a
        quick visual glance at what's loaded. Turns orange (instead of blue)
        when the sample is too long for the current rate/pitch settings.
        Shows both channels stacked when the sample is stereo (unless the
        global Force Mono switch is on)."""
        self._mini_wave_cache = None
        self.mini_wave_canvas.delete("all")
        if not self.filepath or not os.path.exists(self.filepath):
            return
        try:
            data, fs = sf.read(self.filepath, dtype="float32")
            if len(data) == 0:
                return
        except Exception:
            return
        force_mono = self.effective_mono()
        too_long = bool(check_duration_warning(self.filepath, self.target_rate.get(),
                                                self.pitch_cents.get(), force_mono))
        color = ACCENT_ORANGE if too_long else WAVE_COLOR
        self._mini_wave_cache = (data, force_mono, color)
        self._redraw_mini_waveform_at_current_width()

    def _redraw_mini_waveform_at_current_width(self, event=None):
        """Draws the cached mini-waveform data at whatever width the canvas
        currently reports. Bound to <Configure> instead of calling
        update_idletasks() (which forces an expensive synchronous X11
        round-trip on Linux) - Tk fires <Configure> naturally once the real
        layout size is known, so this just redraws cheaply from cache."""
        if not getattr(self, "_mini_wave_cache", None):
            return
        data, force_mono, color = self._mini_wave_cache
        width_px = max(self.mini_wave_canvas.winfo_width(), self.mini_wave_width)
        if data.ndim > 1 and data.shape[1] >= 2 and not force_mono:
            half_h = self.mini_wave_height / 2.0
            draw_waveform_on_canvas(self.mini_wave_canvas, data[:, 0], 0.0, 1.0,
                                     width_px, half_h, color=color,
                                     tag="waveform", y_offset=0, clear=True)
            draw_waveform_on_canvas(self.mini_wave_canvas, data[:, 1], 0.0, 1.0,
                                     width_px, half_h, color=color,
                                     tag="waveform", y_offset=half_h, clear=False)
            self.mini_wave_canvas.create_line(0, half_h, width_px, half_h,
                                               fill=BORDER_COLOR, width=1, tags="waveform")
        else:
            mono = data.mean(axis=1) if data.ndim > 1 else data
            draw_waveform_on_canvas(self.mini_wave_canvas, mono, 0.0, 1.0,
                                     width_px, self.mini_wave_height, color=color)

    def remove_sample(self):
        if self.filepath:
            bank = self.app.current_bank.get()
            pad_path = os.path.join(self.app.import_root, f"BANK_{bank}", f"PAD_{self.pad_num}")
            deleted_any = False
            if os.path.isdir(pad_path):
                try:
                    files_in_pad = [f for f in os.listdir(pad_path) if f.lower().endswith((".wav", ".mp3"))]
                except Exception as e:
                    files_in_pad = []
                    print(f"Could not read pad folder ({pad_path}): {e}")
                if files_in_pad:
                    file_list_str = ", ".join(files_in_pad)
                    msg_line1 = "The IMPORT folder for Bank " + bank + ", PAD_" + str(self.pad_num) + " already contains a file (" + file_list_str + ")."
                    msg_line2 = "Should this file also be permanently deleted?"
                    full_msg = msg_line1 + chr(10) + chr(10) + msg_line2
                    answer = dark_askyesno("Delete Sample on Device?", full_msg)
                    if answer:
                        for fname in files_in_pad:
                            full_path = os.path.join(pad_path, fname)
                            try:
                                os.remove(full_path)
                                deleted_any = True
                            except Exception as e:
                                dark_showerror("Deletion Error", f"{fname} could not be deleted: {e}")
            if deleted_any:
                dark_showinfo("Deleted", f"Sample(s) in the IMPORT folder for PAD_{self.pad_num} were removed.")

        self.clear_pad()
        if hasattr(self.app, "clear_playback_waveform"):
            self.app.clear_playback_waveform()
        if hasattr(self.app, "update_storage_display"):
            self.app.update_storage_display()
        if hasattr(self.app, "update_pad_warnings"):
            self.app.update_pad_warnings()


class P6ManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PyP6 Roland AIRA P-6 Sample Manager v2.0.3")
        self.root.configure(bg=BG_DARK)
        self.root.resizable(True, True)
        self.root.minsize(MAIN_MIN_W, MAIN_MIN_H)
        self.root.geometry(f"{MAIN_MIN_W}x{MAIN_MIN_H}")
        # Use a fast, always-safe placeholder immediately so the window
        # appears right away - the real import_root (which may involve
        # scanning /run/media, /media, etc. and can hang for several
        # seconds on Linux if a stale automount entry is present) is
        # resolved in the background and applied once ready.
        self.import_root = os.path.expanduser("~")
        self.current_bank = tk.StringVar(value=BANKS[0])
        self.slots = {b: {p: None for p in PADS} for b in BANKS}
        self._active_bank = None  # which bank's state is currently loaded into pad_widgets

        top = tk.Frame(root, padx=14, pady=14, bg=BG_DARK)
        top.pack(fill="x")
        bank_lbl = tk.Label(top, text="Bank:")
        style_label(bank_lbl, font=(UI_FAMILY, 11, "bold"))
        bank_lbl.pack(side="left")
        bank_menu = RoundedDropdown(top, self.current_bank, BANKS, command=self.switch_bank,
                                    parent_bg=BG_DARK, width=70, height=30)
        bank_menu.pack(side="left", padx=8)

        self.force_mono_var = tk.BooleanVar(value=False)
        force_mono_cb = tk.Checkbutton(top, text="Force Mono (all pads)", variable=self.force_mono_var,
                                        command=self.on_force_mono_changed)
        style_checkbutton(force_mono_cb)
        force_mono_cb.pack(side="left", padx=(4, 0))

        self.path_label = tk.Label(top, text=f"IMPORT Path: {self.import_root}")
        style_label(self.path_label, fg=FG_MUTED, font=(UI_FAMILY, 9))
        self.path_label.pack(side="left", padx=20)

        settings_btn = RoundedButton(top, text="\u2699", command=self.open_settings,
                                      bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                      width=36, height=30, font=(UI_FAMILY, 13, "bold"))
        settings_btn.pack(side="right", padx=4)

        self.pad_container = tk.Frame(root, padx=14, bg=BG_DARK)
        self.pad_container.pack(fill="x", pady=(6, 0))
        self.pad_container.grid_columnconfigure((0, 1, 2), weight=1)
        self.pad_widgets = {}
        _log_timing("  before creating 6 pad widgets")
        for pad in PADS:
            self.pad_widgets[pad] = SampleSlot(self.pad_container, pad, self)
        _log_timing("  after creating 6 pad widgets")

        storage_outer = tk.Frame(root, padx=14, bg=BG_DARK)
        storage_outer.pack(fill="x", side="top", pady=(6, 14))
        self.storage_panel = RoundedPanel(storage_outer, title="Storage (loaded samples)",
                                           parent_bg=BG_DARK, panel_bg=BG_PANEL,
                                           border=BORDER_LIGHT, radius=14, title_fg=ACCENT_BLUE)
        self.storage_panel.pack(fill="x")

        storage_row = tk.Frame(self.storage_panel.body, bg=BG_PANEL)
        storage_row.pack(fill="x")

        bank_col = tk.Frame(storage_row, bg=BG_PANEL)
        bank_col.pack(side="left", padx=(0, 40))
        bank_col_title = tk.Label(bank_col, text="Current Bank")
        style_label(bank_col_title, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8, "bold"))
        bank_col_title.pack(anchor="w")
        self.bank_size_label = tk.Label(bank_col, text="0.00 MB")
        style_label(self.bank_size_label, bg=BG_PANEL, fg=FG_TEXT, font=(UI_FAMILY, 15, "bold"))
        self.bank_size_label.pack(anchor="w")

        total_col = tk.Frame(storage_row, bg=BG_PANEL)
        total_col.pack(side="left")
        total_col_title = tk.Label(total_col, text="All Banks (total)")
        style_label(total_col_title, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8, "bold"))
        total_col_title.pack(anchor="w")
        self.total_size_label = tk.Label(total_col, text="0.00 MB")
        style_label(self.total_size_label, bg=BG_PANEL, fg=FG_TEXT, font=(UI_FAMILY, 15, "bold"))
        self.total_size_label.pack(anchor="w")

        hint_col = tk.Frame(storage_row, bg=BG_PANEL)
        hint_col.pack(side="left", padx=(30, 0), fill="both", expand=True)
        self.storage_hint_label = tk.Label(hint_col, text="", anchor="w", justify="left")
        style_label(self.storage_hint_label, bg=BG_PANEL, fg=ACCENT_RED, font=(UI_FAMILY, 9, "bold"))
        self.storage_hint_label.pack(anchor="w")

        self.pad_warnings_label = tk.Label(hint_col, text="", anchor="w", justify="left")
        style_label(self.pad_warnings_label, bg=BG_PANEL, fg=ACCENT_ORANGE, font=(UI_FAMILY, 8))
        self.pad_warnings_label.pack(anchor="w", pady=(4, 0))

        # ----- playback waveform (reacts to Play/Preview on any pad) -----
        wave_header = tk.Frame(self.storage_panel.body, bg=BG_PANEL)
        wave_header.pack(fill="x", pady=(10, 0))
        self.main_wave_name_label = tk.Label(wave_header, text="No sample playing")
        style_label(self.main_wave_name_label, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 9))
        self.main_wave_name_label.pack(side="left")
        main_wave_stop_btn = RoundedButton(wave_header, text="Stop", command=self.stop_playback_waveform,
                                            bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL,
                                            width=55, height=22, font=(UI_FAMILY, 8, "bold"))
        main_wave_stop_btn.pack(side="left", padx=(10, 0))
        self.main_wave_duration_label = tk.Label(wave_header, text="")
        style_label(self.main_wave_duration_label, bg=BG_PANEL, fg=ACCENT_BLUE, font=(UI_FAMILY, 9, "bold"))
        self.main_wave_duration_label.pack(side="right")

        self.main_wave_width = 880
        self.main_wave_height = 99
        self.main_wave_canvas = tk.Canvas(self.storage_panel.body, bg=WAVE_BG,
                                           width=self.main_wave_width, height=self.main_wave_height,
                                           highlightthickness=0)
        self.main_wave_canvas.pack(fill="x", pady=(4, 0))
        self.main_wave_canvas.bind("<Configure>", self._render_main_waveform)
        self.main_wave_img = None
        self.main_wave_data = None
        self.main_wave_data_stereo = None
        self.main_wave_fs = None
        self.main_wave_duration = 0.0
        self.main_wave_max_seconds = None
        self.main_wave_is_playing = False
        self.main_wave_play_start_time = None
        self._main_wave_play_id = 0

        bottom = tk.Frame(root, padx=14, bg=BG_DARK)
        bottom.pack(fill="x", side="top", pady=(0, 14))
        copy_bank_btn = RoundedButton(bottom, text="Copy Current Bank", command=self.export_current_bank,
                                       bg=ACCENT_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=160)
        copy_bank_btn.pack(side="left", padx=4)
        copy_all_btn = RoundedButton(bottom, text="Copy ALL Banks", command=self.export_all_banks,
                                      bg=ACCENT_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=140)
        copy_all_btn.pack(side="left", padx=4)
        sync_btn = RoundedButton(bottom, text="Reload IMPORT Folder", command=self.sync_from_device,
                                  bg=ACCENT_ORANGE, fg="#FFFFFF", parent_bg=BG_DARK, width=170)
        sync_btn.pack(side="left", padx=4)
        del_btn = RoundedButton(bottom, text="Delete Bank", command=self.delete_current_bank,
                                 bg=ACCENT_RED, fg="#FFFFFF", parent_bg=BG_DARK, width=110)
        del_btn.pack(side="left", padx=4)

        self.build_pad_slots(self.current_bank.get())
        _log_timing("build_pad_slots done, scheduling async import-root resolution")

        self._load_logo()

        # Schedule (don't call directly!) - starting the background thread
        # here, before mainloop() is running, let it call self.root.after()
        # from a non-main thread while Tcl wasn't yet pumping events. That
        # caused a multi-second stall/near-deadlock in Tcl's cross-thread
        # locking. root.after(0, ...) itself is safe pre-mainloop (it just
        # queues the call for whenever the loop starts), which guarantees
        # the thread only starts once mainloop is genuinely active.
        self.root.after(0, self._resolve_import_root_async)

    def _load_logo(self):
        """Shows the logo in the bottom-right corner, floating on top of the
        packed layout via place() - silently skipped if the file is missing."""
        try:
            logo_path = resource_path("pyp6logo.png")
            if not os.path.exists(logo_path):
                return
            full_img = tk.PhotoImage(file=logo_path)
            self._logo_img = full_img.subsample(2, 2)  # ~half size
            logo_label = tk.Label(self.root, image=self._logo_img, bg=BG_DARK,
                                   bd=0, highlightthickness=0)
            logo_label.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-10)
        except Exception as e:
            print(f"Could not load logo: {e}")

    def _resolve_import_root_async(self):
        """Finds the real import_root (saved config path, or an auto-detected
        mount) on a background thread, since this can involve filesystem
        scans that hang for several seconds on Linux if a stale automount
        entry exists under /run/media or /media. The UI stays responsive
        and shows the placeholder path until this completes.

        IMPORTANT: the worker thread must never touch Tkinter directly -
        calling self.root.after() (or anything else Tk) from a non-main
        thread is not thread-safe. It used to appear to work by luck; newer
        Python/Tk builds actively raise RuntimeError('main thread is not in
        main loop') for it. The worker only ever writes to a plain
        queue.Queue, and a poller running on the main thread (scheduled via
        root.after, called only from the main thread) picks the result up."""
        result_queue = queue.Queue()

        def worker():
            t0 = _time.perf_counter()
            resolved = load_last_import_root()
            elapsed = _time.perf_counter() - t0
            print(f"[startup]   background import-root resolution took {elapsed:.3f}s "
                  f"(this touches /run/media or /media on Linux - slow here usually "
                  f"means a stale automount entry)")
            result_queue.put(resolved)

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                resolved = result_queue.get_nowait()
            except queue.Empty:
                self.root.after(50, poll)
                return
            self._apply_resolved_import_root(resolved)

        self.root.after(50, poll)

    def _apply_resolved_import_root(self, resolved):
        self.import_root = resolved
        if hasattr(self, "path_label"):
            self.path_label.config(text=f"IMPORT Path: {self.import_root}")
        self.sync_from_device(initial=True)

    def show_playback_waveform(self, samples, fs, name, max_seconds=None, source_path=None):
        """Called by a pad's Play/Preview so the main window mirrors what's
        currently being played, including a live playhead. No zoom/trim here
        - purely a passive display."""
        if not hasattr(self, "main_wave_canvas"):
            return
        if samples is None or fs is None or len(samples) == 0:
            return
        if samples.ndim > 1 and samples.shape[1] >= 2:
            self.main_wave_data_stereo = samples
            self.main_wave_data = samples.mean(axis=1)
        else:
            self.main_wave_data_stereo = None
            self.main_wave_data = samples.reshape(-1) if samples.ndim > 1 else samples
        self.main_wave_fs = fs
        self.main_wave_duration = len(samples) / float(fs)
        self.main_wave_max_seconds = max_seconds
        self.main_wave_source_path = source_path
        self.main_wave_name_label.config(text=name)
        self._render_main_waveform()
        self._start_main_playhead()

    def stop_and_refresh_waveform_for(self, filepath, max_seconds, pitch_cents=0):
        """Called whenever a pad's rate or pitch changes: stops any ongoing
        playback (since it would now reflect stale settings) and, if the
        main window happens to be showing that same sample, refreshes both
        its duration (pitch changes duration!) and its truncation overlay
        to match the new rate/pitch immediately."""
        self.stop_playback_waveform()
        if filepath and getattr(self, "main_wave_source_path", None) == filepath \
                and hasattr(self, "main_wave_canvas"):
            try:
                orig_duration, _, _ = get_wav_info(filepath)
                if pitch_cents:
                    orig_duration = orig_duration / pitch_speed_factor(pitch_cents)
                self.main_wave_duration = orig_duration
            except Exception:
                pass
            self.main_wave_max_seconds = max_seconds
            self._redraw_main_truncate()

    def _render_main_waveform(self, event=None):
        self.main_wave_canvas.delete("all")
        # The canvas is packed with fill="x" and can be wider than
        # main_wave_width - draw at the real current width so there's no gap.
        # No forced update_idletasks() here (expensive synchronous X11
        # round-trip on Linux) - bound to <Configure> instead, which fires
        # naturally once the real size is known.
        width_px = max(self.main_wave_canvas.winfo_width(), self.main_wave_width)
        self.main_wave_render_width = width_px
        if getattr(self, "main_wave_data", None) is None:
            return
        if getattr(self, "main_wave_data_stereo", None) is not None:
            half_h = self.main_wave_height / 2.0
            draw_waveform_on_canvas(self.main_wave_canvas, self.main_wave_data_stereo[:, 0],
                                     0.0, 1.0, width_px, half_h,
                                     tag="waveform", y_offset=0, clear=True)
            draw_waveform_on_canvas(self.main_wave_canvas, self.main_wave_data_stereo[:, 1],
                                     0.0, 1.0, width_px, half_h,
                                     tag="waveform", y_offset=half_h, clear=False)
            self.main_wave_canvas.create_line(0, half_h, width_px, half_h,
                                               fill=BORDER_COLOR, width=1, tags="waveform")
        else:
            draw_waveform_on_canvas(self.main_wave_canvas, self.main_wave_data, 0.0, 1.0,
                                     width_px, self.main_wave_height)
        self._redraw_main_truncate()

    def _redraw_main_truncate(self):
        self.main_wave_canvas.delete("truncate")
        width_px = getattr(self, "main_wave_render_width", self.main_wave_width)
        duration = self.main_wave_duration
        limit = self.main_wave_max_seconds
        if limit and duration > limit > 0:
            x_cut = (limit / duration) * width_px
            self.main_wave_canvas.create_rectangle(x_cut, 0, width_px, self.main_wave_height,
                                                    fill=BG_DARK, stipple="gray50", outline="",
                                                    tags="truncate")
            self.main_wave_canvas.create_rectangle(x_cut, 0, width_px, self.main_wave_height,
                                                    fill=ACCENT_ORANGE, stipple="gray25", outline="",
                                                    tags="truncate")
            self.main_wave_canvas.create_line(x_cut, 0, x_cut, self.main_wave_height,
                                               fill=ACCENT_ORANGE, width=1, dash=(3, 2),
                                               tags="truncate")
            self.main_wave_duration_label.config(
                text=f"Length: {duration:.2f}s   (max {limit:.2f}s)", fg=ACCENT_ORANGE)
        else:
            self.main_wave_duration_label.config(text=f"Length: {duration:.2f}s", fg=ACCENT_BLUE)

    def stop_playback_waveform(self):
        try:
            sd.stop()
        except Exception:
            pass
        self.main_wave_is_playing = False
        self._main_wave_play_id += 1  # invalidate any still-scheduled playhead tick
        if hasattr(self, "main_wave_canvas"):
            self.main_wave_canvas.delete("playhead")

    def clear_playback_waveform(self):
        """Stops playback and empties the main waveform view entirely -
        used when switching banks, since the currently shown sample may not
        even belong to the newly selected bank anymore."""
        self.stop_playback_waveform()
        self.main_wave_data = None
        self.main_wave_data_stereo = None
        self.main_wave_source_path = None
        self.main_wave_duration = 0.0
        self.main_wave_max_seconds = None
        if hasattr(self, "main_wave_canvas"):
            self.main_wave_canvas.delete("all")
        if hasattr(self, "main_wave_name_label"):
            self.main_wave_name_label.config(text="No sample playing")
        if hasattr(self, "main_wave_duration_label"):
            self.main_wave_duration_label.config(text="")

    def _start_main_playhead(self):
        self._main_wave_play_id += 1
        my_id = self._main_wave_play_id
        self.main_wave_is_playing = True
        self.main_wave_play_start_time = time.time()
        self._update_main_playhead(my_id)

    def _update_main_playhead(self, play_id):
        if play_id != self._main_wave_play_id or not self.main_wave_is_playing:
            return
        elapsed = time.time() - self.main_wave_play_start_time
        duration = self.main_wave_duration
        frac = min(elapsed / duration, 1.0) if duration > 0 else 1.0
        width_px = getattr(self, "main_wave_render_width", self.main_wave_width)
        x = frac * width_px
        self.main_wave_canvas.delete("playhead")
        if frac < 1.0:
            self.main_wave_canvas.create_line(x, 0, x, self.main_wave_height,
                                               fill=ACCENT_BLUE, width=2, tags="playhead")
            self.root.after(30, lambda: self._update_main_playhead(play_id))
        else:
            self.main_wave_is_playing = False

    def open_settings(self):
        dialog = SettingsDialog(self.root, self)
        self.root.wait_window(dialog)

    def choose_import_folder(self, parent_window=None):
        parent_window = parent_window or self.root
        initial = self.import_root if os.path.isdir(self.import_root) else os.path.expanduser("~")
        picker = FolderPickerDialog(parent_window, initial_dir=initial, title="Select P-6 IMPORT Folder")
        parent_window.wait_window(picker)
        new_path = picker.selected_dir
        if new_path:
            self.import_root = new_path
            self.path_label.config(text=f"IMPORT Path: {self.import_root}")
            save_last_import_root(new_path)

    def build_pad_slots(self, bank):
        """Loads `bank`'s saved pad state into the persistent pad widgets.
        No widgets are created/destroyed here anymore (that used to happen
        on every bank switch and was the main cause of UI lag) - only their
        content is refreshed."""
        for pad in PADS:
            state = self.slots.get(bank, {}).get(pad)
            self.pad_widgets[pad].apply_state(state)
        self._active_bank = bank
        self.update_storage_display()
        self.update_pad_warnings()

    def _get_pad_state(self, bank, pad):
        """Current state for (bank, pad). For the active bank this reads
        live from the widget (so unsaved edits are always seen); for other
        banks it reads the last-saved snapshot."""
        if bank == self._active_bank:
            return self.pad_widgets[pad].get_state()
        return self.slots.get(bank, {}).get(pad)

    def _save_active_bank_state(self):
        if self._active_bank is None:
            return
        for pad in PADS:
            self.slots[self._active_bank][pad] = self.pad_widgets[pad].get_state()

    def update_pad_warnings(self):
        """Collects the 'sample too long' warnings for all pads in the
        current bank and shows them centrally, next to the storage numbers,
        instead of inside each pad (which used to make the pads grow/shrink)."""
        if not hasattr(self, "pad_warnings_label"):
            return
        bank = self.current_bank.get()
        global_force = self.force_mono_var.get()
        messages = []
        for pad in PADS:
            state = self._get_pad_state(bank, pad)
            if not state or not state.get("filepath"):
                continue
            pad_mono = global_force or state.get("mono", False)
            msg = check_duration_warning(state["filepath"], state.get("target_rate"),
                                          state.get("pitch_cents", 0), pad_mono)
            if msg:
                messages.append(f"PAD_{pad}: {msg}")
        self.pad_warnings_label.config(text="\n".join(messages))

    def on_force_mono_changed(self):
        """Global Force Mono toggled: refresh every pad's waveform/warnings,
        lock/unlock their individual Mono checkboxes, and stop playback
        (settings changed)."""
        self.stop_playback_waveform()
        for pad in PADS:
            self.pad_widgets[pad].update_mono_lock()
            self.pad_widgets[pad].update_mini_waveform()
        self.update_storage_display()
        self.update_pad_warnings()
        if self.force_mono_var.get():
            warn_pydub_missing_once()

    def _estimated_export_bytes(self, filepath, target_rate, pitch_cents=0, force_mono=False):
        if not PYDUB_AVAILABLE:
            # No conversion actually happens without pydub (compute_export_ready_path
            # just returns the original file) - reflect that instead of showing a
            # falsely optimistic post-conversion size estimate.
            try:
                return os.path.getsize(filepath)
            except OSError:
                return 0
        try:
            duration, orig_rate, channels = get_wav_info(filepath)
        except Exception:
            # Not a readable WAV (e.g. an MP3 that couldn't be converted) -
            # the export would copy it as-is, so report its real size.
            try:
                return os.path.getsize(filepath)
            except OSError:
                return 0

        # Mirror compute_export_ready_path()'s passthrough condition exactly:
        # if nothing needs converting, the ORIGINAL file gets copied verbatim -
        # including any metadata chunks (LIST/INFO, cue points, ...), which a
        # pure audio-data calculation would not account for.
        orig_sample_width = get_wav_sample_width(filepath) or 2
        needs_mono = force_mono and channels > 1
        needs_bit_depth_fix = orig_sample_width != 2
        if target_rate == orig_rate and not pitch_cents and not needs_mono and not needs_bit_depth_fix:
            try:
                return os.path.getsize(filepath)
            except OSError:
                return 0

        if pitch_cents:
            duration = duration / pitch_speed_factor(pitch_cents)
        if needs_mono:
            channels = 1
        bytes_per_sample = 2  # 16-bit PCM, as required by the P-6
        return int(duration * target_rate * channels * bytes_per_sample) + 44  # + WAV header

    def _bank_size_bytes(self, bank):
        total = 0
        global_force = self.force_mono_var.get()
        for pad in PADS:
            state = self._get_pad_state(bank, pad)
            path = state.get("filepath") if state else None
            if path and os.path.exists(path):
                target_rate = state.get("target_rate")
                pitch_cents = state.get("pitch_cents", 0)
                pad_mono = global_force or state.get("mono", False)
                if target_rate:
                    total += self._estimated_export_bytes(path, target_rate, pitch_cents, pad_mono)
                else:
                    try:
                        total += os.path.getsize(path)
                    except OSError:
                        pass
        return total

    def update_storage_display(self):
        if not hasattr(self, "bank_size_label"):
            return  # panel not built yet during early init

        bank = self.current_bank.get()
        bank_bytes = self._bank_size_bytes(bank)
        total_bytes = sum(self._bank_size_bytes(b) for b in BANKS)
        limit_mb = MAX_UPLOAD_BYTES / (1024 * 1024)

        bank_mb = bank_bytes / (1024 * 1024)
        total_mb = total_bytes / (1024 * 1024)
        bank_over = bank_bytes > MAX_UPLOAD_BYTES
        total_over = total_bytes > MAX_UPLOAD_BYTES

        self.bank_size_label.config(text=f"{bank_mb:.2f} MB", fg=ACCENT_RED if bank_over else FG_TEXT)
        self.total_size_label.config(text=f"{total_mb:.2f} MB", fg=ACCENT_RED if total_over else FG_TEXT)

        if bank_over or total_over:
            self.storage_hint_label.config(text=f"Max. total file size per upload is {limit_mb:.0f} MB")
        else:
            self.storage_hint_label.config(text="")

    def switch_bank(self, bank):
        self._save_active_bank_state()
        self.clear_playback_waveform()
        self.build_pad_slots(bank)

    def sync_from_device(self, initial=False):
        if not os.path.exists(self.import_root):
            if not initial:
                dark_showwarning("Not Found", f"{self.import_root} not reachable.")
            return
        self._save_active_bank_state()  # don't clobber unsaved live edits on pads sync won't touch
        found = 0
        for bank in BANKS:
            pad_base = os.path.join(self.import_root, f"BANK_{bank}")
            for pad in PADS:
                pad_path = os.path.join(pad_base, f"PAD_{pad}")
                if os.path.isdir(pad_path):
                    try:
                        files = [f for f in os.listdir(pad_path) if f.lower().endswith((".wav", ".mp3"))]
                    except Exception as e:
                        print(f"Error reading {pad_path}: {e}")
                        continue
                    if files:
                        full_path = os.path.join(pad_path, files[0])
                        try:
                            _, detected_rate, _ = get_wav_info(full_path)
                            closest_rate = min(TARGET_RATES, key=lambda r: abs(r - detected_rate))
                        except Exception:
                            closest_rate = 44100
                        self.slots[bank][pad] = {
                            "filepath": full_path,
                            "target_rate": closest_rate,
                            "pitch_cents": 0,
                            "from_sync": True,
                        }
                        found += 1
        self.build_pad_slots(self.current_bank.get())
        if not initial:
            if found > 0:
                dark_showinfo(
                    "Sync Complete",
                    f"{found} Sample(s) im IMPORT-Ordner gefunden.\n\n"
                    "Hinweis: Der P-6 leert diesen Ordner offenbar bei jedem Neustart. "
                    "Gefunden werden also nur Dateien, die seit dem letzten Einschalten "
                    "des Geräts in dieser Sitzung dorthin exportiert wurden - nicht "
                    "unbedingt alles, was aktuell tatsächlich auf den Pads liegt."
                )
            else:
                dark_showinfo(
                    "Sync Complete",
                    "Keine Samples im IMPORT-Ordner gefunden.\n\n"
                    "Das bedeutet nicht zwangsläufig, dass die Pads leer sind: Der P-6 "
                    "leert den IMPORT-Ordner offenbar bei jedem Neustart. Direkt nach "
                    "dem Einschalten ist dieser Ordner unabhängig vom Pad-Inhalt meist leer."
                )

    def export_bank(self, bank):
        if bank == self._active_bank:
            self._save_active_bank_state()  # make sure live edits are captured before exporting
        bank_path = os.path.join(self.import_root, f"BANK_{bank}")
        copied, skipped = 0, 0
        for pad in PADS:
            state = self._get_pad_state(bank, pad)
            pad_path = os.path.join(bank_path, f"PAD_{pad}")

            if not state or not state.get("filepath"):
                skipped += 1
                continue

            filepath = state["filepath"]
            os.makedirs(pad_path, exist_ok=True)

            try:
                pad_mono = self.force_mono_var.get() or state.get("mono", False)
                export_path = compute_export_ready_path(
                    filepath, state.get("target_rate") or 44100, state.get("pitch_cents", 0),
                    pad_mono)
            except Exception:
                export_path = filepath

            dest = os.path.join(pad_path, os.path.basename(export_path))

            try:
                for existing_file in os.listdir(pad_path):
                    existing_full = os.path.join(pad_path, existing_file)
                    if os.path.abspath(existing_full) != os.path.abspath(export_path):
                        try:
                            os.remove(existing_full)
                        except Exception as e:
                            print(f"Could not delete old file ({existing_full}): {e}")
            except Exception as e:
                print(f"Could not read pad folder ({pad_path}): {e}")

            if os.path.abspath(export_path) == os.path.abspath(dest):
                copied += 1
                continue

            try:
                shutil.copy2(export_path, dest)
                copied += 1
            except Exception as e:
                dark_showerror("Copy Error", f"PAD_{pad}: {e}")

        return copied, skipped

    def delete_current_bank(self):
        bank = self.current_bank.get()
        if hasattr(self, "clear_playback_waveform"):
            self.clear_playback_waveform()
        bank_path = os.path.join(self.import_root, f"BANK_{bank}")

        files_found = []
        if os.path.isdir(bank_path):
            for pad in PADS:
                pad_path = os.path.join(bank_path, f"PAD_{pad}")
                if os.path.isdir(pad_path):
                    try:
                        for fname in os.listdir(pad_path):
                            if fname.lower().endswith((".wav", ".mp3")):
                                files_found.append(f"PAD_{pad}/{fname}")
                    except Exception as e:
                        print(f"Could not read {pad_path}: {e}")

        has_loaded_samples = any(
            (self._get_pad_state(bank, pad) or {}).get("filepath") for pad in PADS
        )

        if not files_found and not has_loaded_samples:
            dark_showinfo("Nothing to Delete", f"Bank {bank} contains no samples.")
            return

        warning_lines = []
        warning_lines.append("Really delete Bank " + bank + " completely?")
        warning_lines.append("")
        if files_found:
            warning_lines.append("The following files will be permanently removed from the device:")
            for f in files_found:
                warning_lines.append("- " + f)
        else:
            warning_lines.append("There are currently only loaded but not yet exported samples present.")
        warning_lines.append("")
        warning_lines.append("This action cannot be undone.")
        full_warning = chr(10).join(warning_lines)

        answer = dark_askyesno("Confirm Bank Deletion", full_warning)
        if not answer:
            return

        deleted_count = 0
        errors = []
        for pad in PADS:
            pad_path = os.path.join(bank_path, f"PAD_{pad}")
            if os.path.isdir(pad_path):
                try:
                    for fname in os.listdir(pad_path):
                        if fname.lower().endswith((".wav", ".mp3")):
                            full_path = os.path.join(pad_path, fname)
                            try:
                                os.remove(full_path)
                                deleted_count += 1
                            except Exception as e:
                                errors.append(f"{fname}: {e}")
                except Exception as e:
                    errors.append(f"PAD_{pad}: {e}")

        self.slots[bank] = {p: None for p in PADS}
        self.build_pad_slots(bank)

        if errors:
            dark_showerror("Partial Errors", "Some files could not be deleted:\n" + chr(10).join(errors))
        else:
            dark_showinfo("Bank Deleted", f"Bank {bank}: {deleted_count} file(s) deleted.")

    def _set_busy(self, busy):
        """Busy cursor for operations that can take a moment (conversion on
        export), so the window doesn't just look frozen."""
        try:
            self.root.config(cursor="watch" if busy else "")
            self.root.update_idletasks()
        except Exception:
            pass

    def _confirm_if_over_limit(self, banks):
        """The storage warning is otherwise only a passive display - ask for
        confirmation at the moment it actually matters, i.e. right before
        writing to the device."""
        over = []
        for bank in banks:
            size = self._bank_size_bytes(bank)
            if size > MAX_UPLOAD_BYTES:
                over.append((bank, size / (1024 * 1024)))
        if not over:
            return True
        limit_mb = MAX_UPLOAD_BYTES / (1024 * 1024)
        details = "\n".join(f"  Bank {b}: {mb:.2f} MB" for b, mb in over)
        return dark_askyesno(
            "Storage Limit Exceeded",
            f"The following exceed the {limit_mb:.0f} MB limit:\n\n{details}\n\n"
            "Copy anyway?"
        )

    def export_current_bank(self):
        bank = self.current_bank.get()
        self._save_active_bank_state()
        if not self._confirm_if_over_limit([bank]):
            return
        self._set_busy(True)
        try:
            copied, skipped = self.export_bank(bank)
        finally:
            self._set_busy(False)
        dark_showinfo("Export Complete", f"Bank {bank}: {copied} copied, {skipped} empty.")

    def export_all_banks(self):
        total_c, total_s = 0, 0
        self._save_active_bank_state()
        if not self._confirm_if_over_limit(BANKS):
            return
        self._set_busy(True)
        try:
            for bank in BANKS:
                c, s = self.export_bank(bank)
                total_c += c
                total_s += s
        finally:
            self._set_busy(False)
        dark_showinfo("Export Complete", f"All banks: {total_c} copied, {total_s} empty.")


def _verify_ui_font(root):
    """Warns (to the console only) if the chosen UI font family isn't
    actually installed. A missing family is resolved by a substitution
    search on every distinct size/weight combination, which on X11 is slow
    enough to visibly delay window construction."""
    try:
        import tkinter.font as tkfont
        available = set(tkfont.families(root))
        if UI_FAMILY not in available:
            print(f"[startup] WARNING: UI font '{UI_FAMILY}' is not installed - "
                  f"falling back (this can noticeably slow down window drawing).")
            for candidate in ("DejaVu Sans", "Liberation Sans", "Noto Sans",
                              "FreeSans", "Helvetica", "Arial"):
                if candidate in available:
                    print(f"[startup] Suggestion: '{candidate}' is available on this system.")
                    break
    except Exception:
        pass


def check_startup_dependencies(root):
    """One clear, consolidated notice at launch instead of the user only
    finding out piecemeal via different error messages the first time each
    affected feature is touched."""
    global _pydub_warning_shown
    if not PYDUB_AVAILABLE:
        dark_showwarning(
            "pydub nicht gefunden",
            "Das Python-Paket 'pydub' wurde nicht gefunden.\n\n"
            "Dadurch funktionieren nicht:\n"
            "- Sample Rate-, Pitch- und Mono-Konvertierung beim Export\n"
            "- Das Chop-Feature (Multisample bauen)\n"
            "- MP3-Dateien (Laden, Vorhören, Längenanzeige)\n\n"
            "WAV-Dateien lassen sich weiterhin laden und unverändert exportieren.\n"
            "Installation: pip install pydub",
            parent=root
        )
        _pydub_warning_shown = True  # already told them - don't nag again per-feature
    elif not FFMPEG_AVAILABLE:
        dark_showwarning(
            "ffmpeg nicht gefunden",
            "pydub ist installiert, aber ffmpeg wurde nicht gefunden (weder im "
            "PATH noch unter /usr/bin/ffmpeg).\n\n"
            "Dadurch funktionieren nicht:\n"
            "- MP3-Dateien (Laden, Vorhören, Längenanzeige)\n"
            "- Manche interne Formatprüfungen\n\n"
            "WAV-Dateien inkl. Rate-/Pitch-/Mono-Konvertierung und Chop funktionieren "
            "in der Regel trotzdem, da diese kein ffmpeg benötigen.\n"
            "Installation z. B.: apt install ffmpeg / brew install ffmpeg",
            parent=root
        )


if __name__ == "__main__":
    _log_timing("module fully loaded (all imports + class/function defs)")
    LAST_SAMPLE_DIR = load_last_sample_dir()
    apply_saved_ffmpeg_overrides()
    apply_saved_storage_threshold()
    root = tk.Tk()
    _log_timing("tk.Tk() root window created")
    _verify_ui_font(root)
    check_startup_dependencies(root)
    _log_timing("dependency check done")
    app = P6ManagerApp(root)
    _log_timing("P6ManagerApp constructed (full UI built, incl. Reload IMPORT Folder)")

    def _count_widgets(w):
        n = 1
        for child in w.winfo_children():
            n += _count_widgets(child)
        return n

    def _count_canvases(w):
        n = 1 if isinstance(w, tk.Canvas) else 0
        for child in w.winfo_children():
            n += _count_canvases(child)
        return n

    print(f"[startup]   widget tree: {_count_widgets(root)} widgets total, "
          f"{_count_canvases(root)} of them Canvas")
    root.update_idletasks()
    _log_timing("root.update_idletasks() done (geometry/layout settled)")
    _log_perf_counters()
    root.update()
    _log_timing("root.update() done (all pending draw commands flushed to the display server)")
    root.mainloop()
