import os
import shutil
import wave
import contextlib
import json
import tkinter as tk
from tkinter import messagebox
import sounddevice as sd
import soundfile as sf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

try:
    from pydub import AudioSegment
    from pydub.silence import detect_leading_silence
    from pydub.effects import normalize as pydub_normalize
    _ffmpeg_path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    _ffprobe_path = shutil.which("ffprobe") or "/usr/bin/ffprobe"
    if os.path.exists(_ffmpeg_path):
        AudioSegment.converter = _ffmpeg_path
        AudioSegment.ffmpeg = _ffmpeg_path
    if os.path.exists(_ffprobe_path):
        AudioSegment.ffprobe = _ffprobe_path
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

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

MAX_SECONDS = {
    (44100, 1): 5.9, (44100, 2): 2.95,
    (22050, 1): 11.8, (22050, 2): 5.9,
    (14700, 1): 17.8, (14700, 2): 8.9,
    (11025, 1): 23.7, (11025, 2): 11.85,
}

BG_DARK = "#1E1E24"
BG_PANEL = "#2A2A33"
BG_INPUT = "#33333E"
FG_TEXT = "#E8E8ED"
FG_MUTED = "#9A9AA5"
ACCENT_BLUE = "#4FC3F7"
ACCENT_GREEN = "#66BB6A"
ACCENT_RED = "#EF5350"
ACCENT_ORANGE = "#FFA726"
ACCENT_PURPLE = "#AB47BC"
SELECT_GREEN = "#3E7A3E"
BORDER_COLOR = "#3D3D48"

MAIN_MIN_W, MAIN_MIN_H = 900, 620
CHOP_MIN_W, CHOP_MIN_H = 680, 580
PREVIEW_MIN_W, PREVIEW_MIN_H = 560, 480


def style_button(btn, bg, fg=FG_TEXT):
    btn.config(bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
               relief="flat", bd=0, padx=10, pady=5,
               font=("Segoe UI", 9, "bold"), cursor="hand2",
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
              highlightcolor=ACCENT_BLUE, font=("Segoe UI", 10))


def style_checkbutton(cb):
    cb.config(bg=BG_DARK, fg=FG_TEXT, selectcolor=BG_INPUT,
              activebackground=BG_DARK, activeforeground=FG_TEXT,
              relief="flat", bd=0, highlightthickness=0,
              font=("Segoe UI", 9))


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text="", command=None, bg=ACCENT_BLUE, fg="#00131A",
                 parent_bg=None, width=110, height=32, radius=10,
                 font=("Segoe UI", 9, "bold"), state="normal"):
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
        self.delete("all")
        fill = self._lighten(self.bg_color) if hover and self._state == "normal" else self.bg_color
        if self._state == "disabled":
            fill = BORDER_COLOR
        self._round_rect(1, 1, self.width - 1, self.height - 1, self.radius, fill=fill, outline=fill)
        text_fg = self.fg_color if self._state == "normal" else FG_MUTED
        self.create_text(self.width / 2, self.height / 2, text=self.text,
                          fill=text_fg, font=self.font)

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
                 width=110, height=30, radius=10, font=("Segoe UI", 9, "bold")):
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
        self.delete("all")
        fill = "#3B3B47" if hover else BG_INPUT
        self._round_rect(1, 1, self.width - 1, self.height - 1, self.radius, fill=fill, outline=fill)
        self.create_text(14, self.height / 2, text=str(self.variable.get()),
                          fill=FG_TEXT, font=self.font, anchor="w")
        self.create_text(self.width - 14, self.height / 2, text="\u25be",
                          fill=ACCENT_BLUE, font=("Segoe UI", 8), anchor="e")

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


def get_wav_info(path):
    with contextlib.closing(wave.open(path, "r")) as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        channels = wf.getnchannels()
        duration = frames / float(rate)
        return duration, rate, channels


def check_duration_warning(path, target_rate=None):
    try:
        duration, rate, channels = get_wav_info(path)
    except Exception:
        return None
    rate = target_rate or rate
    ch_key = 1 if channels == 1 else 2
    limit = MAX_SECONDS.get((rate, ch_key))
    if limit and duration > limit:
        return (f"Sample is {duration:.1f}s long, but at {rate}Hz/"
                f"{'Mono' if ch_key==1 else 'Stereo'} only {limit}s are possible.")
    return None


def render_waveform_image(path, width_px=480, height_px=80):
    try:
        data, fs = sf.read(path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if len(data) == 0:
            return None
        step = max(1, len(data) // width_px)
        data = data[::step]
        fig = plt.figure(figsize=(width_px / 100, height_px / 100), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.plot(data, color="#4FC3F7", linewidth=0.7)
        ax.fill_between(range(len(data)), data, color="#4FC3F7", alpha=0.25)
        ax.set_xlim(0, len(data))
        ax.axis("off")
        tmp_png = os.path.join(os.path.expanduser("~"), ".p6_waveform_tmp.png")
        fig.savefig(tmp_png, facecolor="#33333E")
        plt.close(fig)
        return tmp_png
    except Exception as e:
        print(f"Could not render waveform: {e}")
        return None


def convert_to_wav_if_needed(path):
    if path.lower().endswith(".wav"):
        return path, False
    if not PYDUB_AVAILABLE:
        messagebox.showerror("pydub missing", "MP3 conversion requires pydub + ffmpeg.")
        return path, False
    try:
        sound = AudioSegment.from_file(path)
        wav_path = os.path.splitext(path)[0] + "_converted.wav"
        sound.export(wav_path, format="wav")
        return wav_path, True
    except Exception as e:
        messagebox.showerror("Conversion Error", f"Details: {e}")
        return path, False


def build_chop_file(file_paths, rate, channels, num_slices, normalize_audio=False):
    if not PYDUB_AVAILABLE:
        raise RuntimeError("pydub is required for the Chop feature.")

    limit = MAX_SECONDS.get((rate, channels))
    if not limit:
        raise ValueError(f"No duration limit defined for {rate}Hz/{channels}ch.")
    slice_ms = int((limit / num_slices) * 1000)

    combined = AudioSegment.silent(duration=0, frame_rate=rate)
    if channels == 2:
        combined = combined.set_channels(2)
    else:
        combined = combined.set_channels(1)

    for path in file_paths:
        audio = AudioSegment.from_file(path)
        audio = audio.set_frame_rate(rate)
        audio = audio.set_channels(channels)

        trimmed_start = detect_leading_silence(audio)
        audio = audio[trimmed_start:]

        if len(audio) > slice_ms:
            audio = audio[:slice_ms]
        elif len(audio) < slice_ms:
            pad = AudioSegment.silent(duration=slice_ms - len(audio), frame_rate=rate)
            pad = pad.set_channels(channels)
            audio = audio + pad

        combined += audio

    if normalize_audio:
        combined = pydub_normalize(combined)

    return combined


class FolderPickerDialog(tk.Toplevel):
    """Dark-themed replacement for filedialog.askdirectory(), since native OS
    folder dialogs cannot be restyled through Tkinter."""

    def __init__(self, parent, initial_dir=None, title="Select Folder"):
        super().__init__(parent)
        self.title(title)
        self.geometry(f"{PREVIEW_MIN_W}x{PREVIEW_MIN_H}")
        self.minsize(PREVIEW_MIN_W, PREVIEW_MIN_H)
        style_toplevel(self)
        self.selected_dir = None
        self.current_dir = initial_dir if initial_dir and os.path.isdir(initial_dir) else os.path.expanduser("~")

        top = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        top.pack(fill="x")
        up_btn = RoundedButton(top, text="\u2191 Up", command=self.go_up,
                                bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=60, height=28)
        up_btn.pack(side="left", padx=(0, 6))
        self.path_entry = tk.Entry(top, bg=BG_INPUT, fg=FG_TEXT,
                                    insertbackground=FG_TEXT, relief="flat",
                                    highlightthickness=1, highlightbackground=BORDER_COLOR,
                                    highlightcolor=ACCENT_BLUE, font=("Segoe UI", 9))
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.path_entry.bind("<Return>", self.go_to_typed_path)
        go_btn = RoundedButton(top, text="Go", command=self.go_to_typed_path,
                                bg=ACCENT_BLUE, fg="#00131A", parent_bg=BG_DARK, width=50, height=28)
        go_btn.pack(side="left", padx=(6, 0))

        quick_row = tk.Frame(self, padx=10, bg=BG_DARK)
        quick_row.pack(fill="x", pady=(4, 0))
        quick_lbl = tk.Label(quick_row, text="Quick access:")
        style_label(quick_lbl, fg=FG_MUTED, font=("Segoe UI", 8))
        quick_lbl.pack(side="left", padx=(0, 6))
        for label, path in self._quick_access_locations():
            qb = RoundedButton(quick_row, text=label, command=lambda p=path: self.navigate_to(p),
                                bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=80, height=24,
                                font=("Segoe UI", 8, "bold"))
            qb.pack(side="left", padx=2)

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
        style_label(hint, fg=FG_MUTED, font=("Segoe UI", 8))
        hint.pack(fill="x", padx=10)

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        cancel_btn.pack(side="right", padx=4)
        select_btn = RoundedButton(btn_row, text="Diesen Ordner wählen", command=self.on_confirm,
                                    bg=ACCENT_GREEN, fg="#0A1F0A", parent_bg=BG_DARK, width=170)
        select_btn.pack(side="right", padx=4)

        self.refresh_list()
        self.transient(parent)
        self._safe_grab()

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
            messagebox.showwarning("Nicht gefunden", f"Ordner existiert nicht:\n{path}", parent=self)

    def go_up(self):
        parent_dir = os.path.dirname(self.current_dir.rstrip(os.sep)) or os.sep
        self.navigate_to(parent_dir)

    def go_to_typed_path(self, event=None):
        typed = self.path_entry.get().strip()
        if not typed:
            return
        typed = os.path.expanduser(typed)
        self.navigate_to(typed)

    def _safe_grab(self, attempt=0):
        try:
            self.update_idletasks()
            self.grab_set()
        except tk.TclError:
            if attempt < 20:
                self.after(50, lambda: self._safe_grab(attempt + 1))
            return
        self.deiconify()
        self.wait_visibility()
        self.grab_set()

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        self.path_entry.delete(0, tk.END)
        self.path_entry.insert(0, self.current_dir)
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


class AudioPreviewDialog(tk.Toplevel):
    def __init__(self, parent, initial_dir=None):
        super().__init__(parent)
        self.title("Select Sample (with Preview)")
        self.geometry(f"{PREVIEW_MIN_W}x{PREVIEW_MIN_H}")
        self.minsize(PREVIEW_MIN_W, PREVIEW_MIN_H)
        style_toplevel(self)
        self.selected_path = None
        self.current_dir = initial_dir or os.path.expanduser("~")
        self.autoplay_var = tk.BooleanVar(value=False)

        top = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        top.pack(fill="x")
        self.path_label = tk.Label(top, text=self.current_dir, anchor="w")
        style_label(self.path_label, fg=FG_MUTED, font=("Segoe UI", 9))
        self.path_label.pack(side="left", fill="x", expand=True)
        folder_btn = RoundedButton(top, text="Change Folder", command=self.choose_folder,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=130)
        folder_btn.pack(side="right")

        list_frame = tk.Frame(self, padx=10, pady=6, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        style_listbox(self.listbox)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<Double-Button-1>", self.on_confirm)

        autoplay_row = tk.Frame(self, padx=10, bg=BG_DARK)
        autoplay_row.pack(fill="x")
        autoplay_cb = tk.Checkbutton(autoplay_row, text="Autoplay (play sound on click)",
                       variable=self.autoplay_var)
        style_checkbutton(autoplay_cb)
        autoplay_cb.pack(side="left")

        self.waveform_label = tk.Label(self, bg=BG_INPUT)
        self.waveform_label.pack(fill="x", padx=10, pady=8)
        self.waveform_img = None

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        preview_btn = RoundedButton(btn_row, text="Preview", command=self.preview_selected,
                                     bg=ACCENT_BLUE, fg="#00131A", parent_bg=BG_DARK)
        preview_btn.pack(side="left", padx=4)
        stop_btn = RoundedButton(btn_row, text="Stop", command=self.stop_preview,
                                  bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        stop_btn.pack(side="left", padx=4)
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        cancel_btn.pack(side="right", padx=4)
        select_btn = RoundedButton(btn_row, text="Select", command=self.on_confirm,
                                    bg=ACCENT_GREEN, fg="#0A1F0A", parent_bg=BG_DARK)
        select_btn.pack(side="right", padx=4)

        self.refresh_list()
        self.transient(parent)
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
        self.wait_visibility()
        self.grab_set()

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        self.path_label.config(text=self.current_dir)
        try:
            entries = sorted(os.listdir(self.current_dir))
        except Exception as e:
            entries = []
            print(f"Could not read folder: {e}")
        self.listbox.insert(tk.END, "..")
        for entry in entries:
            full = os.path.join(self.current_dir, entry)
            if os.path.isdir(full):
                self.listbox.insert(tk.END, f"[Folder] {entry}")
            elif entry.lower().endswith((".wav", ".mp3")):
                self.listbox.insert(tk.END, entry)

    def get_selected_entry(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self.listbox.get(sel[0])

    def on_select(self, event):
        entry = self.get_selected_entry()
        if entry and not entry.startswith("[Folder]") and entry != "..":
            self.selected_path = os.path.join(self.current_dir, entry)
            self.show_waveform(self.selected_path)
            if self.autoplay_var.get():
                self.preview_selected()
        else:
            self.selected_path = None
            self.waveform_label.config(image="", text="")

    def show_waveform(self, path):
        wav_path = path
        if path.lower().endswith(".mp3") and PYDUB_AVAILABLE:
            try:
                sound = AudioSegment.from_file(path)
                wav_path = os.path.join(os.path.expanduser("~"), ".p6_waveform_src.wav")
                sound.export(wav_path, format="wav")
            except Exception:
                self.waveform_label.config(image="", text="(No preview)", fg=FG_MUTED)
                return
        png_path = render_waveform_image(wav_path)
        if png_path:
            try:
                self.waveform_img = tk.PhotoImage(file=png_path)
                self.waveform_label.config(image=self.waveform_img, text="")
            except Exception:
                self.waveform_label.config(image="", text="(No preview)", fg=FG_MUTED)
        else:
            self.waveform_label.config(image="", text="(No preview)", fg=FG_MUTED)

    def on_confirm(self, event=None):
        entry = self.get_selected_entry()
        if entry is None:
            return
        if entry == "..":
            self.current_dir = os.path.dirname(self.current_dir)
            self.refresh_list()
            return
        if entry.startswith("[Folder] "):
            folder_name = entry.replace("[Folder] ", "", 1)
            self.current_dir = os.path.join(self.current_dir, folder_name)
            self.refresh_list()
            return
        self.selected_path = os.path.join(self.current_dir, entry)
        self.stop_preview()
        self.destroy()

    def choose_folder(self):
        self.grab_release()
        picker = FolderPickerDialog(self, initial_dir=self.current_dir)
        self.wait_window(picker)
        self.grab_set()
        self.lift()
        self.focus_force()
        if picker.selected_dir:
            self.current_dir = picker.selected_dir
            self.refresh_list()

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
                    messagebox.showerror("pydub missing", "Previewing MP3 requires pydub + ffmpeg.")
                    return
                sound = AudioSegment.from_file(path)
                tmp_preview = os.path.join(os.path.expanduser("~"), ".p6_preview_tmp.wav")
                sound.export(tmp_preview, format="wav")
                play_path = tmp_preview
            data, fs = sf.read(play_path, dtype="float32")
            sd.play(data, fs)
        except Exception as e:
            messagebox.showerror("Error During Preview", str(e))

    def stop_preview(self):
        try:
            sd.stop()
        except Exception:
            pass

    def on_cancel(self):
        self.stop_preview()
        self.selected_path = None
        self.destroy()


class ChopDialog(tk.Toplevel):
    def __init__(self, parent, initial_dir=None):
        super().__init__(parent)
        self.title("Chop - Build Multisample")
        self.geometry(f"{CHOP_MIN_W}x{CHOP_MIN_H}")
        self.minsize(CHOP_MIN_W, CHOP_MIN_H)
        style_toplevel(self)
        self.result_path = None
        self.current_dir = initial_dir or os.path.expanduser("~")
        self.selected_files = []

        top = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        top.pack(fill="x")
        self.path_label = tk.Label(top, text=self.current_dir, anchor="w")
        style_label(self.path_label, fg=FG_MUTED, font=("Segoe UI", 9))
        self.path_label.pack(side="left", fill="x", expand=True)
        folder_btn = RoundedButton(top, text="Change Folder", command=self.choose_folder,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=130)
        folder_btn.pack(side="right")

        list_frame = tk.Frame(self, padx=10, pady=6, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED,
                                   yscrollcommand=scrollbar.set)
        style_listbox(self.listbox)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self.on_double_click)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        self.autoplay_var = tk.BooleanVar(value=False)
        preview_row = tk.Frame(self, padx=10, bg=BG_DARK)
        preview_row.pack(fill="x")
        autoplay_cb = tk.Checkbutton(preview_row, text="Autoplay (play sound on click)",
                       variable=self.autoplay_var)
        style_checkbutton(autoplay_cb)
        autoplay_cb.pack(side="left")
        preview_btn = RoundedButton(preview_row, text="Preview", command=self.preview_selected,
                                     bg=ACCENT_BLUE, fg="#00131A", parent_bg=BG_DARK)
        preview_btn.pack(side="left", padx=6)
        stop_btn = RoundedButton(preview_row, text="Stop", command=self.stop_preview,
                                  bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        stop_btn.pack(side="left", padx=4)

        self.count_label = tk.Label(self, text="0 files selected")
        style_label(self.count_label, fg=ACCENT_BLUE, font=("Segoe UI", 9, "bold"))
        self.count_label.pack(fill="x", padx=10, pady=(6, 0))

        opts = tk.Frame(self, padx=10, pady=8, bg=BG_DARK)
        opts.pack(fill="x")

        lbl1 = tk.Label(opts, text="Slices:")
        style_label(lbl1)
        lbl1.grid(row=0, column=0, sticky="w")
        self.slices_var = tk.IntVar(value=32)
        om1 = RoundedDropdown(opts, self.slices_var, SLICE_COUNTS, parent_bg=BG_DARK, width=70)
        om1.grid(row=0, column=1, padx=6)

        lbl2 = tk.Label(opts, text="Sample Rate:")
        style_label(lbl2)
        lbl2.grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.rate_var = tk.IntVar(value=44100)
        om2 = RoundedDropdown(opts, self.rate_var, TARGET_RATES, parent_bg=BG_DARK, width=90)
        om2.grid(row=0, column=3, padx=6)

        self.stereo_var = tk.BooleanVar(value=False)
        stereo_cb = tk.Checkbutton(opts, text="Stereo", variable=self.stereo_var)
        style_checkbutton(stereo_cb)
        stereo_cb.grid(row=0, column=4, padx=(16, 0))

        self.normalize_var = tk.BooleanVar(value=False)
        norm_cb = tk.Checkbutton(opts, text="Normalize", variable=self.normalize_var)
        style_checkbutton(norm_cb)
        norm_cb.grid(row=0, column=5, padx=(10, 0))

        self.info_label = tk.Label(self, text="", wraplength=580, justify="left")
        style_label(self.info_label, fg=ACCENT_RED)
        self.info_label.pack(fill="x", padx=10)

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        cancel_btn.pack(side="right", padx=4)
        build_btn = RoundedButton(btn_row, text="Build Multisample", command=self.on_build,
                                   bg=ACCENT_GREEN, fg="#0A1F0A", parent_bg=BG_DARK, width=150)
        build_btn.pack(side="right", padx=4)
        add_btn = RoundedButton(btn_row, text="Add Selected", command=self.add_selected,
                                 bg=ACCENT_BLUE, fg="#00131A", parent_bg=BG_DARK, width=120)
        add_btn.pack(side="left", padx=4)
        remove_btn = RoundedButton(btn_row, text="Remove Selected", command=self.remove_selected,
                                    bg=ACCENT_ORANGE, fg="#2A1600", parent_bg=BG_DARK, width=140)
        remove_btn.pack(side="left", padx=4)
        clear_btn = RoundedButton(btn_row, text="Clear Selection", command=self.clear_selection,
                                   bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=130)
        clear_btn.pack(side="left", padx=4)

        self.refresh_list()
        self.transient(parent)
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
        self.wait_visibility()
        self.grab_set()

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        self.path_label.config(text=self.current_dir)
        try:
            entries = sorted(os.listdir(self.current_dir))
        except Exception as e:
            entries = []
            print(f"Could not read folder: {e}")
        self.listbox.insert(tk.END, "..")
        for entry in entries:
            full = os.path.join(self.current_dir, entry)
            if os.path.isdir(full):
                self.listbox.insert(tk.END, f"[Folder] {entry}")
            elif entry.lower().endswith((".wav", ".mp3")):
                self.listbox.insert(tk.END, entry)
        self.highlight_selected()

    def highlight_selected(self):
        for i in range(self.listbox.size()):
            entry = self.listbox.get(i)
            if entry == ".." or entry.startswith("[Folder]"):
                continue
            full_path = os.path.join(self.current_dir, entry)
            if full_path in self.selected_files:
                self.listbox.itemconfig(i, bg=SELECT_GREEN, fg="#FFFFFF")
            else:
                self.listbox.itemconfig(i, bg=BG_INPUT, fg=FG_TEXT)

    def on_double_click(self, event):
        sel = self.listbox.curselection()
        if not sel:
            return
        entry = self.listbox.get(sel[0])
        if entry == "..":
            self.current_dir = os.path.dirname(self.current_dir)
            self.refresh_list()
        elif entry.startswith("[Folder] "):
            folder_name = entry.replace("[Folder] ", "", 1)
            self.current_dir = os.path.join(self.current_dir, folder_name)
            self.refresh_list()
        else:
            self.toggle_selected()

    def on_select(self, event):
        if self.autoplay_var.get():
            self.preview_selected()

    def preview_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        entry = self.listbox.get(sel[0])
        if not entry or entry == ".." or entry.startswith("[Folder]"):
            return
        path = os.path.join(self.current_dir, entry)
        try:
            sd.stop()
            play_path = path
            if path.lower().endswith(".mp3"):
                if not PYDUB_AVAILABLE:
                    messagebox.showerror("pydub missing", "Previewing MP3 requires pydub + ffmpeg.")
                    return
                sound = AudioSegment.from_file(path)
                tmp_preview = os.path.join(os.path.expanduser("~"), ".p6_preview_tmp.wav")
                sound.export(tmp_preview, format="wav")
                play_path = tmp_preview
            data, fs = sf.read(play_path, dtype="float32")
            sd.play(data, fs)
        except Exception as e:
            messagebox.showerror("Error During Preview", str(e))

    def stop_preview(self):
        try:
            sd.stop()
        except Exception:
            pass

    def choose_folder(self):
        self.grab_release()
        picker = FolderPickerDialog(self, initial_dir=self.current_dir)
        self.wait_window(picker)
        self.grab_set()
        self.lift()
        self.focus_force()
        if picker.selected_dir:
            self.current_dir = picker.selected_dir
            self.refresh_list()

    def add_selected(self):
        sel = self.listbox.curselection()
        for i in sel:
            entry = self.listbox.get(i)
            if entry == ".." or entry.startswith("[Folder]"):
                continue
            full_path = os.path.join(self.current_dir, entry)
            if full_path not in self.selected_files:
                self.selected_files.append(full_path)
        self.count_label.config(text=f"{len(self.selected_files)} files selected")
        self.highlight_selected()

    def toggle_selected(self):
        sel = self.listbox.curselection()
        for i in sel:
            entry = self.listbox.get(i)
            if entry == ".." or entry.startswith("[Folder]"):
                continue
            full_path = os.path.join(self.current_dir, entry)
            if full_path in self.selected_files:
                self.selected_files.remove(full_path)
            else:
                self.selected_files.append(full_path)
        self.count_label.config(text=f"{len(self.selected_files)} files selected")
        self.highlight_selected()

    def remove_selected(self):
        sel = self.listbox.curselection()
        for i in sel:
            entry = self.listbox.get(i)
            if entry == ".." or entry.startswith("[Folder]"):
                continue
            full_path = os.path.join(self.current_dir, entry)
            if full_path in self.selected_files:
                self.selected_files.remove(full_path)
        self.count_label.config(text=f"{len(self.selected_files)} files selected")
        self.highlight_selected()

    def clear_selection(self):
        self.selected_files = []
        self.count_label.config(text="0 files selected")
        self.highlight_selected()

    def on_build(self):
        if not PYDUB_AVAILABLE:
            messagebox.showerror("pydub missing", "The Chop feature requires pydub + ffmpeg.", parent=self)
            return
        if not self.selected_files:
            messagebox.showwarning("No Files", "Please add at least one sample first.", parent=self)
            return

        num_slices = self.slices_var.get()
        rate = self.rate_var.get()
        channels = 2 if self.stereo_var.get() else 1

        if len(self.selected_files) > num_slices:
            proceed = messagebox.askyesno(
                "Too Many Files",
                f"You selected {len(self.selected_files)} files but only {num_slices} slices "
                f"fit in one output file. Only the first {num_slices} will be used. Continue?",
                parent=self
            )
            if not proceed:
                return

        files_to_use = self.selected_files[:num_slices]

        try:
            combined = build_chop_file(files_to_use, rate, channels, num_slices,
                                        normalize_audio=self.normalize_var.get())

            out_dir = os.path.join(os.path.expanduser("~"), ".p6_chop_tmp")
            os.makedirs(out_dir, exist_ok=True)
            import uuid
            unique_id = uuid.uuid4().hex[:8]
            out_name = f"chop_{num_slices}slices_{rate}Hz_{'stereo' if channels == 2 else 'mono'}_{unique_id}.wav"
            out_path = os.path.join(out_dir, out_name)
            combined.export(out_path, format="wav")
        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror(
                "Chop Error",
                f"Beim Erstellen des Chop-Samples ist ein Fehler aufgetreten:\n{e}",
                parent=self
            )
            return

        self.result_path = out_path
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        messagebox.showinfo(
            "Chop Complete",
            f"Multisample created from {len(files_to_use)} file(s), "
            f"{num_slices} slices at {rate}Hz.",
            parent=self
        )
        self.attributes("-topmost", False)
        self.destroy()

    def on_cancel(self):
        self.result_path = None
        self.destroy()


class SampleSlot:
    def __init__(self, parent, pad_num, app):
        self.pad_num = pad_num
        self.app = app
        self.filepath = None
        self.target_rate = tk.IntVar(value=44100)

        self.frame = tk.LabelFrame(parent, text=f"PAD_{pad_num}", padx=10, pady=10,
                                    bg=BG_PANEL, fg=ACCENT_BLUE,
                                    font=("Segoe UI", 10, "bold"),
                                    highlightbackground=BORDER_COLOR,
                                    highlightthickness=1, bd=0)
        self.frame.grid(row=(pad_num - 1) // 3, column=(pad_num - 1) % 3,
                         padx=6, pady=6, sticky="nsew")

        self.label = tk.Label(self.frame, text="No sample loaded", width=30,
                               anchor="w")
        style_label(self.label, bg=BG_PANEL, fg=FG_MUTED, font=("Segoe UI", 9))
        self.label.pack(fill="x")

        rate_row = tk.Frame(self.frame, bg=BG_PANEL)
        rate_row.pack(fill="x", pady=4)
        rate_lbl = tk.Label(rate_row, text="Sample Rate:")
        style_label(rate_lbl, bg=BG_PANEL, font=("Segoe UI", 9))
        rate_lbl.pack(side="left")
        rate_menu = RoundedDropdown(rate_row, self.target_rate, TARGET_RATES,
                      command=lambda _: self.update_warning(),
                      parent_bg=BG_PANEL, width=90, height=26, font=("Segoe UI", 9))
        rate_menu.pack(side="left", padx=4)

        self.warn_label = tk.Label(self.frame, text="", wraplength=220,
                                    justify="left", anchor="w")
        style_label(self.warn_label, bg=BG_PANEL, fg=ACCENT_RED, font=("Segoe UI", 8))
        self.warn_label.pack(fill="x")

        btn_row = tk.Frame(self.frame, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=4)
        load_btn = RoundedButton(btn_row, text="Load...", command=self.load_sample,
                                  bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=80, height=28)
        load_btn.pack(side="left", padx=2)
        self.play_btn = RoundedButton(btn_row, text="Play", command=self.play_sample,
                                       bg=ACCENT_GREEN, fg="#0A1F0A", parent_bg=BG_PANEL,
                                       width=70, height=28, state="disabled")
        self.play_btn.pack(side="left", padx=2)
        self.remove_btn = RoundedButton(btn_row, text="Remove", command=self.remove_sample,
                                         bg=ACCENT_RED, fg="#2A0A0A", parent_bg=BG_PANEL,
                                         width=80, height=28, state="disabled")
        self.remove_btn.pack(side="left", padx=2)

        btn_row2 = tk.Frame(self.frame, bg=BG_PANEL)
        btn_row2.pack(fill="x", pady=2)
        chop_btn = RoundedButton(btn_row2, text="Chop...", command=self.open_chop,
                                  bg=ACCENT_ORANGE, fg="#2A1600", parent_bg=BG_PANEL,
                                  width=80, height=28)
        chop_btn.pack(side="left", padx=2)

    def set_file(self, path, from_sync=False):
        try:
            path, converted = convert_to_wav_if_needed(path)
        except Exception as e:
            print(f"Error in set_file for PAD_{self.pad_num}: {e}")
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

        self.update_warning()

    def update_warning(self):
        if not self.filepath:
            self.warn_label.config(text="")
            return
        msg = check_duration_warning(self.filepath, self.target_rate.get())
        self.warn_label.config(text=msg if msg else "")

    def get_export_ready_path(self):
        if not self.filepath or not PYDUB_AVAILABLE:
            return self.filepath
        try:
            _, orig_rate, _ = get_wav_info(self.filepath)
        except Exception:
            return self.filepath
        rate = self.target_rate.get()
        if rate == orig_rate:
            return self.filepath
        audio = AudioSegment.from_wav(self.filepath)
        audio = audio.set_frame_rate(rate)
        temp_path = os.path.splitext(self.filepath)[0] + f"_{rate}Hz.wav"
        audio.export(temp_path, format="wav")
        return temp_path

    def load_sample(self):
        global LAST_SAMPLE_DIR
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

    def play_sample(self):
        if self.filepath and os.path.exists(self.filepath):
            try:
                sd.stop()
                data, fs = sf.read(self.filepath, dtype="float32")
                sd.play(data, fs)
            except Exception as e:
                messagebox.showerror("Playback Error", str(e))

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
                    answer = messagebox.askyesno("Delete Sample on Device?", full_msg)
                    if answer:
                        for fname in files_in_pad:
                            full_path = os.path.join(pad_path, fname)
                            try:
                                os.remove(full_path)
                                deleted_any = True
                            except Exception as e:
                                messagebox.showerror("Deletion Error", f"{fname} could not be deleted: {e}")
            if deleted_any:
                messagebox.showinfo("Deleted", f"Sample(s) in the IMPORT folder for PAD_{self.pad_num} were removed.")

        self.filepath = None
        self.label.config(text="No sample loaded", fg=FG_MUTED)
        self.warn_label.config(text="")
        self.play_btn.config_state("disabled")
        self.remove_btn.config_state("disabled")


class P6ManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Roland AIRA P-6 Sample Manager v1.3.0")
        self.root.configure(bg=BG_DARK)
        self.root.minsize(MAIN_MIN_W, MAIN_MIN_H)
        self.root.geometry(f"{MAIN_MIN_W}x{MAIN_MIN_H}")
        self.import_root = load_last_import_root()
        self.current_bank = tk.StringVar(value=BANKS[0])
        self.slots = {b: {} for b in BANKS}

        top = tk.Frame(root, padx=14, pady=14, bg=BG_DARK)
        top.pack(fill="x")
        bank_lbl = tk.Label(top, text="Bank:")
        style_label(bank_lbl, font=("Segoe UI", 11, "bold"))
        bank_lbl.pack(side="left")
        bank_menu = RoundedDropdown(top, self.current_bank, BANKS, command=self.switch_bank,
                                    parent_bg=BG_DARK, width=70, height=30)
        bank_menu.pack(side="left", padx=8)

        self.path_label = tk.Label(top, text=f"IMPORT Path: {self.import_root}")
        style_label(self.path_label, fg=FG_MUTED, font=("Segoe UI", 9))
        self.path_label.pack(side="left", padx=20)

        del_btn = RoundedButton(top, text="Delete Bank", command=self.delete_current_bank,
                                 bg=ACCENT_RED, fg="#2A0A0A", parent_bg=BG_DARK, width=110)
        del_btn.pack(side="right", padx=4)
        sync_btn = RoundedButton(top, text="Sync with Device", command=self.sync_from_device,
                                  bg=ACCENT_ORANGE, fg="#2A1600", parent_bg=BG_DARK, width=140)
        sync_btn.pack(side="right", padx=4)
        folder_btn = RoundedButton(top, text="Choose Folder", command=self.choose_import_folder,
                                    bg=ACCENT_PURPLE, fg="#1A0A1F", parent_bg=BG_DARK, width=130)
        folder_btn.pack(side="right", padx=4)

        self.pad_container = tk.Frame(root, padx=14, bg=BG_DARK)
        self.pad_container.pack(fill="x", pady=(6, 0))

        bottom = tk.Frame(root, padx=14, bg=BG_DARK)
        bottom.pack(fill="x", side="top", pady=(10, 14))
        copy_bank_btn = RoundedButton(bottom, text="Copy Current Bank", command=self.export_current_bank,
                                       bg=ACCENT_GREEN, fg="#0A1F0A", parent_bg=BG_DARK, width=160)
        copy_bank_btn.pack(side="left", padx=4)
        copy_all_btn = RoundedButton(bottom, text="Copy ALL Banks", command=self.export_all_banks,
                                      bg=ACCENT_BLUE, fg="#00131A", parent_bg=BG_DARK, width=140)
        copy_all_btn.pack(side="left", padx=4)

        self.build_pad_slots(self.current_bank.get())
        self.sync_from_device(initial=True)

    def choose_import_folder(self):
        initial = self.import_root if os.path.isdir(self.import_root) else os.path.expanduser("~")
        picker = FolderPickerDialog(self.root, initial_dir=initial, title="Select P-6 IMPORT Folder")
        self.root.wait_window(picker)
        new_path = picker.selected_dir
        if new_path:
            self.import_root = new_path
            self.path_label.config(text=f"IMPORT Path: {self.import_root}")
            save_last_import_root(new_path)
            for bank in BANKS:
                self.slots[bank] = {}
            self.build_pad_slots(self.current_bank.get())
            self.sync_from_device(initial=False)

    def build_pad_slots(self, bank):
        for widget in self.pad_container.winfo_children():
            widget.destroy()
        self.pad_container.grid_columnconfigure((0, 1, 2), weight=1)
        for pad in PADS:
            existing = self.slots[bank].get(pad)
            new_slot = SampleSlot(self.pad_container, pad, self)
            if existing and getattr(existing, "filepath", None):
                try:
                    from_sync_flag = False
                    if hasattr(existing, "label"):
                        try:
                            from_sync_flag = "[on device]" in existing.label.cget("text")
                        except Exception:
                            from_sync_flag = False
                    new_slot.set_file(existing.filepath, from_sync=from_sync_flag)
                except Exception as e:
                    print(f"Warning: Bank {bank} PAD_{pad} could not be restored: {e}")
            self.slots[bank][pad] = new_slot

    def switch_bank(self, bank):
        self.build_pad_slots(bank)

    def sync_from_device(self, initial=False):
        if not os.path.exists(self.import_root):
            if not initial:
                messagebox.showwarning("Not Found", f"{self.import_root} not reachable.")
            return
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
                        placeholder = type("PlaceholderSlot", (), {})()
                        placeholder.filepath = full_path
                        placeholder.pad_num = pad
                        self.slots[bank][pad] = placeholder
                        found += 1
        self.build_pad_slots(self.current_bank.get())
        if not initial:
            messagebox.showinfo("Sync Complete", f"{found} existing samples found on the device.")

    def export_bank(self, bank):
        bank_path = os.path.join(self.import_root, f"BANK_{bank}")
        copied, skipped = 0, 0
        for pad, slot in self.slots[bank].items():
            pad_path = os.path.join(bank_path, f"PAD_{pad}")

            if not getattr(slot, "filepath", None):
                skipped += 1
                continue

            os.makedirs(pad_path, exist_ok=True)

            try:
                export_path = slot.get_export_ready_path()
            except Exception:
                export_path = slot.filepath

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
                messagebox.showerror("Copy Error", f"PAD_{pad}: {e}")

        return copied, skipped

    def delete_current_bank(self):
        bank = self.current_bank.get()
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

        has_loaded_samples = any(getattr(slot, "filepath", None) for slot in self.slots[bank].values())

        if not files_found and not has_loaded_samples:
            messagebox.showinfo("Nothing to Delete", f"Bank {bank} contains no samples.")
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

        answer = messagebox.askyesno("Confirm Bank Deletion", full_warning)
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

        self.slots[bank] = {}
        self.build_pad_slots(bank)

        if errors:
            messagebox.showerror("Partial Errors", "Some files could not be deleted:\n" + chr(10).join(errors))
        else:
            messagebox.showinfo("Bank Deleted", f"Bank {bank}: {deleted_count} file(s) deleted.")

    def export_current_bank(self):
        bank = self.current_bank.get()
        copied, skipped = self.export_bank(bank)
        messagebox.showinfo("Export Complete", f"Bank {bank}: {copied} copied, {skipped} empty.")

    def export_all_banks(self):
        total_c, total_s = 0, 0
        for bank in BANKS:
            c, s = self.export_bank(bank)
            total_c += c
            total_s += s
        messagebox.showinfo("Export Complete", f"All banks: {total_c} copied, {total_s} empty.")


if __name__ == "__main__":
    LAST_SAMPLE_DIR = load_last_sample_dir()
    root = tk.Tk()
    app = P6ManagerApp(root)
    root.mainloop()
