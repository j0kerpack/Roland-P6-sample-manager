import time as _time
import os as _os
_t_start = _time.perf_counter()

# Startup profiling. This was added to track down the slow window layout on
# Wayland/XWayland and is kept around in case that needs revisiting, but it
# is off by default so normal runs don't print diagnostics or pay for the
# per-draw timing calls. Enable with:  PYP6_DEBUG=1 python3 <script>
DEBUG_STARTUP = bool(_os.environ.get("PYP6_DEBUG"))

_PERF = {
    "panel_redraws": 0, "panel_redraw_time": 0.0,
    "button_draws": 0, "button_draw_time": 0.0,
    "dropdown_draws": 0, "dropdown_draw_time": 0.0,
}


def _log_timing(label):
    if DEBUG_STARTUP:
        print(f"[startup] {label}: {_time.perf_counter() - _t_start:6.3f}s elapsed")


def _log_perf_counters():
    if not DEBUG_STARTUP:
        return
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
import copy
import wave
import contextlib
import json
import subprocess
import threading
import queue
import colorsys
import re
_log_timing("stdlib imports (batch 1)")

if sys.platform.startswith("win"):
    # Every subprocess call (our own ffprobe lookups, and pydub's internal
    # ffmpeg calls for anything beyond plain WAV) launches a console
    # sub-process. A normal python.exe run has a console to attach to, so
    # this is invisible - but a PyInstaller --windowed/--noconsole build has
    # none, and Windows then briefly pops up a NEW console window for each
    # one before it closes. With MP3 folders that's one flash per file (the
    # "Haufen von Fenstern" behavior), and in some cases the console
    # allocation itself can make the child process fail to run correctly at
    # all, which lines up with MP3s not playing/showing a waveform. Patching
    # subprocess.Popen once, globally, fixes this for every caller
    # (including inside pydub, which we don't otherwise control) without
    # having to fix each individual call site.
    _original_popen_init = subprocess.Popen.__init__

    def _no_console_popen_init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        _original_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _no_console_popen_init

import tkinter as tk
from tkinter import ttk
_log_timing("tkinter imported")

try:
    # Optional: enables dragging sample files from the OS file manager
    # straight onto a pad. Not a hard requirement - without it the app runs
    # exactly as before, just without that one feature.
    #   pip install tkinterdnd2
    # PyInstaller note: this package ships native Tcl extension files, not
    # just .py - PyInstaller won't find them automatically. Build with:
    #   pyinstaller --collect-data tkinterdnd2 ...
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
    DND_IMPORT_ERROR = None
except ImportError as e:
    # Keep the reason. Swallowing it entirely meant a missing (or broken)
    # tkinterdnd2 looked exactly like drag & drop being switched off: no
    # message anywhere, no registration, no drag highlight, nothing in the
    # debug log either - just a feature that silently wasn't there.
    DND_AVAILABLE = False
    DND_IMPORT_ERROR = str(e)
_log_timing("tkinterdnd2 import attempted"
            + ("" if DND_AVAILABLE else f" - NOT AVAILABLE: {DND_IMPORT_ERROR}"))

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

APP_NAME = "PyP6"
APP_SUBTITLE = "Roland AIRA P-6 Sample Manager"
APP_VERSION = "2.8.0"
APP_AUTHOR = "Brian Siemund"
APP_YEAR = "2026"
APP_URL = "https://github.com/j0kerpack/Roland-P6-sample-manager"
# Single source of truth for the version: the window title and the About
# box both read these, so a release bump can't leave one of them stale.

APP_DIR = os.path.expanduser("~/.pyp6")
TEMP_DIR = os.path.join(APP_DIR, "temp")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")


def _module_version(module, dist_name=None):
    """Best-effort version string for an optional dependency.

    Tries __version__ first, then the installed package metadata (pydub,
    for one, ships no __version__ attribute). Never raises - a missing
    version in an About box is cosmetic and must not break the dialog."""
    try:
        v = getattr(module, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    if dist_name:
        try:
            from importlib.metadata import version as _dist_version
            return str(_dist_version(dist_name))
        except Exception:
            pass
    return "installed (version unknown)"


_DND_APP = None  # set by P6ManagerApp so the status below can see the live state


def _dnd_is_working():
    return bool(DND_AVAILABLE and getattr(_DND_APP, "_dnd_registered", None))


def dnd_status_text():
    """Reports what drag & drop is ACTUALLY doing, not just whether the
    module imported. Those are different failures with different fixes:
    a missing package is a pip install, a failed registration is a tkdnd
    or compositor problem - and the old text said "active" for both."""
    if not DND_AVAILABLE:
        reason = f" - {DND_IMPORT_ERROR}" if DND_IMPORT_ERROR else ""
        return f"tkinterdnd2 not available (pip install tkinterdnd2){reason}"
    registered = getattr(_DND_APP, "_dnd_registered", None)
    if registered is None:
        return "tkinterdnd2 loaded, drop targets not registered yet"
    if not registered:
        return "tkinterdnd2 loaded, but NO drop target could be registered"
    return "active (" + ", ".join(registered) + ")"


def collect_about_info():
    """(label, value) pairs describing this install, for the About box.

    Also what the About box's Copy button puts on the clipboard, so a bug
    report can be pasted with the exact versions and paths involved -
    which is the whole reason the optional-dependency state is in here."""
    rows = []
    rows.append(("Version", APP_VERSION))
    rows.append(("Author", APP_AUTHOR))
    rows.append(("Copyright", f"\u00a9 {APP_YEAR} {APP_AUTHOR}"))
    rows.append(("Project", APP_URL))
    try:
        rows.append(("Python", f"{sys.version.split()[0]} on {sys.platform}"))
    except Exception:
        pass
    try:
        rows.append(("Tk", str(tk.TkVersion)))
    except Exception:
        pass
    rows.append(("NumPy", _module_version(np, "numpy")))
    rows.append(("soundfile", _module_version(sf, "soundfile")))
    rows.append(("sounddevice", _module_version(sd, "sounddevice")))
    if PYDUB_AVAILABLE:
        try:
            import pydub as _pydub
            rows.append(("pydub", _module_version(_pydub, "pydub")))
        except Exception:
            rows.append(("pydub", "installed"))
    else:
        rows.append(("pydub", "not installed - Chop, MP3, rate/pitch/mono disabled"))
    if FFMPEG_AVAILABLE:
        converter = None
        try:
            converter = AudioSegment.converter
        except Exception:
            pass
        rows.append(("ffmpeg", str(converter) if converter else "found"))
    else:
        rows.append(("ffmpeg", "not found"))
    rows.append(("Drag & drop", dnd_status_text()))
    rows.append(("Settings file", CONFIG_FILE))
    rows.append(("Temp folder", TEMP_DIR))
    return rows


def about_info_as_text():
    lines = [f"{APP_NAME} - {APP_SUBTITLE}"]
    lines += [f"{label}: {value}" for label, value in collect_about_info()]
    return "\n".join(lines)


def ensure_app_dirs():
    """Creates the app's folders on demand. Everything the app writes lives
    under ~/.pyp6: config.json at the top, and all generated audio (trims,
    mono downmixes, normalized copies, chops, preview conversions) under
    temp/. Keeping config OUTSIDE temp/ matters - it means clearing temp
    can never wipe the user's settings."""
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
    except Exception as e:
        print(f"Could not create app folders: {e}")
    return TEMP_DIR


def temp_path(filename):
    """Absolute path for a generated file inside the temp folder."""
    ensure_app_dirs()
    return os.path.join(TEMP_DIR, filename)


def get_temp_folder_size():
    """(total_bytes, file_count) of the temp folder."""
    total, count = 0, 0
    for root, _dirs, files in os.walk(TEMP_DIR):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except OSError:
                pass
    return total, count


def clear_temp_folder():
    """Deletes everything inside temp/ (but keeps the folder itself).
    Returns (deleted_count, errors)."""
    deleted, errors = 0, []
    if not os.path.isdir(TEMP_DIR):
        return deleted, errors
    try:
        names = os.listdir(TEMP_DIR)
    except Exception as e:
        # e.g. permission denied, or the folder vanished between the
        # isdir() check above and here.
        return deleted, [f"Could not read temp folder: {e}"]
    for name in names:
        full = os.path.join(TEMP_DIR, name)
        try:
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
            deleted += 1
        except Exception as e:
            errors.append(f"{name}: {e}")
    return deleted, errors

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
MAX_UNDO_STEPS = 5
TARGET_RATES = [44100, 22050, 14700, 11025]
SLICE_COUNTS = [1, 2, 4, 8, 16, 24, 32, 48, 64]
# Chop normalize choices. "Per sample" evens out slices recorded at
# different levels; "Whole file" only lifts the overall level and keeps the
# balance between slices. Mutually exclusive - see build_chop_file().
NORMALIZE_MODES = ["Off", "Per sample", "Whole file"]
NORMALIZE_MODE_KEYS = {"Off": "off", "Per sample": "per_sample", "Whole file": "whole"}
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
    # Catppuccin Latte - the light member of the Catppuccin family.
    # Surfaces are the official values. The accents are darkened slightly
    # from the official ones because this UI reuses them as warning/status
    # TEXT, and Latte's originals are too light for that: official orange
    # scored ~2.6 and green ~3.0 against these backgrounds, well under the
    # 4.5 readability threshold. The adjusted values keep the same hue
    # character while clearing it in both roles (as text, and with white
    # on top when used as solid button fills).
    BG_DARK = "#EFF1F5"
    BG_PANEL = "#FFFFFF"
    BG_INPUT = "#E6E9EF"
    FG_TEXT = "#4C4F69"
    FG_MUTED = "#5C5F73"
    ACCENT_BLUE = "#1B57CE"
    ACCENT_GREEN = "#337524"
    ACCENT_RED = "#C10E35"
    ACCENT_ORANGE = "#A85107"
    ACCENT_PURPLE = "#7B33D6"
    SELECT_GREEN = "#CFE6C7"
    BORDER_COLOR = "#CCD0DA"
    BORDER_LIGHT = "#BCC0CC"
    WAVE_BG = "#DCE0E8"
    HOVER_BG = "#E1E4EA"
elif THEME == "latte":
    # Based on Catppuccin Latte, but pulled a step darker and noticeably
    # desaturated: the official palette's accents are vivid enough to feel
    # loud across a dense UI like this one. Every accent below is also
    # dark enough to stay readable as TEXT on these surfaces (they double
    # as warning/status colors), while keeping white legible on top of
    # them when used as solid button fills.
    BG_DARK = "#E4E6EC"
    BG_PANEL = "#F2F3F7"
    BG_INPUT = "#D8DBE3"
    FG_TEXT = "#3F4256"
    FG_MUTED = "#5C5F73"
    ACCENT_BLUE = "#2F5FBF"
    ACCENT_GREEN = "#396E2E"
    ACCENT_RED = "#A8324A"
    ACCENT_ORANGE = "#9A5518"
    ACCENT_PURPLE = "#6B44AB"
    SELECT_GREEN = "#C7DCC0"
    BORDER_COLOR = "#C3C6D2"
    BORDER_LIGHT = "#ACB0BE"
    WAVE_BG = "#CCD0DA"
    HOVER_BG = "#CFD3DC"
elif THEME == "tokyo":
    # Tokyo Night - deep navy with muted purples. Official palette, except
    # the muted grey, which at #787C99 scored only 3.6 against the panel
    # background; this UI uses FG_MUTED for real content (file names, hints,
    # storage labels), so it's lightened just enough to clear 4.5.
    BG_DARK = "#1A1B26"
    BG_PANEL = "#24283B"
    BG_INPUT = "#292E42"
    FG_TEXT = "#C0CAF5"
    FG_MUTED = "#8A8FAD"
    ACCENT_BLUE = "#7AA2F7"
    ACCENT_GREEN = "#9ECE6A"
    ACCENT_RED = "#F7768E"
    ACCENT_ORANGE = "#FF9E64"
    ACCENT_PURPLE = "#BB9AF7"
    SELECT_GREEN = "#2E4B36"
    BORDER_COLOR = "#3B4261"
    BORDER_LIGHT = "#545C7E"
    WAVE_BG = "#16161E"
    HOVER_BG = "#343A55"
elif THEME == "dracula":
    # Dracula - the high-contrast classic. Official palette, except the red,
    # which at #FF5555 scored 3.8 against the panel background; it's used
    # for the storage-over-limit warning text, so it's lightened slightly
    # to stay readable there while keeping dark text legible on top of it
    # when used as a solid button fill.
    BG_DARK = "#282A36"
    BG_PANEL = "#343746"
    BG_INPUT = "#44475A"
    FG_TEXT = "#F8F8F2"
    FG_MUTED = "#9CA0B0"
    ACCENT_BLUE = "#8BE9FD"
    ACCENT_GREEN = "#50FA7B"
    ACCENT_RED = "#FF7B7B"
    ACCENT_ORANGE = "#FFB86C"
    ACCENT_PURPLE = "#BD93F9"
    SELECT_GREEN = "#2F5D3F"
    BORDER_COLOR = "#44475A"
    BORDER_LIGHT = "#6272A4"
    WAVE_BG = "#21222C"
    HOVER_BG = "#4E5266"
elif THEME == "modern":
    # A third option distinct from both: warm graphite instead of the
    # cooler blue-grey of "dark", with softer, more contemporary accent
    # colors (periwinkle/mint/coral/amber) rather than the more traditional
    # saturated primaries.
    BG_DARK = "#1A1B23"
    BG_PANEL = "#22232D"
    BG_INPUT = "#2C2D38"
    FG_TEXT = "#EDEDF2"
    FG_MUTED = "#8E8FA3"
    ACCENT_BLUE = "#6C8EEF"
    ACCENT_GREEN = "#4ADE80"
    ACCENT_RED = "#F87171"
    ACCENT_ORANGE = "#FBBF6D"
    ACCENT_PURPLE = "#C084FC"
    SELECT_GREEN = "#1F4D34"
    BORDER_COLOR = "#34353F"
    BORDER_LIGHT = "#4A4B58"
    WAVE_BG = BG_INPUT
    HOVER_BG = "#33343F"
else:
    # Accents here are slightly brighter than the original muted values:
    # they double as warning/status TEXT on these surfaces, and the
    # originals fell short of readable contrast against BG_PANEL
    # (red was worst at 3.33, and it's what the over-limit warning uses).
    BG_DARK = "#1E1E24"
    BG_PANEL = "#2A2A33"
    BG_INPUT = "#33333E"
    FG_TEXT = "#E8E8ED"
    FG_MUTED = "#9A9AA5"
    ACCENT_BLUE = "#5FA6C0"
    ACCENT_GREEN = "#6BB06F"
    ACCENT_RED = "#DD807C"
    ACCENT_ORANGE = "#CC8A3D"
    ACCENT_PURPLE = "#8C5A99"
    SELECT_GREEN = "#3E7A3E"
    BORDER_COLOR = "#3D3D48"
    BORDER_LIGHT = "#5C5C6A"
    WAVE_BG = BG_INPUT
    HOVER_BG = "#3B3B47"  # slightly lighter than BG_INPUT, for hover feedback

WAVE_COLOR = "#1D7A9C"  # darker, theme-independent blue for the waveform trace itself


# ---------------------------------------------------------------------------
# Button fill colors
# ---------------------------------------------------------------------------
# The ACCENT_* colors above carry two jobs that pull in opposite directions:
# they're warning/status TEXT on a dark panel (wants a LIGHT color), and they
# are solid button fills under white text (wants a DARK one). The palettes
# were tuned for the text role, so on all four dark themes white-on-accent
# came out unreadable - Dracula's green was the extreme at 1.37:1, barely
# distinguishable from the fill behind it.
#
# Rather than compromise one color for both roles, the fill role gets its own
# derived value: same hue, saturation eased back a quarter so a large block
# of color doesn't shout, and lightness pulled down only as far as white text
# needs. The accents themselves are untouched and keep doing the text job.

def _relative_luminance(hex_color):
    h = hex_color.lstrip("#")
    channels = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(hex_a, hex_b):
    """WCAG contrast ratio between two colors (1.0 = identical, 21 = max)."""
    la, lb = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


BUTTON_SATURATION = 0.75  # button fills sit at 75% of the accent's saturation

# Density of the orange tint in the truncation overlay. On a dark background
# the same stipple reads far more strongly than on a light one, enough to
# swallow the red end marker where the two overlap - so dark themes get the
# next step down. Decided from BG_DARK's luminance rather than the theme
# name, so a future theme is classified correctly without touching this.
TRUNCATE_TINT_STIPPLE = "gray12" if _relative_luminance(BG_DARK) < 0.2 else "gray25"


def fill_for_white_text(hex_color, target_ratio=4.5, saturation_scale=BUTTON_SATURATION):
    """Turns an accent into a button fill that carries white text.

    Two steps, both in HLS so the hue never moves: pull the saturation back
    to `saturation_scale` of the original, then lower the lightness only as
    far as needed for white text to reach `target_ratio`. A large colored
    button is a much bigger block of color than the same accent used as a
    thin line of status text, and at full saturation those blocks shout -
    hence the first step, which applies on every theme.

    The order matters: desaturating changes luminance, so the contrast
    search has to run afterwards or the guarantee wouldn't hold."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    saturation *= saturation_scale

    def at(l):
        rr, gg, bb = colorsys.hls_to_rgb(hue, l, saturation)
        return "#{:02X}{:02X}{:02X}".format(round(rr * 255), round(gg * 255), round(bb * 255))

    if contrast_ratio(at(lightness), "#FFFFFF") >= target_ratio:
        return at(lightness)  # already dark enough; keep the original brightness

    lo, hi = 0.0, lightness
    for _ in range(24):  # binary search; 24 steps is far finer than 8-bit color
        mid = (lo + hi) / 2.0
        if contrast_ratio(at(mid), "#FFFFFF") >= target_ratio:
            lo = mid  # still readable - try to keep more of the original brightness
        else:
            hi = mid
    return at(lo)


BTN_BLUE = fill_for_white_text(ACCENT_BLUE)
BTN_GREEN = fill_for_white_text(ACCENT_GREEN)
BTN_RED = fill_for_white_text(ACCENT_RED)
BTN_ORANGE = fill_for_white_text(ACCENT_ORANGE)
BTN_PURPLE = fill_for_white_text(ACCENT_PURPLE)

MAIN_MIN_W = 1000
# Windows needs more headroom than Linux for the same content: its title
# bar and window borders are taller, and the default UI font renders a few
# pixels larger. 860 was arrived at by testing on an actual Windows box -
# at 835 the bottom row sat too tight.
MAIN_MIN_H = 860 if sys.platform.startswith("win") else 825
CHOP_MIN_W, CHOP_MIN_H = 1000, 815
PREVIEW_MIN_W, PREVIEW_MIN_H = 560, 480
AUDIO_PREVIEW_MIN_W, AUDIO_PREVIEW_MIN_H = 660, 580


def style_toplevel(win):
    win.configure(bg=BG_DARK)


def style_label(lbl, bg=BG_DARK, fg=FG_TEXT, **kw):
    lbl.config(bg=bg, fg=fg, **kw)


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

    style.configure("Dark.Vertical.TScrollbar",
                     background=BG_INPUT, troughcolor=BG_PANEL, bordercolor=BG_PANEL,
                     arrowcolor=FG_MUTED, relief="flat", arrowsize=12, width=10)
    style.map("Dark.Vertical.TScrollbar",
              background=[("active", ACCENT_BLUE), ("pressed", ACCENT_BLUE)])

    _TREEVIEW_STYLE_READY = True


def style_checkbutton(cb):
    cb.config(bg=BG_DARK, fg=FG_TEXT, selectcolor=BG_INPUT,
              activebackground=BG_DARK, activeforeground=FG_TEXT,
              disabledforeground=FG_MUTED,
              relief="flat", bd=0, highlightthickness=0,
              font=(UI_FAMILY, 9))


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text="", command=None, bg=BTN_BLUE, fg="#FFFFFF",
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
                 width=110, height=30, radius=10, font=(UI_FAMILY, 9, "bold"),
                 value_color_fn=None):
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
        # Optional callable(value) -> color-string-or-None, checked fresh
        # every time the menu opens (not cached), so it can reflect live
        # state - e.g. highlighting which banks currently hold samples.
        self.value_color_fn = value_color_fn
        self._draw()
        self.bind("<ButtonRelease-1>", self._open_menu)
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
            color = self.value_color_fn(v) if self.value_color_fn else None
            kwargs = {"foreground": color} if color else {}
            menu.add_command(label=str(v), command=lambda val=v: self._select(val), **kwargs)
        menu.bind("<Escape>", lambda e: menu.unpost())
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
        self._border_width = 1
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

    def set_border_color(self, color, width=None):
        """Changes the border color (and optionally its width) and forces a
        redraw, bypassing the size-based cache in _redraw() (which would
        otherwise skip drawing since only the color, not the size, changed).
        Used for drag-and-drop hover feedback - a color change alone at the
        default 1px width is easy to miss at a glance, so hover states
        should also pass a thicker width."""
        self._border = color
        if width is not None:
            self._border_width = width
        self._last_size = None
        self._redraw()

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
        self._round_rect(1, 1, w - 1, h - 1, r, fill=self._panel_bg, outline=self._border,
                          width=self._border_width)
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
                                     bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=80)
            yes_btn.pack(side="right", padx=4)
        else:
            ok_btn = RoundedButton(btn_row, text="OK", command=self._on_ok,
                                    bg=BTN_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=80)
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


class _DarkTextPromptDialog(tk.Toplevel):
    """Dark-themed replacement for tkinter.simpledialog.askstring."""

    def __init__(self, parent, title, prompt, initial=""):
        super().__init__(parent)
        self.title(title or "")
        style_toplevel(self)
        self.resizable(False, False)
        self.result = None

        body = tk.Frame(self, bg=BG_DARK, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        prompt_label = tk.Label(body, text=prompt, anchor="w", justify="left", wraplength=320)
        style_label(prompt_label, font=(UI_FAMILY, 10))
        prompt_label.pack(fill="x", pady=(0, 8))

        self.entry_var = tk.StringVar(value=initial)
        entry = tk.Entry(body, textvariable=self.entry_var, bg=BG_INPUT, fg=FG_TEXT,
                          insertbackground=FG_TEXT, relief="flat", highlightthickness=1,
                          highlightbackground=BORDER_COLOR, highlightcolor=ACCENT_BLUE,
                          font=(UI_FAMILY, 10), width=32)
        entry.pack(fill="x")
        entry.bind("<Return>", lambda e: self._on_ok())
        entry.bind("<Escape>", lambda e: self._on_cancel())
        entry.focus_set()
        entry.select_range(0, tk.END)

        btn_row = tk.Frame(body, bg=BG_DARK)
        btn_row.pack(fill="x", pady=(16, 0))
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self._on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=80)
        cancel_btn.pack(side="right", padx=4)
        ok_btn = RoundedButton(btn_row, text="OK", command=self._on_ok,
                                bg=BTN_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=80)
        ok_btn.pack(side="right", padx=4)

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.transient(parent)
        self.update_idletasks()
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

    def _on_ok(self):
        self.result = self.entry_var.get()
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


def dark_ask_text(parent, title, prompt, initial=""):
    root = _dark_msg_parent(parent)
    dlg = _DarkTextPromptDialog(root, title, prompt, initial)
    root.wait_window(dlg)
    return dlg.result


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


def load_last_export_dir():
    """Where the user last pointed the "P6 → Bank" import.

    Deliberately the folder they PICKED (usually the P-6 drive), not the
    BANK_x folder it resolved to - the device exports a different bank next
    time, so re-resolving from the picked folder finds whichever bank is
    actually there now."""
    data = load_config()
    path = data.get("last_export_dir")
    if path and os.path.isdir(path):
        return path
    return None


def save_config_value(key, value):
    data = load_config()
    data[key] = value
    try:
        # On a fresh install ~/.pyp6 doesn't exist yet - without this the
        # write fails and (because of the except below) settings would
        # silently never be saved.
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Could not save configuration: {e}")


PRESET_MANIFEST_NAME = "preset.json"
PRESET_FORMAT_VERSION = 2  # v2: force_mono moved from one global flag to per-bank


def is_preset_folder(path):
    """A folder counts as a preset if it directly contains preset.json."""
    return os.path.isfile(os.path.join(path, PRESET_MANIFEST_NAME))


def read_preset_manifest(preset_dir):
    """Returns the parsed preset.json, or None if missing/unreadable."""
    manifest_path = os.path.join(preset_dir, PRESET_MANIFEST_NAME)
    try:
        with open(manifest_path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "banks" not in data:
            return None
        return data
    except Exception:
        return None


def write_preset_manifest(preset_dir, data):
    manifest_path = os.path.join(preset_dir, PRESET_MANIFEST_NAME)
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2)


def load_recent_presets():
    return load_config().get("recent_presets", [])


def add_recent_preset(path):
    recents = load_recent_presets()
    recents = [p for p in recents if p != path]  # de-dupe, most-recent-first
    recents.insert(0, path)
    recents = recents[:5]
    save_config_value("recent_presets", recents)


def save_last_import_root(path):
    save_config_value("import_root", path)


def save_last_sample_dir(path):
    save_config_value("last_sample_dir", path)


def save_last_export_dir(path):
    save_config_value("last_export_dir", path)


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


# ---------------------------------------------------------------------------
# Tooltips (hover help)
# ---------------------------------------------------------------------------

TOOLTIP_DELAY_MS = 550       # long enough that they don't fire while just passing over
TOOLTIP_WRAPLENGTH = 320


def load_tooltips_enabled():
    return bool(load_config().get("tooltips_enabled", True))


TOOLTIPS_ENABLED = load_tooltips_enabled()


def set_tooltips_enabled(enabled):
    """Turns tooltips on/off for the whole app at runtime.

    Every Tooltip re-checks this global right before it shows, so a change
    takes effect immediately - no restart, and no need to keep a registry
    of every tooltip that has already been attached somewhere."""
    global TOOLTIPS_ENABLED
    TOOLTIPS_ENABLED = bool(enabled)
    if not TOOLTIPS_ENABLED:
        Tooltip.hide_active()


_TOOLTIP_BINDTAG = "PyP6Tooltip"
_TOOLTIP_CLASS_BOUND = False


def _tooltip_click_handler(event):
    """Tears down the tooltip on any mouse press, before the widget's own
    click handler gets to run (see the bindtags trick in Tooltip.__init__)."""
    tip = getattr(getattr(event, "widget", None), "_pyp6_tooltip", None)
    if tip is not None:
        tip._on_leave()
    else:
        Tooltip.hide_active()


class Tooltip:
    """A small delayed hover popup for one widget.

    Deliberately passive: an override-redirect Toplevel that never takes
    focus, never grabs, and is destroyed on the first Leave/click - so it
    can safely appear on top of the modal dialogs in this app (Chop, Load,
    Settings) without interfering with their grab_set()."""

    _active = None  # at most one tooltip is ever on screen

    def __init__(self, widget, text, delay=TOOLTIP_DELAY_MS, wraplength=TOOLTIP_WRAPLENGTH):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._after_id = None
        self._tip = None
        # add="+" everywhere: RoundedButton already binds <Enter>/<Leave>
        # for its hover fill, and the pad drag handles bind <ButtonPress-1>
        # for drag-to-swap. A plain bind() would silently replace those.
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")
        # Clicks are handled through an extra bind tag placed BEFORE the
        # widget's own tag, for two reasons. Tk only ever runs the single
        # most specific binding per tag, so a plain <ButtonPress> on the
        # widget itself would be shadowed by the existing <ButtonPress-1>
        # and never fire at all. And tags run in order, so this way the
        # tooltip is gone before the button's command runs - which matters
        # for buttons that open a modal window, since a tooltip left behind
        # is topmost and would float over it.
        self._install_click_binding(widget)

    @staticmethod
    def _install_click_binding(widget):
        global _TOOLTIP_CLASS_BOUND
        try:
            if not _TOOLTIP_CLASS_BOUND:
                widget.bind_class(_TOOLTIP_BINDTAG, "<ButtonPress>", _tooltip_click_handler)
                _TOOLTIP_CLASS_BOUND = True
            tags = widget.bindtags()
            if _TOOLTIP_BINDTAG not in tags:
                widget.bindtags((_TOOLTIP_BINDTAG,) + tuple(tags))
        except tk.TclError:
            pass  # tooltips still work, they just won't close on click

    @classmethod
    def hide_active(cls):
        if cls._active is not None:
            cls._active._hide()

    def set_text(self, text):
        self.text = text
        if self._tip is not None:
            self._hide()

    def _on_enter(self, event=None):
        self._cancel()
        if not TOOLTIPS_ENABLED or not self.text:
            return
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except tk.TclError:
            self._after_id = None

    def _on_leave(self, event=None):
        self._cancel()
        self._hide()

    def _on_destroy(self, event=None):
        # <Destroy> bubbles up from children too - only react to our own widget.
        if event is not None and getattr(event, "widget", None) is not self.widget:
            return
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except (tk.TclError, ValueError):
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        if not TOOLTIPS_ENABLED or self._tip is not None:
            return
        try:
            if not self.widget.winfo_exists() or not self.widget.winfo_ismapped():
                return
            # Re-check that the pointer is genuinely still on this widget.
            # A <Leave> can be missed when a grab changes or a dialog opens
            # right after <Enter>, and without this the tooltip would then
            # pop up over a window the mouse left long ago and stay there.
            px, py = self.widget.winfo_pointerxy()
            if self.widget.winfo_containing(px, py) is not self.widget:
                return
        except tk.TclError:
            return
        Tooltip.hide_active()  # never two at once (e.g. fast moves between buttons)
        try:
            tip = tk.Toplevel(self.widget)
            # Hidden until it's positioned - a freshly created Toplevel would
            # otherwise briefly flash in the top-left corner of the screen.
            tip.withdraw()
            tip.wm_overrideredirect(True)   # no title bar, no WM decorations
            tip.configure(bg=BORDER_LIGHT)  # the 1px padding below shows as a border
            label = tk.Label(tip, text=self.text, justify="left", anchor="w",
                             bg=BG_INPUT, fg=FG_TEXT, font=(UI_FAMILY, 9),
                             wraplength=self.wraplength, padx=8, pady=5,
                             bd=0, highlightthickness=0)
            label.pack(padx=1, pady=1)
            tip.update_idletasks()
            x, y = self._position(tip)
            tip.wm_geometry(f"+{x}+{y}")
            try:
                tip.attributes("-topmost", True)
            except tk.TclError:
                pass  # not supported everywhere; the tooltip still shows
            tip.deiconify()
            self._tip = tip
            Tooltip._active = self
        except tk.TclError:
            self._tip = None

    def _position(self, tip):
        """Below the widget, left-aligned to it, clamped to the screen.

        Below (not under the cursor) on purpose: a popup that lands beneath
        the pointer would immediately trigger <Leave> on the widget and the
        tooltip would flicker on/off forever."""
        w = self.widget
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = w.winfo_rootx()
        y = w.winfo_rooty() + w.winfo_height() + 8
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        if x + tw > sw - 4:
            x = max(4, sw - tw - 4)
        if y + th > sh - 4:
            y = max(4, w.winfo_rooty() - th - 8)  # no room below -> flip above
        return x, y

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
        if Tooltip._active is self:
            Tooltip._active = None


def add_tooltip(widget, text, **kwargs):
    """Attaches hover help to a widget.

    Intentionally forgiving: a missing widget, empty text, or a widget that
    can't be bound is skipped silently. Tooltips are a convenience layer and
    must never be able to break the UI they annotate."""
    if widget is None or not text:
        return None
    try:
        tip = Tooltip(widget, text, **kwargs)
    except tk.TclError:
        return None
    # Keep a hard reference on the widget itself. Tkinter's binding table
    # already holds one, but this makes the lifetime explicit and lets a
    # caller update the text later via widget._pyp6_tooltip.set_text(...).
    try:
        widget._pyp6_tooltip = tip
    except Exception:
        pass
    return tip


_TEMP_TAG_RE = re.compile(
    r"_(?:trim|norm|fade|mono|imp|chop|conv)(?:_[A-H][1-6])?_[0-9a-f]{6,8}$", re.IGNORECASE)


def safe_base_name(path, max_len=48, strip_tags=False, fallback="sample"):
    """A filesystem-safe, length-capped base name derived from `path`.

    Every temp file this app writes can end up copied to the P-6, whose
    drive is FAT-formatted - so a name that Linux accepts happily can still
    make the copy fail there. Characters FAT/Windows reject are replaced,
    leading dots (which would make a hidden file on Unix) are dropped, and
    trailing dots/spaces are trimmed because FAT silently mangles those.
    The length cap keeps names readable on the device's small display and
    well clear of any path-length limit.

    With strip_tags, a marker this app appended earlier is removed first,
    so repeated edits don't accumulate a chain of suffixes."""
    base = os.path.splitext(os.path.basename(path or ""))[0]
    base = re.sub(r"[^\w \-.]", "_", base)
    # A source called ".wav" splits to a base of ".wav" - keeping that would
    # write a dot-prefixed (hidden on Unix) file.
    base = base.strip().lstrip(".")
    if strip_tags:
        while True:
            stripped = _TEMP_TAG_RE.sub("", base)
            if stripped == base:
                break
            base = stripped
    return base[:max_len].strip(" .") or fallback


def derived_temp_path(source_path, tag, ext=".wav"):
    """Temp filename that keeps the source sample's name recognizable.

    Named purely `trim_<hex>.wav` before, which meant a sample lost its
    identity the first time it was trimmed - and since these files are what
    gets copied to the device, the P-6 ended up showing names like
    `import_A1_347ec70f` with no clue what the sample was.

    The random part stays: two pads can hold differently edited versions of
    the same source, and they must not overwrite each other."""
    return temp_path(f"{safe_base_name(source_path, strip_tags=True)}_{tag}_"
                     f"{uuid.uuid4().hex[:8]}{ext}")


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


def compute_truncate_fraction(path, target_rate=None, pitch_cents=0, force_mono=False):
    """Where the P-6's length limit falls inside `path`, as a fraction of the
    file's ORIGINAL (un-pitch-shifted) timeline - or None if the sample fits.

    Single source of truth for every truncation overlay in the app (main
    waveform, pad mini waveform, pad editor), so they can't drift apart.
    Deliberately mirrors check_duration_warning's rule exactly: that one
    tests duration/speed > limit, this one tests duration > limit*speed,
    which is the same comparison rearranged. The channel key must include
    `channels == 1` - a mono source file gets the mono limit even with
    Force Mono switched off, and leaving that out silently halves the
    allowed length for every mono sample."""
    try:
        duration, _, channels = get_wav_info(path)
    except Exception:
        return None
    if not duration:
        return None
    ch_key = 1 if (force_mono or channels == 1) else 2
    limit = MAX_SECONDS.get((target_rate, ch_key))
    if not limit:
        return None
    speed_factor = pitch_speed_factor(pitch_cents) if pitch_cents else 1.0
    limit_original_seconds = limit * speed_factor
    if limit_original_seconds >= duration:
        return None  # fits, nothing to shade
    return limit_original_seconds / duration


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


def draw_truncate_overlay(canvas, x_cut, x_right, height_px, tag="truncate"):
    """Dark wash + orange tint over [x_cut, x_right] plus a dashed cutoff
    line - the app-wide visual language for "the P-6 will cut this off".

    Shared by the main waveform, the pad mini waveforms, the pad editor and
    the Chop view so all four look identical; callers only work out where
    the cut is, never how it's drawn. Purely additive - clearing the tag is
    left to the caller, so a caller that needs several shaded regions in one
    redraw can just call this repeatedly."""
    if x_right <= x_cut:
        return
    canvas.create_rectangle(x_cut, 0, x_right, height_px,
                            fill=BG_DARK, stipple="gray50", outline="", tags=tag)
    canvas.create_rectangle(x_cut, 0, x_right, height_px,
                            fill=ACCENT_ORANGE, stipple=TRUNCATE_TINT_STIPPLE,
                            outline="", tags=tag)
    canvas.create_line(x_cut, 0, x_cut, height_px,
                       fill=ACCENT_ORANGE, width=1, dash=(3, 2), tags=tag)


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
    out_path = derived_temp_path(src_path, "trim")
    sf.write(out_path, trimmed, fs, subtype="PCM_16")  # P-6 requires 16-bit PCM
    return out_path


def normalize_wav_file(src_path, target_peak=0.98):
    """Peak-normalizes a wav file (scales so the loudest sample reaches
    target_peak, just under full scale to leave headroom against rounding),
    writing the result to a new temp file. Pure numpy/soundfile, no pydub
    dependency needed - same pattern as trim_wav_file/ensure_mono_wav."""
    data, fs = sf.read(src_path, dtype="float32")
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 0:
        data = data * (target_peak / peak)
    out_path = derived_temp_path(src_path, "norm")
    sf.write(out_path, data, fs, subtype="PCM_16")  # P-6 requires 16-bit PCM
    return out_path


def apply_fade_envelope(data, fs, fade_in_s=0.0, fade_out_s=0.0):
    """Applies a linear fade-in/fade-out directly to an in-memory numpy
    array (mono or multi-channel), in place on a copy. Shared by the
    waveform-preview rendering, the audition playback, and the file-writing
    path in PadWaveformViewDialog, so all three always agree on the exact
    same envelope shape."""
    n = len(data)
    if n == 0 or (fade_in_s <= 0 and fade_out_s <= 0):
        return data
    data = data.copy()
    fade_in_n = min(int(fade_in_s * fs), n)
    fade_out_n = min(int(fade_out_s * fs), n)
    if fade_in_n > 0:
        ramp = np.linspace(0.0, 1.0, fade_in_n)
        if data.ndim > 1:
            ramp = ramp[:, None]
        data[:fade_in_n] = data[:fade_in_n] * ramp
    if fade_out_n > 0:
        ramp = np.linspace(1.0, 0.0, fade_out_n)
        if data.ndim > 1:
            ramp = ramp[:, None]
        data[-fade_out_n:] = data[-fade_out_n:] * ramp
    return data


def apply_fade_to_wav_file(src_path, fade_in_s, fade_out_s):
    """File-level wrapper around apply_fade_envelope, writing the result to
    a new temp file - same pattern as normalize_wav_file/trim_wav_file."""
    data, fs = sf.read(src_path, dtype="float32")
    data = apply_fade_envelope(data, fs, fade_in_s, fade_out_s)
    out_path = derived_temp_path(src_path, "fade")
    sf.write(out_path, data, fs, subtype="PCM_16")  # P-6 requires 16-bit PCM
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
    out_path = derived_temp_path(path, "mono")
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
    # Write into the app temp folder rather than beside the source file:
    # the source may live on read-only media, and writing there would also
    # litter the user's sample library with converted copies.
    # Sanitized: this name is what lands on the P-6's FAT-formatted drive,
    # and an unfiltered source name (colons, wildcards, 200 characters) can
    # make that copy fail on the device even though writing it to the temp
    # folder on Linux succeeded.
    out_path = temp_path(f"{safe_base_name(filepath)}{suffix}.wav")
    audio.export(out_path, format="wav")
    return out_path


def convert_to_wav_if_needed(path):
    if path.lower().endswith(".wav"):
        return path, False
    if not PYDUB_AVAILABLE:
        dark_showerror("pydub missing", "MP3 conversion requires pydub + ffmpeg.")
        return path, False
    try:
        sound = AudioSegment.from_file(path)
        # Write into the app's temp folder rather than next to the source
        # file - that would litter the user's sample library with
        # *_converted.wav files, and fails outright if the source sits on a
        # read-only location.
        wav_path = temp_path(f"{safe_base_name(path)}_conv_{uuid.uuid4().hex[:6]}.wav")
        sound.export(wav_path, format="wav")
        return wav_path, True
    except Exception as e:
        dark_showerror("Conversion Error", f"Details: {e}")
        return path, False


def build_chop_file(file_paths, rate, channels, num_slices, normalize_mode="off"):
    """Renders `file_paths` into one multisample.

    normalize_mode:
      "off"        - levels are left exactly as they are.
      "per_sample" - every slice is peak-normalized on its own, so slices
                     recorded at different levels end up equally loud. Done
                     AFTER the slice is cut to length, so a loud transient
                     that gets truncated away can't set the gain for the
                     whole slice.
      "whole"      - only the finished file is normalized, which lifts the
                     overall level but keeps the balance between slices.
    Per-sample and whole-file are deliberately exclusive: once every slice
    peaks at full scale, normalizing the sum again is a no-op."""
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

        if normalize_mode == "per_sample":
            # After the truncation above, so the gain is set by what will
            # actually be heard, and before the silence padding below, which
            # can't affect the peak either way.
            audio = pydub_normalize(audio)

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

    if normalize_mode == "whole":
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
                                bg=BTN_BLUE, fg="#FFFFFF", parent_bg=container_bg, width=50, height=28)
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
            dark_showwarning("Not Found", f"Folder does not exist:\n{path}", parent=self)

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

        hint = tk.Label(self, text="Double-click/Enter: open folder  \u2022  \u2191 Up or Backspace: go up  "
                                    "\u2022  Type/paste a path above + Enter",
                         anchor="w")
        style_label(hint, fg=FG_MUTED, font=(UI_FAMILY, 8))
        hint.pack(fill="x", padx=10)

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        cancel_btn.pack(side="right", padx=4)
        select_btn = RoundedButton(btn_row, text="Select This Folder", command=self.on_confirm,
                                    bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=170)
        select_btn.pack(side="right", padx=4)

        self.refresh_list()
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
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


class PresetSaveDialog(FolderNavMixin, tk.Toplevel):
    """Navigate to a folder, name the preset, and choose which banks to
    include. Clicking an existing preset folder pre-fills its name - saving
    then only overwrites the checked banks, leaving the rest of that
    preset's banks untouched (partial overwrite)."""

    def __init__(self, parent, app, initial_dir=None):
        super().__init__(parent)
        self.app = app
        self.title("Save Preset")
        self.geometry("580x600")
        self.minsize(580, 600)
        style_toplevel(self)
        self.current_dir = initial_dir or os.path.expanduser("~")
        self.result_dir = None

        self._build_nav_bar(container_bg=BG_DARK)

        toolbar = tk.Frame(self, padx=10, bg=BG_DARK)
        toolbar.pack(fill="x")
        new_folder_btn = RoundedButton(toolbar, text="+ New Folder", command=self.on_new_folder,
                                        bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=110, height=26,
                                        font=(UI_FAMILY, 8))
        new_folder_btn.pack(side="left")

        list_frame = tk.Frame(self, padx=10, pady=6, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=(UI_FAMILY, 10))
        style_listbox(self.listbox)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self.on_double_click)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<BackSpace>", lambda e: self.go_up())

        hint = tk.Label(self, text="Folders marked \U0001F3B9 already contain a preset - "
                                    "click one to overwrite it (only checked banks are replaced).",
                         anchor="w")
        style_label(hint, fg=FG_MUTED, font=(UI_FAMILY, 8))
        hint.pack(fill="x", padx=10)

        name_row = tk.Frame(self, padx=10, bg=BG_DARK)
        name_row.pack(fill="x", pady=(8, 6))
        name_lbl = tk.Label(name_row, text="Preset Name:")
        style_label(name_lbl, font=(UI_FAMILY, 9))
        name_lbl.pack(side="left")
        self.name_var = tk.StringVar()
        self.name_entry = tk.Entry(name_row, textvariable=self.name_var, bg=BG_INPUT, fg=FG_TEXT,
                                    insertbackground=FG_TEXT, relief="flat", highlightthickness=1,
                                    highlightbackground=BORDER_COLOR, highlightcolor=ACCENT_BLUE,
                                    font=(UI_FAMILY, 10))
        self.name_entry.pack(side="left", fill="x", expand=True, padx=6)

        banks_panel = RoundedPanel(self, title="Banks to Save", parent_bg=BG_DARK, panel_bg=BG_PANEL,
                                    border=BORDER_LIGHT, radius=14, title_fg=ACCENT_BLUE)
        banks_panel.pack(fill="x", padx=10, pady=(0, 8))
        self.bank_vars = {}
        bank_row = tk.Frame(banks_panel.body, bg=BG_PANEL)
        bank_row.pack(fill="x", pady=(10, 4))
        for bank in BANKS:
            has_samples = self.app.bank_has_samples(bank)
            var = tk.BooleanVar(value=has_samples)
            self.bank_vars[bank] = var
            cb = tk.Checkbutton(bank_row, text=bank, variable=var)
            style_checkbutton(cb)
            cb.config(bg=BG_PANEL, activebackground=BG_PANEL)
            cb.pack(side="left", padx=6)
        select_row = tk.Frame(banks_panel.body, bg=BG_PANEL)
        select_row.pack(fill="x")
        all_btn = RoundedButton(select_row, text="All", command=lambda: self._set_all_banks(True),
                                 bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=70, height=24,
                                 font=(UI_FAMILY, 8))
        all_btn.pack(side="left", padx=(0, 4))
        none_btn = RoundedButton(select_row, text="None", command=lambda: self._set_all_banks(False),
                                  bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=70, height=24,
                                  font=(UI_FAMILY, 8))
        none_btn.pack(side="left")

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        cancel_btn.pack(side="right", padx=4)
        save_btn = RoundedButton(btn_row, text="Save", command=self.on_save,
                                  bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=90)
        save_btn.pack(side="right", padx=4)

        self._entries = []
        self.refresh_list()
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _set_all_banks(self, value):
        for var in self.bank_vars.values():
            var.set(value)

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

    def on_new_folder(self):
        name = dark_ask_text(self, "New Folder", "Folder name:")
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if any(c in name for c in '\\/:*?"<>|'):
            dark_showerror("Invalid Name", "The folder name can't contain: \\ / : * ? \" < > |",
                            parent=self)
            return
        new_path = os.path.join(self.current_dir, name)
        if os.path.exists(new_path):
            dark_showerror("Already Exists", f"'{name}' already exists in this folder.", parent=self)
            return
        try:
            os.makedirs(new_path)
        except Exception as e:
            dark_showerror("Could Not Create Folder", str(e), parent=self)
            return
        self.navigate_to(new_path)

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
        self._entries = [".."]
        self.listbox.insert(tk.END, "..")
        for entry in entries:
            self._entries.append(entry)
            full = os.path.join(self.current_dir, entry)
            if is_preset_folder(full):
                idx = self.listbox.size()
                self.listbox.insert(tk.END, f"\U0001F3B9 {entry}")
                self.listbox.itemconfig(idx, fg=ACCENT_ORANGE)
            else:
                self.listbox.insert(tk.END, entry)

    def on_double_click(self, event):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._entries):
            return
        entry = self._entries[sel[0]]
        if entry == "..":
            self.go_up()
        else:
            self.navigate_to(os.path.join(self.current_dir, entry))

    def on_select(self, event):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._entries):
            return
        entry = self._entries[sel[0]]
        if entry != "..":
            full = os.path.join(self.current_dir, entry)
            if is_preset_folder(full):
                self.name_var.set(entry)  # one click to target an existing preset for overwrite

    def on_save(self):
        name = self.name_var.get().strip()
        if not name:
            dark_showerror("Name Required", "Please enter a preset name.", parent=self)
            return
        if any(c in name for c in '\\/:*?"<>|'):
            dark_showerror("Invalid Name", "The preset name can't contain: \\ / : * ? \" < > |",
                            parent=self)
            return
        banks_to_save = [b for b, var in self.bank_vars.items() if var.get()]
        if not banks_to_save:
            dark_showerror("No Banks Selected", "Check at least one bank to save.", parent=self)
            return

        target = os.path.join(self.current_dir, name)
        if is_preset_folder(self.current_dir):
            proceed = dark_askyesno(
                "Nested Preset?",
                "You are currently inside a preset folder. Saving here would put a "
                "preset inside another preset, which is confusing to manage later.\n\n"
                "Save here anyway?",
                parent=self
            )
            if not proceed:
                return

        if is_preset_folder(target):
            proceed = dark_askyesno(
                "Overwrite Preset?",
                f"'{name}' already exists as a preset.\n\n"
                f"The checked bank(s) ({', '.join(banks_to_save)}) will be overwritten. "
                f"Other banks already saved in this preset are left as-is.\n\nContinue?",
                parent=self
            )
            if not proceed:
                return
        elif os.path.isdir(target):
            # An existing folder that is NOT a preset - writing into it would
            # scatter BANK_*/preset.json into someone's unrelated directory.
            proceed = dark_askyesno(
                "Folder Already Exists",
                f"'{name}' already exists but is not a preset folder.\n\n"
                "Preset files would be written into that existing folder.\n\nContinue?",
                parent=self
            )
            if not proceed:
                return

        try:
            self.result_dir = self.app.save_preset_to_folder(self.current_dir, name, banks_to_save)
        except Exception as e:
            dark_showerror("Save Error", str(e), parent=self)
            return
        self.destroy()

    def on_cancel(self):
        self.result_dir = None
        self.destroy()


class PresetLoadDialog(FolderNavMixin, tk.Toplevel):
    """Navigate to and click a preset folder, then choose which of its
    banks to actually load. Banks not present in that preset simply aren't
    offered as an option."""

    def __init__(self, parent, app, initial_dir=None, preselect_path=None):
        super().__init__(parent)
        self.app = app
        self.title("Load Preset")
        self.geometry("580x600")
        self.minsize(580, 600)
        style_toplevel(self)
        self.current_dir = initial_dir or os.path.expanduser("~")
        self.result_dir = None
        self.result_banks = []
        self.result_target_override = None
        self.selected_preset_dir = None
        self._preselect_path = preselect_path

        self._build_nav_bar(container_bg=BG_DARK)

        list_frame = tk.Frame(self, padx=10, pady=6, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=(UI_FAMILY, 10))
        style_listbox(self.listbox)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self.on_double_click)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<BackSpace>", lambda e: self.go_up())

        hint = tk.Label(self, text="Folders marked \U0001F3B9 contain a preset - click one to see its banks.",
                         anchor="w")
        style_label(hint, fg=FG_MUTED, font=(UI_FAMILY, 8))
        hint.pack(fill="x", padx=10)

        self.banks_panel = RoundedPanel(self, title="Banks to Load", parent_bg=BG_DARK, panel_bg=BG_PANEL,
                                         border=BORDER_LIGHT, radius=14, title_fg=ACCENT_BLUE)
        self.banks_panel.pack(fill="x", padx=10, pady=(8, 8))
        self.bank_vars = {}
        self.bank_checkbuttons = {}
        bank_row = tk.Frame(self.banks_panel.body, bg=BG_PANEL)
        bank_row.pack(fill="x", pady=(10, 4))
        for bank in BANKS:
            var = tk.BooleanVar(value=False)
            self.bank_vars[bank] = var
            cb = tk.Checkbutton(bank_row, text=bank, variable=var, state="disabled",
                                 command=self._on_bank_checkbox_changed)
            style_checkbutton(cb)
            cb.config(bg=BG_PANEL, activebackground=BG_PANEL)
            cb.pack(side="left", padx=6)
            self.bank_checkbuttons[bank] = cb
        self.no_preset_label = tk.Label(self.banks_panel.body, text="No preset selected yet.", anchor="w")
        style_label(self.no_preset_label, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        self.no_preset_label.pack(fill="x")

        current_bank_letter = self.app.current_bank.get()
        self.to_current_bank_var = tk.BooleanVar(value=False)
        self.to_current_bank_cb = tk.Checkbutton(
            self.banks_panel.body,
            text=f"Load into current bank (Bank {current_bank_letter}) instead of its original slot",
            variable=self.to_current_bank_var, state="disabled")
        style_checkbutton(self.to_current_bank_cb)
        self.to_current_bank_cb.config(bg=BG_PANEL, activebackground=BG_PANEL)
        self.to_current_bank_cb.pack(fill="x", pady=(6, 0))

        btn_row = tk.Frame(self, padx=10, pady=10, bg=BG_DARK)
        btn_row.pack(fill="x")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        cancel_btn.pack(side="right", padx=4)
        self.load_btn = RoundedButton(btn_row, text="Load", command=self.on_load,
                                       bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=90,
                                       state="disabled")
        self.load_btn.pack(side="right", padx=4)

        self._entries = []
        self.refresh_list()
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _on_bank_checkbox_changed(self):
        """The 'load into current bank' option only makes sense with exactly
        one bank checked - otherwise it's ambiguous which one would go
        there. Enable/disable it accordingly, and uncheck it if it's no
        longer a valid choice."""
        checked_count = sum(1 for var in self.bank_vars.values() if var.get())
        if checked_count == 1:
            self.to_current_bank_cb.config(state="normal")
        else:
            self.to_current_bank_var.set(False)
            self.to_current_bank_cb.config(state="disabled")

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
        self._entries = [".."]
        self.listbox.insert(tk.END, "..")
        for entry in entries:
            self._entries.append(entry)
            full = os.path.join(self.current_dir, entry)
            if is_preset_folder(full):
                idx = self.listbox.size()
                self.listbox.insert(tk.END, f"\U0001F3B9 {entry}")
                self.listbox.itemconfig(idx, fg=ACCENT_ORANGE)
            else:
                self.listbox.insert(tk.END, entry)

        if self._preselect_path:
            target_name = os.path.basename(self._preselect_path.rstrip(os.sep))
            for i, entry in enumerate(self._entries):
                if entry == target_name:
                    self.listbox.selection_set(i)
                    self.listbox.see(i)
                    self._show_preset_banks(os.path.join(self.current_dir, entry))
                    break
            self._preselect_path = None  # only auto-apply once

    def on_double_click(self, event):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._entries):
            return
        entry = self._entries[sel[0]]
        if entry == "..":
            self.go_up()
        else:
            self.navigate_to(os.path.join(self.current_dir, entry))

    def on_select(self, event):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._entries):
            self._show_preset_banks(None)
            return
        entry = self._entries[sel[0]]
        if entry == "..":
            self._show_preset_banks(None)
            return
        full = os.path.join(self.current_dir, entry)
        if is_preset_folder(full):
            self._show_preset_banks(full)
        else:
            self._show_preset_banks(None)

    def _show_preset_banks(self, preset_dir):
        self.selected_preset_dir = preset_dir
        manifest = read_preset_manifest(preset_dir) if preset_dir else None
        available_banks = set((manifest or {}).get("banks", {}).keys())

        for bank, cb in self.bank_checkbuttons.items():
            if bank in available_banks:
                cb.config(state="normal")
                self.bank_vars[bank].set(True)
            else:
                self.bank_vars[bank].set(False)
                cb.config(state="disabled")

        if manifest is None:
            self.no_preset_label.config(text="No preset selected yet.")
            self.no_preset_label.pack(fill="x")
            self.load_btn.config_state("disabled")
        else:
            self.no_preset_label.pack_forget()
            self.load_btn.config_state("normal")
        self._on_bank_checkbox_changed()

    def on_load(self):
        if not self.selected_preset_dir:
            return
        banks_to_load = [b for b, var in self.bank_vars.items() if var.get()]
        if not banks_to_load:
            dark_showerror("No Banks Selected", "Check at least one bank to load.", parent=self)
            return

        target_override = None
        if len(banks_to_load) == 1 and self.to_current_bank_var.get():
            target_override = self.app.current_bank.get()

        target_banks = [target_override] if target_override else banks_to_load
        will_overwrite = [b for b in target_banks if self.app.bank_has_samples(b)]
        if will_overwrite:
            proceed = dark_askyesno(
                "Overwrite Loaded Pads?",
                f"Bank(s) {', '.join(will_overwrite)} currently have samples loaded. "
                f"Loading this preset will replace them.\n\nContinue?",
                parent=self
            )
            if not proceed:
                return

        self.result_dir = self.selected_preset_dir
        self.result_banks = banks_to_load
        self.result_target_override = target_override
        self.destroy()

    def on_cancel(self):
        self.result_dir = None
        self.destroy()


class AudioPreviewDialog(FolderNavMixin, tk.Toplevel):
    def __init__(self, parent, initial_dir=None):
        super().__init__(parent)
        self.title("Select Sample (with Preview)")
        self.geometry(f"{AUDIO_PREVIEW_MIN_W}x{AUDIO_PREVIEW_MIN_H}")
        self.minsize(AUDIO_PREVIEW_MIN_W, AUDIO_PREVIEW_MIN_H)
        style_toplevel(self)
        self.selected_path = None        # RESULT - only ever set by on_confirm()
        self.selected_display_name = None
        self.preview_path = None         # what's merely highlighted in the list
        self.current_dir = initial_dir or os.path.expanduser("~")
        self.autoplay_var = tk.BooleanVar(value=load_default_autoplay())
        self._sort_column = None  # None = default (name), else "name"/"length"/"size"
        self._sort_reverse = False

        self._build_nav_bar(container_bg=BG_DARK)

        ensure_dark_treeview_style()
        list_frame = tk.Frame(self, padx=10, pady=6, bg=BG_DARK)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = ttk.Treeview(list_frame, columns=("length", "size"), show="tree headings",
                                     selectmode="browse", yscrollcommand=scrollbar.set,
                                     style="Dark.Treeview")
        self.listbox.heading("#0", text="Name", anchor="w", command=lambda: self._sort_by("name"))
        self.listbox.heading("length", text="Length", anchor="e", command=lambda: self._sort_by("length"))
        self.listbox.heading("size", text="Size", anchor="e", command=lambda: self._sort_by("size"))
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
        add_tooltip(autoplay_cb,
                    "Plays a sample as soon as you select it in the list. The default for "
                    "this is set in Settings.")

        self.normalize_var = tk.BooleanVar(value=False)
        normalize_cb = tk.Checkbutton(autoplay_row, text="Normalize", variable=self.normalize_var,
                                       command=self._render_wave_at_current_view)
        style_checkbutton(normalize_cb)
        normalize_cb.pack(side="left", padx=(12, 0))
        add_tooltip(normalize_cb,
                    "Lifts the sample to its maximum level without clipping. Applied when "
                    "you press \"Select\".")

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
        add_tooltip(self.wave_canvas,
                    "Drag the green (start) and red (end) markers to load only that "
                    "region onto the pad. \"Reset\" returns the zoom to the full view - "
                    "it does not move the markers back.")
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
        self.preview_btn = RoundedButton(btn_row, text="\u25b6 Preview", command=self.toggle_preview,
                                          bg=BTN_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=110)
        self.preview_btn.pack(side="left", padx=4)
        add_tooltip(self.preview_btn,
                    "Plays the marked region, or the whole sample if no markers are "
                    "set.\nShortcut: Space")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK)
        cancel_btn.pack(side="right", padx=4)
        select_btn = RoundedButton(btn_row, text="Select", command=self.on_confirm,
                                    bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK)
        select_btn.pack(side="right", padx=4)
        add_tooltip(select_btn,
                    "Loads the sample onto the pad. Trim and Normalize are written to a "
                    "new file in the temp folder - the original stays untouched.")

        self.bind("<space>", self._on_space_key)
        # Without this, closing via the window manager's X skips on_cancel():
        # playback would keep running and the pending playhead tick would
        # fire on an already-destroyed canvas.
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)

        self.refresh_list()
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _on_space_key(self, event):
        """Space plays the current sample - except while typing in a text
        field (address bar etc.), where it should just type a space."""
        if isinstance(self.focus_get(), (tk.Entry,)):
            return
        self.preview_selected()
        return "break"

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

    def _sort_by(self, column):
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self.refresh_list()

    def _update_sort_headers(self):
        def label(base, col):
            if self._sort_column == col:
                return base + (" \u25bc" if self._sort_reverse else " \u25b2")
            return base
        self.listbox.heading("#0", text=label("Name", "name"))
        self.listbox.heading("length", text=label("Length", "length"))
        self.listbox.heading("size", text=label("Size", "size"))

    def refresh_list(self):
        for item in self.listbox.get_children():
            self.listbox.delete(item)
        self._update_path_entry()
        try:
            entries = sorted(os.listdir(self.current_dir))
        except Exception as e:
            entries = []
            print(f"Could not read folder: {e}")

        folders, files = [], []
        for entry in entries:
            full = os.path.join(self.current_dir, entry)
            if os.path.isdir(full):
                folders.append(entry)
            elif entry.lower().endswith((".wav", ".mp3")):
                duration = get_audio_duration_seconds(full)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = None
                files.append((entry, duration, size))

        folders.sort(key=str.lower)
        if self._sort_column == "length":
            files.sort(key=lambda r: (r[1] is None, r[1] or 0), reverse=self._sort_reverse)
        elif self._sort_column == "size":
            files.sort(key=lambda r: (r[2] is None, r[2] or 0), reverse=self._sort_reverse)
        else:
            files.sort(key=lambda r: r[0].lower(), reverse=self._sort_reverse)

        self._entries = [".."]
        self.listbox.insert("", tk.END, iid="0", text="..")
        for entry in folders:
            idx = len(self._entries)
            self._entries.append(f"[Folder] {entry}")
            self.listbox.insert("", tk.END, iid=str(idx), text=f"[Folder] {entry}")
        for entry, duration, size in files:
            idx = len(self._entries)
            self._entries.append(entry)
            self.listbox.insert("", tk.END, iid=str(idx), text=entry,
                                 values=(format_duration(duration), format_size(size)))
        self._update_sort_headers()

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
            # NOT self.selected_path: that one is the dialog's RESULT and is
            # only allowed to be set by on_confirm(). Merely clicking a file
            # in the list used to set it, so closing the window with the
            # WM's X button (which never runs on_cancel) left it filled in
            # and the caller loaded that sample onto the pad anyway -
            # untrimmed, un-normalized, and with an undo step pushed.
            self.preview_path = os.path.join(self.current_dir, entry)
            self.show_waveform(self.preview_path)
            if self.autoplay_var.get():
                self.preview_selected()
        else:
            self.preview_path = None
            self.wave_canvas.delete("all")

    def show_waveform(self, path):
        wav_path = path
        if path.lower().endswith(".mp3") and PYDUB_AVAILABLE:
            try:
                sound = AudioSegment.from_file(path)
                wav_path = temp_path("waveform_src.wav")
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


    def _display_data_with_edits(self, data):
        """Returns a copy of the FULL (untrimmed) waveform data with
        Normalize applied only within the trim region - so the preview
        shows the amplitude you'll actually get if Normalize is checked
        when you confirm the selection."""
        if data is None or not self.normalize_var.get():
            return data
        n = len(data)
        start_i = int(self.trim_start_frac * n)
        end_i = int(self.trim_end_frac * n)
        region = data[start_i:end_i]
        if len(region) == 0:
            return data
        peak = float(np.max(np.abs(region))) if region.size else 0.0
        if peak <= 0:
            return data
        result = data.copy()
        result[start_i:end_i] = region * (0.98 / peak)
        return result

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
            display_stereo = self._display_data_with_edits(self._wave_data_stereo)
            half_h = self.wave_height / 2.0
            draw_waveform_on_canvas(self.wave_canvas, display_stereo[:, 0],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=0, clear=True)
            draw_waveform_on_canvas(self.wave_canvas, display_stereo[:, 1],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=half_h, clear=False)
            self.wave_canvas.create_line(0, half_h, self.wave_width, half_h,
                                          fill=BORDER_COLOR, width=1, tags="waveform")
        else:
            display_mono = self._display_data_with_edits(self._wave_data)
            draw_waveform_on_canvas(self.wave_canvas, display_mono, self.view_start_frac,
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
            if hasattr(self, "preview_btn"):
                self.preview_btn.text = "\u25b6 Preview"
                self.preview_btn._draw()
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
        try:
            trimmed_path = self.get_trimmed_export_path()
        except Exception as e:
            dark_showerror("Trim Error", f"Could not create the trimmed sample:\n{e}", parent=self)
            return

        result_path = trimmed_path if trimmed_path else original_path
        name_suffix = " (trim)" if trimmed_path else ""

        if self.normalize_var.get():
            try:
                result_path = normalize_wav_file(result_path)
                name_suffix += " (normalized)"
            except Exception as e:
                dark_showerror("Normalize Error", f"Could not normalize the sample:\n{e}", parent=self)
                return

        self.selected_path = result_path
        self.selected_display_name = f"{entry}{name_suffix}"

        self.stop_preview()
        self.destroy()

    def toggle_preview(self):
        if self.is_playing:
            self.stop_preview()
        else:
            self.preview_selected()

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
                tmp_preview = temp_path("preview_tmp.wav")
                sound.export(tmp_preview, format="wav")
                play_path = tmp_preview

            data, fs = sf.read(play_path, dtype="float32")
            n = len(data)
            start_i = int(self.trim_start_frac * n)
            end_i = int(self.trim_end_frac * n)
            segment = data[start_i:end_i]
            segment = apply_micro_fade(segment, fs, fade_ms=2)
            if self.normalize_var.get():
                peak = float(np.max(np.abs(segment))) if segment.size else 0.0
                if peak > 0:
                    segment = segment * (0.98 / peak)
            sd.play(segment, fs)

            self.is_playing = True
            self.preview_btn.text = "\u25a0 Stop"
            self.preview_btn._draw()
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
        if hasattr(self, "preview_btn"):
            self.preview_btn.text = "\u25b6 Preview"
            self.preview_btn._draw()
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
        self._sort_column = None           # Browse list sort state (Selected list is never sorted -
        self._sort_reverse = False         # only the real chop order matters there)

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
        self.listbox.heading("#0", text="Name", anchor="w", command=lambda: self._sort_by("name"))
        self.listbox.heading("length", text="Length", anchor="e", command=lambda: self._sort_by("length"))
        self.listbox.heading("size", text="Size", anchor="e", command=lambda: self._sort_by("size"))
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
        self.browse_preview_btn = RoundedButton(left_btn_row, text="\u25b6 Preview", command=self.toggle_preview,
                                                 bg=BTN_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=95, height=26)
        self.browse_preview_btn.pack(side="left", padx=2)
        add_tooltip(self.browse_preview_btn,
                    "Plays the sample selected on the left (only the marked region if you "
                    "moved the markers).\nShortcut: Space")
        add_btn = RoundedButton(left_btn_row, text="Add to Selection \u2192", command=self.add_selected,
                                 bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=160, height=26)
        add_btn.pack(side="right", padx=2)
        add_tooltip(add_btn,
                    "Moves the selected sample(s) into the chop order on the right. With "
                    "markers set, only the marked region is added - so you can pull "
                    "several regions out of one long file.")

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
        self.selected_listbox = ttk.Treeview(right_list_frame, columns=("no", "name", "length", "size"),
                                              show="headings", selectmode="extended",
                                              yscrollcommand=right_scrollbar.set, style="Dark.Treeview")
        self.selected_listbox.heading("no", text="No.", anchor="w")
        self.selected_listbox.heading("name", text="Name", anchor="w")
        self.selected_listbox.heading("length", text="Length", anchor="e")
        self.selected_listbox.heading("size", text="Size", anchor="e")
        self.selected_listbox.column("no", anchor="w", width=32, stretch=False)
        self.selected_listbox.column("name", anchor="w", width=148, stretch=True)
        self.selected_listbox.column("length", anchor="e", width=60, stretch=False)
        self.selected_listbox.column("size", anchor="e", width=65, stretch=False)
        self.selected_listbox.tag_configure("toolong", foreground=ACCENT_ORANGE)
        self.selected_listbox.pack(side="left", fill="both", expand=True)
        right_scrollbar.config(command=self.selected_listbox.yview)
        self.selected_listbox.bind("<Double-Button-1>", lambda e: self.preview_current())
        self.selected_listbox.bind("<<TreeviewSelect>>", self.on_selected_select)
        self.selected_listbox.bind("<Alt-Up>", lambda e: self.move_up())
        self.selected_listbox.bind("<Alt-Down>", lambda e: self.move_down())
        self.selected_listbox.bind("<Delete>", lambda e: self.remove_from_selection())
        self.selected_listbox.bind("<BackSpace>", lambda e: self.remove_from_selection())

        right_btn_row = tk.Frame(right, bg=BG_DARK)
        right_btn_row.pack(fill="x")
        move_up_btn = RoundedButton(right_btn_row, text="\u2191", command=self.move_up,
                                     bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=36, height=26)
        move_up_btn.pack(side="left", padx=2)
        add_tooltip(move_up_btn,
                    "Moves the selected entry one slice earlier. The order here is the "
                    "slice order on the P-6.\nShortcut: Alt+Up")
        move_down_btn = RoundedButton(right_btn_row, text="\u2193", command=self.move_down,
                                       bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=36, height=26)
        move_down_btn.pack(side="left", padx=2)
        add_tooltip(move_down_btn,
                    "Moves the selected entry one slice later.\nShortcut: Alt+Down")
        remove_btn = RoundedButton(right_btn_row, text="Remove", command=self.remove_from_selection,
                                    bg=BTN_RED, fg="#FFFFFF", parent_bg=BG_DARK, width=90, height=26)
        remove_btn.pack(side="right", padx=2)
        add_tooltip(remove_btn,
                    "Removes the selected entry from the chop order.\nShortcut: Del or "
                    "Backspace")

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
        add_tooltip(self.wave_canvas,
                    "Drag the green (start) and red (end) markers to mark a region. "
                    "The scrollbar appears once you are zoomed in. Markers only work on "
                    "samples from \"Browse\".")
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
                         text="Click a sample in \"Browse\", drag the green/red markers, then "
                              "\"Add to Selection\" \u2014 adds only the marked region. "
                              "Without markers the whole file is added.",
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
        add_tooltip(om1,
                    "How many equal slices the multisample is divided into. Each selected "
                    "sample fills one slice; unused slices stay silent. Fewer slices = "
                    "more time per slice.")

        lbl2 = tk.Label(opts, text="Sample Rate:")
        style_label(lbl2)
        lbl2.grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.rate_var = tk.IntVar(value=44100)
        om2 = RoundedDropdown(opts, self.rate_var, TARGET_RATES, parent_bg=BG_DARK, width=90,
                               command=lambda _v: self.on_options_changed())
        om2.grid(row=0, column=3, padx=6)
        add_tooltip(om2,
                    "Sample rate of the finished multisample. A lower rate means less "
                    "memory and a longer possible slice time, at the cost of high "
                    "frequencies.")

        self.stereo_var = tk.BooleanVar(value=False)
        self.stereo_cb = tk.Checkbutton(opts, text="Stereo", variable=self.stereo_var,
                                         command=self.on_options_changed)
        style_checkbutton(self.stereo_cb)
        self.stereo_cb.grid(row=0, column=4, padx=(16, 0))
        add_tooltip(self.stereo_cb,
                    "Builds the multisample in stereo. Mono halves the size and doubles "
                    "the possible slice time. Locks itself as soon as the list on the "
                    "right is not empty, so mono and stereo entries can't get mixed - "
                    "clear the selection to change it.")

        norm_lbl = tk.Label(opts, text="Normalize:")
        style_label(norm_lbl)
        norm_lbl.grid(row=0, column=5, sticky="w", padx=(16, 0))
        self.normalize_mode_var = tk.StringVar(value=NORMALIZE_MODES[0])
        norm_dd = RoundedDropdown(opts, self.normalize_mode_var, NORMALIZE_MODES,
                                   parent_bg=BG_DARK, width=110,
                                   command=lambda _v: self._on_normalize_mode_changed())
        norm_dd.grid(row=0, column=6, padx=6)
        add_tooltip(norm_dd,
                    "Off: levels stay as they are.\n"
                    "Per sample: every slice is lifted to full level on its own - use "
                    "this when the samples were recorded at different volumes.\n"
                    "Whole file: only the finished multisample is lifted, the balance "
                    "between the slices stays as it is.")

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
                                   bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=150)
        build_btn.pack(side="right", padx=4)
        add_tooltip(build_btn,
                    "Renders the chop order into a single WAV and loads it onto the pad "
                    "you started from. Samples longer than the slice time are truncated.")
        clear_btn = RoundedButton(btn_row, text="Clear Selection", command=self.clear_selection,
                                   bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=130)
        clear_btn.pack(side="left", padx=4)
        add_tooltip(clear_btn, "Empties the chop order on the right. Files are not deleted.")

        self.refresh_list()
        self.refresh_selected_list()
        self.update_limits_label()
        self.bind("<space>", self._on_space_key)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)  # X must stop playback too
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _normalize_mode(self):
        """Internal key ("off"/"per_sample"/"whole") for the Normalize
        dropdown, which itself holds the human-readable label."""
        return NORMALIZE_MODE_KEYS.get(self.normalize_mode_var.get(), "off")

    def _normalize_per_sample(self):
        """Whether the CURRENTLY shown sample is affected by Normalize.

        Only "Per sample" changes an individual sample, so this is what the
        waveform preview and the preview playback follow. In "Whole file"
        mode the individual sample is untouched and only the finished
        multisample gets lifted - showing it scaled here would promise a
        level this sample never actually has."""
        return self._normalize_mode() == "per_sample"

    def _on_normalize_mode_changed(self):
        self._render_wave_at_current_view()

    def _on_space_key(self, event):
        """Space plays the current sample - except while typing in a text
        field (address bar, preset name entry etc.), where it should just
        type a space."""
        if isinstance(self.focus_get(), (tk.Entry,)):
            return
        self.preview_current()
        return "break"

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

    def _sort_by(self, column):
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self.refresh_list()

    def _update_sort_headers(self):
        def label(base, col):
            if self._sort_column == col:
                return base + (" \u25bc" if self._sort_reverse else " \u25b2")
            return base
        self.listbox.heading("#0", text=label("Name", "name"))
        self.listbox.heading("length", text=label("Length", "length"))
        self.listbox.heading("size", text=label("Size", "size"))

    def refresh_list(self):
        for item in self.listbox.get_children():
            self.listbox.delete(item)
        self._update_path_entry()
        try:
            entries = sorted(os.listdir(self.current_dir))
        except Exception as e:
            entries = []
            print(f"Could not read folder: {e}")

        folders, files = [], []
        for entry in entries:
            full = os.path.join(self.current_dir, entry)
            if os.path.isdir(full):
                folders.append(entry)
            elif entry.lower().endswith((".wav", ".mp3")):
                duration = get_audio_duration_seconds(full)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = None
                files.append((entry, duration, size))

        folders.sort(key=str.lower)
        if self._sort_column == "length":
            files.sort(key=lambda r: (r[1] is None, r[1] or 0), reverse=self._sort_reverse)
        elif self._sort_column == "size":
            files.sort(key=lambda r: (r[2] is None, r[2] or 0), reverse=self._sort_reverse)
        else:
            files.sort(key=lambda r: r[0].lower(), reverse=self._sort_reverse)

        self._entries = [".."]
        self.listbox.insert("", tk.END, iid="0", text="..")
        for entry in folders:
            idx = len(self._entries)
            self._entries.append(f"[Folder] {entry}")
            self.listbox.insert("", tk.END, iid=str(idx), text=f"[Folder] {entry}")
        for entry, duration, size in files:
            idx = len(self._entries)
            self._entries.append(entry)
            self.listbox.insert("", tk.END, iid=str(idx), text=entry,
                                 values=(format_duration(duration), format_size(size)))
        self._update_sort_headers()

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
        if limit and num_slices > 0:
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
                "", tk.END, iid=str(i),
                values=(i + 1, display_name, format_duration(duration), format_size(size)),
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
                wav_path = temp_path("waveform_src.wav")
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

    def _display_data_with_edits(self, data):
        """Returns a copy of the FULL (untrimmed) waveform data with the
        per-sample normalization applied inside the trim region, so the
        preview shows the level this slice will really have in the built
        multisample.

        The old version keyed off a plain Normalize checkbox that, at build
        time, actually normalized the COMBINED file - so the preview showed
        a per-sample result the build never produced. It now follows
        _normalize_per_sample() and stays flat in "Whole file" mode."""
        if data is None or not self._normalize_per_sample():
            return data
        n = len(data)
        start_i = int(self.trim_start_frac * n)
        end_i = int(self.trim_end_frac * n)
        region = data[start_i:end_i]
        if len(region) == 0:
            return data
        peak = float(np.max(np.abs(region))) if region.size else 0.0
        if peak <= 0:
            return data
        result = data.copy()
        result[start_i:end_i] = region * (0.98 / peak)
        return result

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
            display_stereo = self._display_data_with_edits(self._wave_data_stereo)
            half_h = self.wave_height / 2.0
            draw_waveform_on_canvas(self.wave_canvas, display_stereo[:, 0],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=0, clear=True)
            draw_waveform_on_canvas(self.wave_canvas, display_stereo[:, 1],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=half_h, clear=False)
            self.wave_canvas.create_line(0, half_h, self.wave_width, half_h,
                                          fill=BORDER_COLOR, width=1, tags="waveform")
        else:
            display_mono = self._display_data_with_edits(self._wave_data)
            draw_waveform_on_canvas(self.wave_canvas, display_mono, self.view_start_frac,
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
        """Shows the part of a slice that will be cut off for exceeding the
        per-slice time limit. Thin wrapper over the app-wide helper so the
        Chop view keeps the exact same look as the other waveform views."""
        draw_truncate_overlay(self.wave_canvas, x_cut, x_right, self.wave_height)

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
            if hasattr(self, "browse_preview_btn"):
                self.browse_preview_btn.text = "\u25b6 Preview"
                self.browse_preview_btn._draw()
            self.wave_canvas.delete("playhead")

    def toggle_preview(self):
        if self.is_playing:
            self.stop_preview()
        else:
            self.preview_current()

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
                if self._normalize_per_sample():
                    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
                    if peak > 0:
                        samples = samples * (0.98 / peak)
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
                if self._normalize_per_sample():
                    peak = float(np.max(np.abs(segment))) if segment.size else 0.0
                    if peak > 0:
                        segment = segment * (0.98 / peak)
                sd.play(segment, fs)

            self.is_playing = True
            if hasattr(self, "browse_preview_btn"):
                self.browse_preview_btn.text = "\u25a0 Stop"
                self.browse_preview_btn._draw()
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
        if hasattr(self, "browse_preview_btn"):
            self.browse_preview_btn.text = "\u25b6 Preview"
            self.browse_preview_btn._draw()
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
                                        normalize_mode=self._normalize_mode())

            unique_id = uuid.uuid4().hex[:8]
            out_name = f"chop_{num_slices}slices_{rate}Hz_{'stereo' if channels == 2 else 'mono'}_{unique_id}.wav"
            out_path = temp_path(out_name)
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


class ClearBanksDialog(tk.Toplevel):
    """Pick which banks to empty. Mirrors the Copy Banks dialog's layout on
    purpose - same checkbox grid, same All/None buttons - so the two
    bank-selection flows behave identically.

    Only the active bank starts checked: clearing is bulk destruction of
    work, so defaulting to "everything with samples in it" (as the copy
    dialog does, where that's harmless) would be the wrong way round."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Clear Banks")
        self.geometry("420x380")
        self.minsize(420, 380)
        style_toplevel(self)
        self.confirmed = False

        outer = tk.Frame(self, bg=BG_DARK, padx=16, pady=16)
        outer.pack(fill="both", expand=True)

        banks_panel = RoundedPanel(outer, title="Banks to Clear", parent_bg=BG_DARK,
                                    panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                    title_fg=ACCENT_BLUE)
        banks_panel.pack(fill="x", pady=(0, 12))

        active = self.app.current_bank.get()
        self.bank_vars = {}
        grid = tk.Frame(banks_panel.body, bg=BG_PANEL)
        grid.pack(fill="x", pady=(8, 6))
        for i, bank in enumerate(BANKS):
            var = tk.BooleanVar(value=(bank == active))
            var.trace_add("write", lambda *a: self._update_summary())
            self.bank_vars[bank] = var
            suffix = "  (active)" if bank == active else ""
            if not self.app.bank_has_samples(bank):
                suffix += "  \u2013 empty"
            cb = tk.Checkbutton(grid, text=f"Bank {bank}{suffix}", variable=var)
            style_checkbutton(cb)
            cb.config(bg=BG_PANEL, activebackground=BG_PANEL)
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=6, pady=2)

        select_row = tk.Frame(banks_panel.body, bg=BG_PANEL)
        select_row.pack(fill="x", pady=(4, 8))
        all_btn = RoundedButton(select_row, text="All", command=lambda: self._set_all(True),
                                 bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=70, height=24,
                                 font=(UI_FAMILY, 8))
        all_btn.pack(side="left", padx=(0, 4))
        none_btn = RoundedButton(select_row, text="None", command=lambda: self._set_all(False),
                                  bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=70, height=24,
                                  font=(UI_FAMILY, 8))
        none_btn.pack(side="left")

        info_panel = RoundedPanel(outer, title="What This Does", parent_bg=BG_DARK,
                                   panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                   title_fg=ACCENT_BLUE)
        info_panel.pack(fill="x", pady=(0, 12))
        self.summary_label = tk.Label(info_panel.body, anchor="w", justify="left",
                                       wraplength=350, text="")
        style_label(self.summary_label, bg=BG_PANEL, font=(UI_FAMILY, 9))
        self.summary_label.pack(fill="x", pady=(8, 4))
        hint = tk.Label(info_panel.body, anchor="w", justify="left", wraplength=350,
                         text="Only the pads in the app are emptied - no files on disk or on "
                              "the P-6 are touched. Undo (Ctrl+Z) restores everything.")
        style_label(hint, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        hint.pack(fill="x", pady=(0, 8))

        btn_row = tk.Frame(outer, bg=BG_DARK)
        btn_row.pack(fill="x", side="bottom")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        cancel_btn.pack(side="right", padx=4)
        self.clear_btn = RoundedButton(btn_row, text="Clear", command=self.on_clear,
                                        bg=BTN_ORANGE, fg="#FFFFFF", parent_bg=BG_DARK, width=90)
        self.clear_btn.pack(side="right", padx=4)

        self._update_summary()
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _set_all(self, value):
        for var in self.bank_vars.values():
            var.set(value)

    def selected_banks(self):
        return [b for b, var in self.bank_vars.items() if var.get()]

    def _update_summary(self):
        banks = self.selected_banks()
        with_samples = [b for b in banks if self.app.bank_has_samples(b)]
        if not banks:
            self.summary_label.config(text="No banks selected.", fg=FG_MUTED)
        elif not with_samples:
            self.summary_label.config(
                text=f"{', '.join(banks)} selected - all of them are already empty.",
                fg=FG_MUTED)
        else:
            pad_word = "bank" if len(with_samples) == 1 else "banks"
            self.summary_label.config(
                text=f"Clears {len(with_samples)} loaded {pad_word}: "
                     f"{', '.join(with_samples)}.", fg=ACCENT_ORANGE)
        if hasattr(self, "clear_btn"):
            self.clear_btn.config_state("normal" if banks else "disabled")

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

    def on_clear(self):
        if not self.selected_banks():
            return
        self.confirmed = True
        self.destroy()

    def on_cancel(self):
        self.confirmed = False
        self.destroy()


class CopyBanksDialog(tk.Toplevel):
    """Pick which banks to export, with a live total-size readout that
    updates as banks are checked/unchecked - so you can see up front how
    much you're about to transfer, before committing to it."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Copy Banks to P6")
        self.geometry("420x420")
        self.minsize(420, 420)
        style_toplevel(self)
        self.confirmed = False

        outer = tk.Frame(self, bg=BG_DARK, padx=16, pady=16)
        outer.pack(fill="both", expand=True)

        banks_panel = RoundedPanel(outer, title="Banks to Copy", parent_bg=BG_DARK,
                                    panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                    title_fg=ACCENT_BLUE)
        banks_panel.pack(fill="x", pady=(0, 12))

        self.bank_vars = {}
        grid = tk.Frame(banks_panel.body, bg=BG_PANEL)
        grid.pack(fill="x", pady=(8, 6))
        for i, bank in enumerate(BANKS):
            has_samples = self.app.bank_has_samples(bank)
            var = tk.BooleanVar(value=has_samples)
            var.trace_add("write", lambda *a: self._update_total())
            self.bank_vars[bank] = var
            cb = tk.Checkbutton(grid, text=f"Bank {bank}", variable=var)
            style_checkbutton(cb)
            cb.config(bg=BG_PANEL, activebackground=BG_PANEL)
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=6, pady=2)

        select_row = tk.Frame(banks_panel.body, bg=BG_PANEL)
        select_row.pack(fill="x", pady=(4, 8))
        all_btn = RoundedButton(select_row, text="All", command=lambda: self._set_all(True),
                                 bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=70, height=24,
                                 font=(UI_FAMILY, 8))
        all_btn.pack(side="left", padx=(0, 4))
        none_btn = RoundedButton(select_row, text="None", command=lambda: self._set_all(False),
                                  bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=70, height=24,
                                  font=(UI_FAMILY, 8))
        none_btn.pack(side="left")

        total_panel = RoundedPanel(outer, title="Total to Transfer", parent_bg=BG_DARK,
                                    panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                    title_fg=ACCENT_BLUE)
        total_panel.pack(fill="x", pady=(0, 12))
        self.total_label = tk.Label(total_panel.body, text="", anchor="w")
        style_label(self.total_label, bg=BG_PANEL, font=(UI_FAMILY, 14, "bold"))
        self.total_label.pack(fill="x", pady=(8, 8))

        btn_row = tk.Frame(outer, bg=BG_DARK)
        btn_row.pack(fill="x", side="bottom")
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        cancel_btn.pack(side="right", padx=4)
        self.copy_btn = RoundedButton(btn_row, text="Copy", command=self.on_copy,
                                       bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=90)
        self.copy_btn.pack(side="right", padx=4)

        self._update_total()
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _set_all(self, value):
        for var in self.bank_vars.values():
            var.set(value)

    def selected_banks(self):
        return [b for b, var in self.bank_vars.items() if var.get()]

    def _update_total(self):
        banks = self.selected_banks()
        total_bytes = sum(self.app._bank_size_bytes(b) for b in banks)
        mb = total_bytes / (1024 * 1024)
        limit_mb = MAX_UPLOAD_BYTES / (1024 * 1024)
        over = total_bytes > MAX_UPLOAD_BYTES
        color = ACCENT_RED if over else FG_TEXT
        n = len(banks)
        bank_word = "bank" if n == 1 else "banks"
        self.total_label.config(
            text=f"{mb:.2f} MB across {n} {bank_word}" + (f"  (over {limit_mb:.0f} MB!)" if over else ""),
            fg=color)
        if hasattr(self, "copy_btn"):
            self.copy_btn.config_state("normal" if banks else "disabled")

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

    def on_copy(self):
        if not self.selected_banks():
            return
        self.confirmed = True
        self.destroy()

    def on_cancel(self):
        self.confirmed = False
        self.destroy()


class PadWaveformViewDialog(tk.Toplevel):
    """Post-hoc editing view for a pad's already-loaded sample: trim
    markers + zoom, the same interaction as the Load dialog's waveform
    pane, plus Normalize. "Apply to Pad" writes the edited result back
    onto the exact pad this was opened from (with undo), rather than
    "Select"-ing it somewhere else the way the Load dialog does."""

    def __init__(self, parent, app, pad_num, filepath, display_name):
        super().__init__(parent)
        self.app = app
        self.pad_num = pad_num
        self.filepath = filepath
        self.base_display_name = display_name
        # Chop-built multisamples have precise, fixed slice boundaries that
        # the P-6 relies on - trimming or fading them can shift/clip a slice
        # boundary and cause playback problems on the actual hardware, even
        # though it looks fine in this editor. Detected via the filename
        # prefix our own Chop export always uses (chop_<slices>slices_...).
        self.is_chop_sample = os.path.basename(filepath).lower().startswith("chop_")
        self.title(display_name)
        self.geometry(f"{MAIN_MIN_W}x570")
        self.minsize(700, 470)
        style_toplevel(self)

        self.trim_start_frac = 0.0
        self.trim_end_frac = 1.0
        self.drag_target = None
        self.is_playing = False
        self.play_start_time = None
        self.play_duration = 0.0
        self._wave_data = None
        self._wave_data_stereo = None
        self._wave_fs = None
        self.zoom_factor = 1.0
        self.view_start_frac = 0.0
        self.view_span_frac = 1.0
        self.center_frac = 0.5
        self.wave_width = 800
        self.wave_height = 300

        header = tk.Frame(self, padx=16, pady=12, bg=BG_DARK)
        header.pack(fill="x")
        name_label = tk.Label(header, text=display_name, anchor="w")
        style_label(name_label, font=(UI_FAMILY, 11, "bold"))
        name_label.pack(side="left")
        self.info_label = tk.Label(header, text="", anchor="e")
        style_label(self.info_label, fg=FG_MUTED, font=(UI_FAMILY, 9))
        self.info_label.pack(side="right")

        # Logarithmic fade-time steps (fast at the short end, where the ear
        # is most sensitive to small changes; coarser toward 1s) rather than
        # fixed linear increments.
        self.FADE_STEPS = [0.0, 0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.7, 1.0]
        self.fade_in_seconds = 0.0
        self.fade_out_seconds = 0.0
        controls_row = tk.Frame(self, padx=16, bg=BG_DARK)
        controls_row.pack(fill="x")

        # Zoom pinned to the right - packed first so it claims its space
        # on that side regardless of how wide the left-hand group grows.
        zoom_row = tk.Frame(controls_row, bg=BG_DARK)
        zoom_row.pack(side="right")
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

        # Normalize + Fade on the left - "shape the level/edges of the
        # selection" controls, grouped together and read as one unit.
        self.normalize_var = tk.BooleanVar(value=False)
        normalize_cb = tk.Checkbutton(controls_row, text="Normalize", variable=self.normalize_var,
                                       command=self._render_wave_at_current_view)
        style_checkbutton(normalize_cb)
        normalize_cb.pack(side="left")
        add_tooltip(normalize_cb,
                    "Lifts the sample to its maximum level without clipping. Shown live "
                    "in the waveform, written on \"Apply to Pad\".")

        fade_in_lbl = tk.Label(controls_row, text="Fade In:")
        style_label(fade_in_lbl, font=(UI_FAMILY, 9))
        fade_in_lbl.pack(side="left", padx=(20, 0))
        self.fade_in_minus = RoundedButton(controls_row, text="\u2212", command=lambda: self._adjust_fade("in", -1),
                                            bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                            width=28, height=22, font=(UI_FAMILY, 10, "bold"))
        self.fade_in_minus.pack(side="left", padx=(6, 1))
        self.fade_in_label = tk.Label(controls_row, text="0.00s", width=6)
        style_label(self.fade_in_label, bg=BG_DARK, fg=FG_MUTED, font=(UI_FAMILY, 8, "bold"))
        self.fade_in_label.pack(side="left", padx=4)
        self.fade_in_plus = RoundedButton(controls_row, text="+", command=lambda: self._adjust_fade("in", 1),
                                           bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                           width=28, height=22, font=(UI_FAMILY, 10, "bold"))
        self.fade_in_plus.pack(side="left", padx=1)

        fade_out_lbl = tk.Label(controls_row, text="Fade Out:")
        style_label(fade_out_lbl, font=(UI_FAMILY, 9))
        fade_out_lbl.pack(side="left", padx=(20, 0))
        self.fade_out_minus = RoundedButton(controls_row, text="\u2212", command=lambda: self._adjust_fade("out", -1),
                                             bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                             width=28, height=22, font=(UI_FAMILY, 10, "bold"))
        self.fade_out_minus.pack(side="left", padx=(6, 1))
        self.fade_out_label = tk.Label(controls_row, text="0.00s", width=6)
        style_label(self.fade_out_label, bg=BG_DARK, fg=FG_MUTED, font=(UI_FAMILY, 8, "bold"))
        self.fade_out_label.pack(side="left", padx=4)
        self.fade_out_plus = RoundedButton(controls_row, text="+", command=lambda: self._adjust_fade("out", 1),
                                            bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                            width=28, height=22, font=(UI_FAMILY, 10, "bold"))
        self.fade_out_plus.pack(side="left", padx=1)

        if self.is_chop_sample:
            fade_help = ("Disabled for chop multisamples: a fade would shift the fixed "
                         "slice boundaries the P-6 relies on.")
        else:
            fade_help = ("Fades the marked region in/out, 0 to 1.0 s in logarithmic "
                         "steps. Useful against clicks at the start or end.")
        for _fade_btn in (self.fade_in_minus, self.fade_in_plus,
                          self.fade_out_minus, self.fade_out_plus):
            add_tooltip(_fade_btn, fade_help)

        duration_row = tk.Frame(self, padx=16, bg=BG_DARK)
        duration_row.pack(fill="x")
        if self.is_chop_sample:
            chop_hint = tk.Label(
                duration_row,
                text="Chop sample: trim and fade are disabled (would risk breaking slice "
                     "playback on the P-6). Normalize is still available.",
                anchor="w")
            style_label(chop_hint, fg=ACCENT_ORANGE, font=(UI_FAMILY, 8))
            chop_hint.pack(side="left")
            self.fade_in_minus.config_state("disabled")
            self.fade_in_plus.config_state("disabled")
            self.fade_out_minus.config_state("disabled")
            self.fade_out_plus.config_state("disabled")
        self.duration_label = tk.Label(duration_row, text="", anchor="e")
        style_label(self.duration_label, fg=FG_MUTED, font=(UI_FAMILY, 8))
        self.duration_label.pack(side="right")

        wave_frame = tk.Frame(self, padx=16, bg=BG_DARK)
        wave_frame.pack(fill="both", expand=True)
        self.wave_canvas = tk.Canvas(wave_frame, bg=WAVE_BG, highlightthickness=0, bd=0,
                                      cursor="sb_h_double_arrow")
        self.wave_canvas.pack(fill="both", expand=True, pady=(8, 2))
        if self.is_chop_sample:
            add_tooltip(self.wave_canvas,
                        "Chop multisample: the trim markers are disabled so the slice "
                        "boundaries stay intact.")
        else:
            add_tooltip(self.wave_canvas,
                        "Drag the green (start) and red (end) markers to shorten the "
                        "sample. An orange area marks what the P-6 would cut off at the "
                        "current rate and pitch.")
        self.wave_canvas.bind("<Configure>", self._on_wave_canvas_resize)
        self.wave_canvas.bind("<ButtonPress-1>", self.on_wave_press)
        self.wave_canvas.bind("<B1-Motion>", self.on_wave_drag)
        self.wave_canvas.bind("<ButtonRelease-1>", self.on_wave_release)
        self.wave_canvas.bind("<MouseWheel>", self.on_wave_mousewheel)
        self.wave_canvas.bind("<Button-4>", self.on_wave_mousewheel)
        self.wave_canvas.bind("<Button-5>", self.on_wave_mousewheel)
        self.wave_scrollbar = tk.Scrollbar(wave_frame, orient="horizontal", command=self.on_wave_scroll)
        # Not packed here on purpose - only shown once zoomed in.

        btn_row = tk.Frame(self, padx=16, pady=12, bg=BG_DARK)
        btn_row.pack(fill="x")
        self.play_btn = RoundedButton(btn_row, text="\u25b6 Preview", command=self.toggle_play,
                                       bg=BTN_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=110)
        self.play_btn.pack(side="left")
        add_tooltip(self.play_btn,
                    "Plays the marked region with the current edits.\nShortcut: Space")
        close_btn = RoundedButton(btn_row, text="Close", command=self.on_close,
                                   bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        close_btn.pack(side="right")
        add_tooltip(close_btn, "Closes the editor. Unapplied changes are discarded.")
        apply_btn = RoundedButton(btn_row, text="Apply to Pad", command=self.apply_changes,
                                   bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=130)
        apply_btn.pack(side="right", padx=(0, 8))
        add_tooltip(apply_btn,
                    "Writes trim, normalize and fade to a new file in the temp folder and "
                    "puts it back on this pad. The original file stays untouched; "
                    "Ctrl+Z undoes it.")

        self.bind("<space>", lambda e: self.toggle_play())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._load_wave_data()
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

    def _load_wave_data(self):
        try:
            data, fs = sf.read(self.filepath, dtype="float32")
            self._wave_fs = fs
            rate, force_mono, pitch_cents = self._get_pad_settings()
            # Follow the pad's mono setting, not just the file's channel
            # count. The main window's mini waveform already collapses a
            # stereo file to a single trace when Mono (or the bank's Force
            # Mono) is on; the editor showed two stacked channels for the
            # same pad, which looked like the two views disagreed about the
            # sample. What gets exported is mono, so that's what's drawn.
            if data.ndim > 1 and data.shape[1] >= 2 and not force_mono:
                self._wave_data_stereo = data
                self._wave_data = data.mean(axis=1)
            else:
                if data.ndim > 1:
                    self._wave_data = data.mean(axis=1) if data.shape[1] >= 2 else data.flatten()
                else:
                    self._wave_data = data
                self._wave_data_stereo = None
            self.play_duration = len(self._wave_data) / fs if fs else 0
            # The file's own rate AND the pad's export settings. Showing only
            # "44100 Hz" here read as if the editor ignored the pad's rate
            # dropdown, when in fact the orange length-limit overlay is
            # computed from exactly those pad settings, not from the file.
            channel_word = "mono" if force_mono else "as-is"
            pitch_note = f", {pitch_cents:+d}c" if pitch_cents else ""
            self.info_label.config(
                text=f"{format_duration(self.play_duration)}  \u2022  file {fs} Hz  "
                     f"\u2022  pad export: {rate} Hz, {channel_word}{pitch_note}")
            self.render_and_draw_wave()
        except Exception as e:
            # Whatever fails here, it must not prevent __init__ from
            # reaching transient()/center_toplevel_on_parent()/_safe_grab()
            # below - otherwise the window is created but never properly
            # positioned/shown, which looks like an empty black rectangle.
            dark_showerror("Could Not Load Sample", str(e), parent=self)

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
        self._update_view_window()
        self._render_wave_at_current_view()

    def _on_wave_canvas_resize(self, event=None):
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

    def _display_data_with_edits(self, data):
        """Returns a copy of the FULL (untrimmed) waveform data with
        normalize + fade-in/fade-out applied only within the trim region -
        in the same order apply_changes() actually writes them (normalize
        first, then fade, so fading down the edges doesn't distort what
        normalize would have used as the peak). Everything outside the
        trim markers (already shown dimmed) stays untouched."""
        if data is None or self._wave_fs is None:
            return data
        normalize = self.normalize_var.get()
        has_fade = self.fade_in_seconds > 0 or self.fade_out_seconds > 0
        if not normalize and not has_fade:
            return data
        n = len(data)
        start_i = int(self.trim_start_frac * n)
        end_i = int(self.trim_end_frac * n)
        region = data[start_i:end_i]
        if len(region) == 0:
            return data
        if normalize:
            peak = float(np.max(np.abs(region))) if region.size else 0.0
            if peak > 0:
                region = region * (0.98 / peak)
        if has_fade:
            region = apply_fade_envelope(region, self._wave_fs, self.fade_in_seconds, self.fade_out_seconds)
        result = data.copy()
        result[start_i:end_i] = region
        return result

    def _render_wave_at_current_view(self):
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
            display_stereo = self._display_data_with_edits(self._wave_data_stereo)
            half_h = self.wave_height / 2.0
            draw_waveform_on_canvas(self.wave_canvas, display_stereo[:, 0],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=0, clear=True)
            draw_waveform_on_canvas(self.wave_canvas, display_stereo[:, 1],
                                     self.view_start_frac, end_frac, self.wave_width, half_h,
                                     tag="waveform", y_offset=half_h, clear=False)
            self.wave_canvas.create_line(0, half_h, self.wave_width, half_h,
                                          fill=BORDER_COLOR, width=1, tags="waveform")
        else:
            display_mono = self._display_data_with_edits(self._wave_data)
            draw_waveform_on_canvas(self.wave_canvas, display_mono, self.view_start_frac,
                                     end_frac, self.wave_width, self.wave_height)
        # Overlay FIRST, markers on top. The other way round (which is how
        # this used to run) painted the shaded region straight over the red
        # end marker, which is exactly where the two most often coincide -
        # the Chop view already drew them in this order.
        self._redraw_length_limit_overlay()
        self.redraw_markers()

    def _get_pad_settings(self):
        """(rate, force_mono, pitch_cents) currently configured for the pad
        this sample lives on - used to compute the same export duration
        limit the app would actually enforce."""
        slot = self.app.pad_widgets.get(self.pad_num) if hasattr(self.app, "pad_widgets") else None
        if slot is None:
            return 44100, False, 0
        return slot.target_rate.get(), slot.effective_mono(), slot.pitch_cents.get()

    def _redraw_length_limit_overlay(self):
        """Shades the portion of the waveform that would exceed the P-6's
        length limit for this pad's current rate/mono/pitch settings -
        same visual language as the main window's truncation overlay, so
        it's clear at a glance how much needs to be trimmed to fit."""
        self.wave_canvas.delete("lenlimit")
        if not self.play_duration:
            return
        rate, force_mono, pitch_cents = self._get_pad_settings()
        # Was computing its own ch_key as "1 if force_mono else 2", which
        # ignored that a MONO SOURCE FILE already gets the mono limit even
        # with Force Mono off - so a mono sample was shaded against the
        # stereo limit (half the length) and disagreed with both the pad
        # warning and the main waveform. Now everything asks the same helper.
        limit_frac = compute_truncate_fraction(self.filepath, rate, pitch_cents, force_mono)
        if limit_frac is None:
            return
        x_cut = self.frac_to_x(limit_frac)
        if x_cut < 0 or x_cut > self.wave_width:
            return  # the cut point is outside the currently zoomed-in view
        draw_truncate_overlay(self.wave_canvas, x_cut, self.wave_width,
                               self.wave_height, tag="lenlimit")


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

    def _adjust_fade(self, which, direction):
        """Moves one step along FADE_STEPS (logarithmic: fine-grained near
        0.01s, coarser toward 1s) rather than a fixed linear increment."""
        current = self.fade_in_seconds if which == "in" else self.fade_out_seconds
        idx = min(range(len(self.FADE_STEPS)), key=lambda i: abs(self.FADE_STEPS[i] - current))
        idx = max(0, min(idx + direction, len(self.FADE_STEPS) - 1))
        new_value = self.FADE_STEPS[idx]
        if which == "in":
            self.fade_in_seconds = new_value
            self.fade_in_label.config(text=f"{new_value:.2f}s")
        else:
            self.fade_out_seconds = new_value
            self.fade_out_label.config(text=f"{new_value:.2f}s")
        self._render_wave_at_current_view()

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
        if self.is_chop_sample:
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
        if not self.drag_target:
            return
        frac = max(0.0, min(self.x_to_frac(event.x), 1.0))
        if self.drag_target == "start":
            self.trim_start_frac = min(frac, self.trim_end_frac - 0.01)
        elif self.drag_target == "end":
            self.trim_end_frac = max(frac, self.trim_start_frac + 0.01)
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
            self.wave_canvas.create_line(x, 0, x, self.wave_height, fill=ACCENT_BLUE, width=2,
                                          tags="playhead")
        if frac_in_region < 1.0:
            self.after(30, self.update_playhead)
        else:
            self.is_playing = False
            self.play_btn.text = "\u25b6 Preview"
            self.play_btn._draw()
            self.wave_canvas.delete("playhead")

    def toggle_play(self):
        if self.is_playing:
            self.stop_play()
            return
        if self._wave_data is None:
            return
        try:
            sd.stop()
            data, fs = sf.read(self.filepath, dtype="float32")
            n = len(data)
            start_i = int(self.trim_start_frac * n)
            end_i = int(self.trim_end_frac * n)
            segment = data[start_i:end_i]
            segment = apply_micro_fade(segment, fs, fade_ms=2)
            if self.normalize_var.get():
                peak = float(np.max(np.abs(segment))) if segment.size else 0.0
                if peak > 0:
                    segment = segment * (0.98 / peak)
            segment = apply_fade_envelope(segment, fs, self.fade_in_seconds, self.fade_out_seconds)
            sd.play(segment, fs)
            self.is_playing = True
            self.play_btn.text = "\u25a0 Stop"
            self.play_btn._draw()
            self.play_start_time = time.time()
            self.update_playhead()
        except Exception as e:
            dark_showerror("Playback Error", str(e), parent=self)

    def stop_play(self):
        try:
            sd.stop()
        except Exception:
            pass
        self.is_playing = False
        if hasattr(self, "play_btn"):
            self.play_btn.text = "\u25b6 Preview"
            self.play_btn._draw()
        if hasattr(self, "wave_canvas"):
            self.wave_canvas.delete("playhead")

    def apply_changes(self):
        trimmed = self.trim_start_frac > 0.001 or self.trim_end_frac < 0.999
        normalize = self.normalize_var.get()
        has_fade = self.fade_in_seconds > 0.0 or self.fade_out_seconds > 0.0
        if self.is_chop_sample:
            # Defense in depth - the UI already blocks marker dragging and
            # disables the fade buttons, but this makes sure neither can
            # ever be applied to a chop sample regardless of how trim_*_frac
            # or fade_*_seconds ended up set.
            trimmed = False
            has_fade = False
        if not trimmed and not normalize and not has_fade:
            dark_showinfo("Nothing to Apply", "No trim, fade or normalize changes were made.", parent=self)
            return
        try:
            result_path = self.filepath
            if trimmed:
                result_path = trim_wav_file(result_path, self.trim_start_frac, self.trim_end_frac)
            if normalize:
                result_path = normalize_wav_file(result_path)
            if has_fade:
                result_path = apply_fade_to_wav_file(result_path, self.fade_in_seconds, self.fade_out_seconds)
        except Exception as e:
            dark_showerror("Edit Error", str(e), parent=self)
            return

        suffix = ""
        if trimmed:
            suffix += " (trim)"
        if normalize:
            suffix += " (normalized)"
        if has_fade:
            suffix += " (fade)"
        new_display_name = f"{self.base_display_name}{suffix}"

        slot = self.app.pad_widgets.get(self.pad_num)
        if slot is not None:
            self.app._push_undo()
            # keep_settings: this replaces the pad's sample with an edited
            # version of itself, so the rate/pitch/mono chosen for this pad
            # must survive. Editing used to silently reset all three.
            slot.set_file(result_path, display_name=new_display_name, keep_settings=True)
            self.app.update_storage_display()
            self.app.update_pad_warnings()
            self.app.show_status(f"PAD_{self.pad_num} updated.")
        self.stop_play()
        self.destroy()

    def on_close(self):
        self.stop_play()
        self.destroy()


class ImportBankDialog(tk.Toplevel):
    """Walks the user through the P-6's own "export to computer" hardware
    procedure, then imports whatever bank ends up in the resulting EXPORT
    folder onto the app's currently active bank.

    Folder layout: confirmed against real hardware, the device writes

        EXPORT/BANK_<letter>/PAD_<n>/<name>.WAV

    i.e. it mirrors the IMPORT side, with a bank level in between. The
    scan below was originally written before that was known and only
    handled a flat "PAD_1.WAV" or a "PAD_n/*.wav" directly under the
    selected folder - so picking the drive, or even the EXPORT folder
    itself, found nothing and left Import greyed out. It now walks down
    through the EXPORT and BANK_x levels on its own, and still accepts
    being pointed straight at the folder that holds the pads.

    PRM files aren't parsed (undocumented, proprietary format) - only the
    audio itself is imported; rate/pitch/mono come in at their defaults."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Bank from P6")
        self.geometry("560x520")
        self.minsize(560, 520)
        style_toplevel(self)
        self.selected_folder = None

        outer = tk.Frame(self, bg=BG_DARK, padx=18, pady=16)
        outer.pack(fill="both", expand=True)

        steps_panel = RoundedPanel(outer, title="On the P-6 itself", parent_bg=BG_DARK,
                                    panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                    title_fg=ACCENT_BLUE)
        steps_panel.pack(fill="x", pady=(0, 12))
        current_bank = self.app.current_bank.get()
        steps_text = (
            f"1. Connect the P-6 to this computer via USB.\n"
            f"2. Power it OFF first if it's already on.\n"
            f"3. Turn the power ON while holding the bank button "
            f"([A/E]-[D/H]) for the bank you want to bring in - here, "
            f"that's the button for Bank {current_bank}. For banks E-H, "
            f"also hold the SAMPLING button at the same time.\n"
            f"4. Wait about a minute - the step buttons light up on the "
            f"device to show it's getting the drive ready.\n"
            f"5. A \u201cP-6\u201d drive appears on this computer, containing "
            f"an \u201cEXPORT\u201d folder with that bank's samples."
        )
        steps_label = tk.Label(steps_panel.body, text=steps_text, anchor="w", justify="left",
                                wraplength=500)
        style_label(steps_label, bg=BG_PANEL, font=(UI_FAMILY, 9))
        steps_label.pack(fill="x", pady=(6, 0))

        warning_label = tk.Label(
            outer,
            text=f"Importing will overwrite Bank {current_bank} in the app (undo covers this "
                 f"afterward). Per-pad rate/pitch/mono settings aren't stored in the export, so "
                 f"they come in at their defaults - only the audio itself is brought over.",
            anchor="w", justify="left", wraplength=520)
        style_label(warning_label, fg=ACCENT_ORANGE, font=(UI_FAMILY, 8))
        warning_label.pack(fill="x", pady=(0, 12))

        folder_panel = RoundedPanel(outer, title="EXPORT Folder", parent_bg=BG_DARK,
                                     panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                     title_fg=ACCENT_BLUE)
        folder_panel.pack(fill="x", pady=(0, 12))
        folder_row = tk.Frame(folder_panel.body, bg=BG_PANEL)
        folder_row.pack(fill="x", pady=(6, 0))
        self.folder_label = tk.Label(folder_row, text="No folder selected yet. Pick the P-6 "
                                                       "drive or its \u201cEXPORT\u201d folder.",
                                      anchor="w")
        style_label(self.folder_label, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        self.folder_label.pack(side="left", fill="x", expand=True)
        choose_btn = RoundedButton(folder_row, text="Choose Folder...", command=self.choose_folder,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_PANEL, width=130, height=26,
                                    font=(UI_FAMILY, 8))
        choose_btn.pack(side="right")

        self.found_label = tk.Label(outer, text="", anchor="w", justify="left", wraplength=520)
        style_label(self.found_label, fg=FG_MUTED, font=(UI_FAMILY, 8))
        self.found_label.pack(fill="x")

        btn_row = tk.Frame(outer, bg=BG_DARK)
        btn_row.pack(fill="x", side="bottom", pady=(12, 0))
        cancel_btn = RoundedButton(btn_row, text="Cancel", command=self.on_cancel,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        cancel_btn.pack(side="right", padx=4)
        self.import_btn = RoundedButton(btn_row, text="Import", command=self.on_import,
                                         bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=100,
                                         state="disabled")
        self.import_btn.pack(side="right", padx=4)

        # Re-use last session's folder if the drive is still mounted and the
        # export is still there, so a repeat import needs no browsing at all.
        # Silent when it doesn't apply - the label already explains what to
        # pick, and an error about a drive that simply isn't plugged in
        # would be noise.
        remembered = load_last_export_dir()
        if remembered and self._resolve_export_folder(
                remembered, preferred_bank=self.app.current_bank.get())[0]:
            self._apply_chosen_folder(remembered)

        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
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

    @staticmethod
    def _find_pad_file(folder, pad_num):
        """Looks for one pad's sample, tolerating either a flat
        PAD_1.WAV file or a PAD_1/*.wav subfolder."""
        for ext in (".wav", ".WAV", ".Wav"):
            direct = os.path.join(folder, f"PAD_{pad_num}{ext}")
            if os.path.isfile(direct):
                return direct
        subfolder = os.path.join(folder, f"PAD_{pad_num}")
        if os.path.isdir(subfolder):
            try:
                for fname in sorted(os.listdir(subfolder)):
                    if fname.lower().endswith(".wav"):
                        return os.path.join(subfolder, fname)
            except Exception:
                pass
        return None

    @staticmethod
    def _folder_has_pads(folder):
        return any(ImportBankDialog._find_pad_file(folder, p) for p in PADS)

    @staticmethod
    def _subdirs(folder, name_test):
        try:
            entries = sorted(os.listdir(folder))
        except Exception as e:
            print(f"Could not scan {folder}: {e}")
            return []
        out = []
        for entry in entries:
            full = os.path.join(folder, entry)
            if os.path.isdir(full) and name_test(entry):
                out.append((entry, full))
        return out

    @staticmethod
    def _resolve_export_folder(folder, preferred_bank=None):
        """Finds the folder the pad samples actually live in, walking down
        through the EXPORT and BANK_x levels as needed.

        Returns (folder_or_None, note). Deliberately tolerant about where
        the user points it - the drive, the EXPORT folder and a single
        BANK_x folder are all reasonable things to click, and only one of
        them used to work. Never guesses silently: whenever it descends,
        the note says so, and with several banks present it names the one
        it took."""
        if ImportBankDialog._folder_has_pads(folder):
            return folder, ""
        _log_timing(f"  EXPORT scan: no pads directly in {folder}")

        # One level down into EXPORT, if that's what was selected around.
        base, note = folder, ""
        export_dirs = ImportBankDialog._subdirs(folder, lambda e: e.upper() == "EXPORT")
        if export_dirs:
            base = export_dirs[0][1]
            note = " (found via the EXPORT folder)"
            if ImportBankDialog._folder_has_pads(base):
                return base, note

        # Then the bank level: EXPORT/BANK_<letter>/PAD_<n>/...
        banks = [(name, path) for name, path in
                 ImportBankDialog._subdirs(base, lambda e: e.upper().startswith("BANK_"))
                 if ImportBankDialog._folder_has_pads(path)]
        _log_timing(f"  EXPORT scan: base={base} usable banks={[n for n, _ in banks]}")
        if not banks:
            return None, ""
        if len(banks) == 1:
            return banks[0][1], f"{note} (from {banks[0][0]})".strip()

        # More than one bank exported: prefer the one whose letter matches
        # the bank we'd import into, otherwise take the first and say which.
        if preferred_bank:
            for name, path in banks:
                if name.upper() == f"BANK_{preferred_bank.upper()}":
                    return path, f"{note} (from {name}, of {len(banks)} banks present)".strip()
        name, path = banks[0]
        others = ", ".join(n for n, _ in banks)
        return path, f"{note} (using {name}; folder contains {others})".strip()

    def choose_folder(self):
        picker = FolderPickerDialog(self, initial_dir=(load_last_export_dir() or self.app.import_root),
                                     title="Select the P-6 EXPORT Folder")
        self.wait_window(picker)
        if not picker.selected_dir:
            return
        self._apply_chosen_folder(picker.selected_dir, remember=True)

    def _apply_chosen_folder(self, chosen_dir, remember=False):
        """Resolves `chosen_dir` down to the folder holding the pads and
        updates the dialog. Shared by the Choose Folder button and the
        auto-fill from the last session, so both behave identically."""
        resolved, note = self._resolve_export_folder(
            chosen_dir, preferred_bank=self.app.current_bank.get())
        if resolved is None:
            self.selected_folder = None
            self.folder_label.config(text=chosen_dir, fg=FG_TEXT)
            self.found_label.config(
                text="No pad samples found there. Expected "
                     "EXPORT/BANK_x/PAD_n/<name>.WAV - select the P-6 drive, the "
                     "\u201cEXPORT\u201d folder, or one BANK folder inside it. If the path "
                     "looks right, the device may not have finished writing its export yet.",
                fg=ACCENT_ORANGE)
            self.import_btn.config_state("disabled")
            return False

        self.selected_folder = resolved
        self.folder_label.config(text=self.selected_folder, fg=FG_TEXT)
        found = [p for p in PADS if self._find_pad_file(self.selected_folder, p)]
        pads_str = ", ".join(str(p) for p in found)
        self.found_label.config(text=f"Found samples for pad(s): {pads_str}{note}",
                                fg=ACCENT_GREEN)
        self.import_btn.config_state("normal")
        if remember:
            # Remember what was PICKED, not what it resolved to - see
            # load_last_export_dir() for why.
            save_last_export_dir(chosen_dir)
        return True

    @staticmethod
    def _copy_into_temp(src, bank, pad):
        """Copies a sample off the device into the app's temp folder.

        The device is removable, so anything imported from it has to be
        taken along rather than referenced in place. The name keeps the
        bank/pad it came from so the temp folder stays readable, plus a
        short random part so importing the same bank twice, or two banks
        with identically named samples, can't collide.

        The P-6 stores each pad's settings in a .PRM file sharing the
        sample's base name. Its format is undocumented, so the app can't
        read it - but it copies it along under the copy's new base name, so
        the settings can be carried back to the device on export instead of
        being lost the moment a sample passes through here."""
        base, ext = os.path.splitext(src)
        dest = derived_temp_path(src, f"imp_{bank}{pad}", ext or ".wav")
        shutil.copy2(src, dest)
        for prm_ext in (".PRM", ".prm"):
            prm_src = base + prm_ext
            if os.path.isfile(prm_src):
                try:
                    shutil.copy2(prm_src, os.path.splitext(dest)[0] + ".PRM")
                except Exception as e:
                    print(f"Could not copy settings file {prm_src}: {e}")
                break
        return dest

    def on_import(self):
        if not self.selected_folder:
            return
        bank = self.app.current_bank.get()
        self.app._push_undo()
        self.app.stop_playback_waveform()

        imported, missing, failed = 0, [], []
        for i, pad in enumerate(PADS, 1):
            self.app.show_progress(f"Importing Bank {bank} from P6 \u2026 (Pad {i}/{len(PADS)})")
            src = self._find_pad_file(self.selected_folder, pad)
            if not src:
                missing.append(pad)
                self.app.pad_widgets[pad].clear_pad()
                continue
            try:
                # Copy into the temp folder instead of pointing the pad at
                # the file on the P-6 drive. Referencing the device directly
                # meant the pad emptied itself the moment the drive was
                # unmounted or the device rewrote its EXPORT folder - and a
                # preset saved after that silently stored those banks empty.
                self.app.pad_widgets[pad].set_file(
                    self._copy_into_temp(src, bank, pad),
                    display_name=os.path.basename(src))
                imported += 1
            except Exception as e:
                # One unreadable/corrupt file on the device must not abort
                # the whole import and leave the remaining pads untouched
                # with self.app.slots never re-synced below.
                print(f"Could not import PAD_{pad} from {src}: {e}")
                failed.append(pad)
                self.app.pad_widgets[pad].clear_pad()

        self.app.slots[bank] = {p: self.app.pad_widgets[p].get_state() for p in PADS}
        self.app.update_storage_display()
        self.app.update_pad_warnings()

        if failed:
            dark_showerror(
                "Some Pads Could Not Be Imported",
                "These pads' files could not be read:\n"
                + "\n".join(f"PAD_{p}" for p in failed), parent=self)
        if missing:
            missing_str = ", ".join(str(p) for p in missing)
            self.app.show_status(
                f"Bank {bank}: {imported} pad(s) imported, no sample found for pad(s) {missing_str}.",
                kind="warning")
        else:
            self.app.show_status(f"Bank {bank}: {imported} pad(s) imported from the P-6.")
        self.destroy()

    def on_cancel(self):
        self.destroy()


class AboutDialog(tk.Toplevel):
    """About box, opened from Settings.

    Kept as its own window rather than a sixth panel inside Settings: that
    dialog has no scrolling and is already 800px tall, so another panel
    would start pushing the Save/Close row off shorter screens."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title(f"About {APP_NAME}")
        self.geometry("560x560")
        self.minsize(560, 560)
        style_toplevel(self)

        outer = tk.Frame(self, bg=BG_DARK, padx=16, pady=16)
        outer.pack(fill="both", expand=True)

        name_label = tk.Label(outer, text=APP_NAME, anchor="w")
        style_label(name_label, fg=ACCENT_BLUE, font=(UI_FAMILY, 20, "bold"))
        name_label.pack(fill="x")

        subtitle_label = tk.Label(outer, text=APP_SUBTITLE, anchor="w")
        style_label(subtitle_label, fg=FG_TEXT, font=(UI_FAMILY, 11))
        subtitle_label.pack(fill="x")

        version_label = tk.Label(outer, text=f"Version {APP_VERSION}", anchor="w")
        style_label(version_label, fg=FG_MUTED, font=(UI_FAMILY, 9))
        version_label.pack(fill="x", pady=(2, 10))

        desc_label = tk.Label(
            outer, anchor="w", justify="left", wraplength=500,
            text=("Manages samples for the Roland AIRA P-6: load, trim, chop and "
                  "normalize audio, then write complete banks to the device's "
                  "IMPORT folder."))
        style_label(desc_label, fg=FG_TEXT, font=(UI_FAMILY, 9))
        desc_label.pack(fill="x", pady=(0, 12))

        # Not a clickable link on purpose: opening a browser from Tk needs
        # webbrowser plus a working desktop handler, and a silently failing
        # click is worse than text you can select and copy. "Copy Info"
        # includes it too.
        link_label = tk.Entry(outer, bd=0, highlightthickness=0, readonlybackground=BG_DARK,
                               fg=ACCENT_BLUE, font=(UI_FAMILY, 9), cursor="xterm",
                               selectbackground=ACCENT_BLUE, selectforeground="#FFFFFF")
        link_label.insert(0, APP_URL)
        link_label.config(state="readonly")  # selectable and copyable, but not editable
        link_label.pack(fill="x", pady=(0, 12))
        add_tooltip(link_label,
                    "Project page with the latest version, source code and issue "
                    "tracker. Select the text to copy it, or use \u201cCopy Info\u201d "
                    "for everything at once.")

        info_panel = RoundedPanel(outer, title="Details", parent_bg=BG_DARK,
                                   panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                   title_fg=ACCENT_BLUE)
        info_panel.pack(fill="both", expand=True)
        # A Text widget rather than a grid of Labels: Tk labels can't be
        # selected, so the version numbers and paths here - exactly the
        # things you want to paste into a bug report - couldn't be picked
        # out individually. "Copy Info" copies everything at once; this
        # allows grabbing a single line.
        self.info_text = tk.Text(
            info_panel.body, bg=BG_PANEL, fg=FG_TEXT, font=(UI_FAMILY, 8),
            bd=0, highlightthickness=0, wrap="word", height=14,
            insertwidth=0, cursor="xterm",
            selectbackground=ACCENT_BLUE, selectforeground="#FFFFFF")
        self.info_text.pack(fill="both", expand=True, pady=(6, 4))
        self.info_text.tag_configure("key", foreground=FG_MUTED)
        for label, value in collect_about_info():
            self.info_text.insert("end", f"{label}: ", "key")
            self.info_text.insert("end", f"{value}\n")
        self.info_text.delete("end-1c")  # trailing newline
        self._make_read_only(self.info_text)

        # Not legal advice, just plain honesty: this is an independent tool
        # and users shouldn't be left thinking Roland published it.
        disclaimer = tk.Label(
            outer, anchor="w", justify="left", wraplength=500,
            text=("Roland, AIRA and P-6 are trademarks of Roland Corporation. "
                  "This is an independent project and is not affiliated with, "
                  "endorsed by or supported by Roland."))
        style_label(disclaimer, fg=FG_MUTED, font=(UI_FAMILY, 8))
        disclaimer.pack(fill="x", pady=(12, 0))

        btn_row = tk.Frame(outer, bg=BG_DARK)
        btn_row.pack(fill="x", pady=(12, 0))
        close_btn = RoundedButton(btn_row, text="Close", command=self.destroy,
                                   bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        close_btn.pack(side="right", padx=4)
        self.copy_btn = RoundedButton(btn_row, text="Copy Info", command=self._copy_info,
                                       bg=BTN_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=110)
        self.copy_btn.pack(side="right", padx=4)
        add_tooltip(self.copy_btn,
                    "Copies version, component and path details to the clipboard - "
                    "handy to paste into a bug report.")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    @staticmethod
    def _make_read_only(text_widget):
        """Keeps a Text widget selectable and copyable but not editable.

        state="disabled" would also block the selection this exists for, so
        the widget stays enabled and every key that could change the content
        is swallowed instead - Ctrl+C/Ctrl+A and cursor movement still get
        through. Middle-click is blocked too: on X11 that pastes the primary
        selection straight into the widget."""
        allowed = {"c", "a", "C", "A", "Insert"}

        def on_key(event):
            if event.state & 0x4 and (event.keysym in allowed):  # Control held
                return None
            if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                                "Prior", "Next", "Shift_L", "Shift_R",
                                "Control_L", "Control_R"):
                return None
            return "break"

        text_widget.bind("<Key>", on_key)
        text_widget.bind("<<Paste>>", lambda e: "break")
        text_widget.bind("<<Cut>>", lambda e: "break")
        text_widget.bind("<Button-2>", lambda e: "break")

    def _copy_info(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(about_info_as_text())
            self.update()  # some X11 setups drop the selection without this
        except tk.TclError:
            return
        self.copy_btn.text = "Copied"
        self.copy_btn._draw()
        self.after(1500, self._reset_copy_button)

    def _reset_copy_button(self):
        try:
            if self.copy_btn.winfo_exists():
                self.copy_btn.text = "Copy Info"
                self.copy_btn._draw()
        except tk.TclError:
            pass

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


class SettingsDialog(tk.Toplevel):
    """Central place for things that used to be scattered top-bar buttons
    (Choose Folder) plus a few sensible defaults/overrides."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Settings")
        # 40px taller than the content used to need - the Appearance panel
        # gained the tooltip row, and this window has no scrolling, so the
        # Save/Close row at the bottom would otherwise be pushed off-screen.
        self.geometry("620x800")
        self.minsize(620, 800)
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
                                           bg=BTN_PURPLE, fg="#FFFFFF", parent_bg=BG_PANEL, width=100)
        change_folder_btn.pack(side="right")
        add_tooltip(change_folder_btn,
                    "The IMPORT folder on the P-6 drive. \"Banks \u2192 P6\" writes the "
                    "BANK_x/PAD_n folders in here.")

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
        theme_dd = RoundedDropdown(theme_row, self.theme_var,
                                    ["dark", "tokyo", "dracula", "modern", "latte", "bright"],
                                    parent_bg=BG_PANEL, width=100, height=26)
        theme_dd.pack(side="left", padx=6)
        theme_names_hint = tk.Label(
            theme_row,
            text="(latte = muted Catppuccin Latte, bright = the original)",
            anchor="w")
        style_label(theme_names_hint, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        theme_names_hint.pack(side="left", padx=(4, 0))

        theme_hint = tk.Label(appearance_panel.body,
                               text="Requires an app restart to take effect.",
                               anchor="w")
        style_label(theme_hint, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        theme_hint.pack(fill="x", pady=(6, 0))

        tooltip_row = tk.Frame(appearance_panel.body, bg=BG_PANEL)
        tooltip_row.pack(fill="x", pady=(10, 0))
        self.tooltips_var = tk.BooleanVar(value=TOOLTIPS_ENABLED)
        tooltips_cb = tk.Checkbutton(tooltip_row, text="Show tooltips (hover help)",
                                      variable=self.tooltips_var,
                                      command=self._on_tooltips_toggled)
        style_checkbutton(tooltips_cb)
        tooltips_cb.config(bg=BG_PANEL, activebackground=BG_PANEL)
        tooltips_cb.pack(side="left")
        tooltips_hint = tk.Label(tooltip_row, text="(applies immediately)", anchor="w")
        style_label(tooltips_hint, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        tooltips_hint.pack(side="left", padx=(6, 0))
        add_tooltip(tooltips_cb,
                    "Short explanations that appear when you hover over a button or "
                    "waveform. Turn this off once you know your way around.")

        # ----- Audio components -----
        comp_panel = RoundedPanel(outer, title="Audio Components (pydub / ffmpeg)", parent_bg=BG_DARK,
                                   panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                   title_fg=ACCENT_BLUE)
        comp_panel.pack(fill="x", pady=(0, 12))

        dnd_row = tk.Frame(comp_panel.body, bg=BG_PANEL)
        dnd_row.pack(fill="x", pady=(0, 8))
        dnd_lbl = tk.Label(dnd_row, text=f"Drag & drop onto pads: {dnd_status_text()}", anchor="w")
        style_label(dnd_lbl, bg=BG_PANEL, fg=(FG_TEXT if _dnd_is_working() else FG_MUTED), font=(UI_FAMILY, 9))
        dnd_lbl.pack(side="left")

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
        add_tooltip(self.storage_mb_entry,
                    "Above this size the storage display turns red (for the current bank "
                    "and for the total), and copying to the P-6 asks for confirmation "
                    "for every bank that is over.")

        # ----- Temporary files -----
        temp_panel = RoundedPanel(outer, title="Temporary Files", parent_bg=BG_DARK,
                                   panel_bg=BG_PANEL, border=BORDER_LIGHT, radius=14,
                                   title_fg=ACCENT_BLUE)
        temp_panel.pack(fill="x", pady=(0, 12))
        temp_row = tk.Frame(temp_panel.body, bg=BG_PANEL)
        temp_row.pack(fill="x", pady=(8, 0))
        self.temp_size_label = tk.Label(temp_row, text="", anchor="w")
        style_label(self.temp_size_label, bg=BG_PANEL, font=(UI_FAMILY, 9))
        self.temp_size_label.pack(side="left")
        clear_temp_btn = RoundedButton(temp_row, text="Clear Now", command=self._clear_temp,
                                        bg=BTN_ORANGE, fg="#FFFFFF", parent_bg=BG_PANEL,
                                        width=100, height=26, font=(UI_FAMILY, 8))
        clear_temp_btn.pack(side="right")
        add_tooltip(clear_temp_btn,
                    "Deletes all trimmed, normalized and chopped intermediate files. "
                    "Pads still pointing at one of them are cleared - save a preset "
                    "first if you want to keep those edits.")
        temp_hint = tk.Label(temp_panel.body,
                              text="Trimmed, normalized, mono-converted and chopped samples are stored here. "
                                   "Saved presets are self-contained (their samples are copied into the "
                                   "preset folder), so they are unaffected - but pads holding an edited "
                                   "sample you haven't saved to a preset yet will lose it.",
                              anchor="w", justify="left", wraplength=560)
        style_label(temp_hint, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 8))
        temp_hint.pack(fill="x", pady=(6, 0))
        self._refresh_temp_size()

        # ----- buttons -----
        btn_row = tk.Frame(outer, bg=BG_DARK)
        btn_row.pack(fill="x", pady=(4, 0))
        close_btn = RoundedButton(btn_row, text="Close", command=self.destroy,
                                   bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        close_btn.pack(side="right", padx=4)
        save_btn = RoundedButton(btn_row, text="Save", command=self._save,
                                  bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=90)
        save_btn.pack(side="right", padx=4)
        about_btn = RoundedButton(btn_row, text="About", command=self._open_about,
                                   bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK, width=90)
        about_btn.pack(side="left", padx=4)
        add_tooltip(about_btn,
                    "Version, author and the state of the optional components "
                    "(pydub, ffmpeg, drag & drop).")

        self.transient(parent)
        center_toplevel_on_parent(self, parent)
        self._safe_grab()

    def _open_about(self):
        """Hands the modal grab over to the About window and takes it back
        afterwards - two stacked grab_set() windows otherwise leave Settings
        unresponsive on some window managers once About closes."""
        try:
            self.grab_release()
        except tk.TclError:
            pass
        dlg = AboutDialog(self)
        self.wait_window(dlg)
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

    def _on_tooltips_toggled(self):
        """Applied and persisted right away, unlike the rest of this dialog.

        Toggling this is how you check whether you want it - so it has to be
        visible immediately, and saving it on the spot avoids the state where
        tooltips are visibly off but come back on the next start because
        "Close" was pressed instead of "Save"."""
        enabled = bool(self.tooltips_var.get())
        set_tooltips_enabled(enabled)
        save_config_value("tooltips_enabled", enabled)

    def _refresh_temp_size(self):
        total, count = get_temp_folder_size()
        mb = total / (1024 * 1024)
        if count == 0:
            self.temp_size_label.config(text="Temp folder is empty.", fg=FG_MUTED)
        else:
            file_word = "file" if count == 1 else "files"
            self.temp_size_label.config(text=f"{mb:.2f} MB in {count} {file_word}", fg=FG_TEXT)

    def _clear_temp(self):
        total, count = get_temp_folder_size()
        if count == 0:
            dark_showinfo("Nothing to Clear", "The temp folder is already empty.", parent=self)
            return
        mb = total / (1024 * 1024)
        if not dark_askyesno(
                "Clear Temporary Files?",
                f"Delete {count} file(s) ({mb:.2f} MB) from:\n{TEMP_DIR}\n\n"
                "Saved presets and your settings are not affected. Pads still holding an "
                "edited (trimmed/normalized/chopped) sample that hasn't been saved to a "
                "preset yet will be cleared.\n\nContinue?",
                parent=self):
            return
        deleted, errors = clear_temp_folder()
        self._refresh_temp_size()
        cleared_pads = 0
        if hasattr(self.app, "clear_pads_referencing_missing_files"):
            cleared_pads = self.app.clear_pads_referencing_missing_files()
        if errors:
            dark_showerror("Partial Errors",
                           "Some items could not be deleted:\n" + chr(10).join(errors), parent=self)
        else:
            msg = f"{deleted} item(s) deleted."
            if cleared_pads:
                pad_word = "pad" if cleared_pads == 1 else "pads"
                msg += f"\n\n{cleared_pads} {pad_word} referencing a deleted file were cleared."
            dark_showinfo("Temp Cleared", msg, parent=self)

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

        # Tooltips (already applied+saved on toggle - written again so an
        # explicit Save is never a no-op for a setting shown in this dialog)
        set_tooltips_enabled(bool(self.tooltips_var.get()))
        save_config_value("tooltips_enabled", bool(self.tooltips_var.get()))

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
        self.display_name = None
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
        self._bind_internal_drag(self.label)
        self._bind_internal_drag(self.panel.canvas)
        drag_help = ("Drag this name onto another pad to swap the two pads (including "
                     "their rate, pitch and mono settings). The target pad is outlined "
                     "in orange while you drag.")
        if DND_AVAILABLE:
            drag_help += ("\n\nYou can also drop an audio file from your file manager "
                          "straight onto a pad.")
        add_tooltip(self.label, drag_help)
        add_tooltip(self.panel.canvas,
                    "Drag the pad frame or the sample name onto another pad to swap "
                    "the two pads.")

        self.mini_wave_width = 240
        self.mini_wave_height = 50
        self.mini_wave_canvas = tk.Canvas(self.frame, bg=WAVE_BG,
                                           width=self.mini_wave_width, height=self.mini_wave_height,
                                           highlightthickness=0, cursor="hand2")
        self.mini_wave_canvas.pack(fill="x", pady=(2, 4))
        self.mini_wave_canvas.bind("<Configure>", self._redraw_mini_waveform_at_current_width)
        self.mini_wave_canvas.bind("<Button-1>", self.open_waveform_view)
        add_tooltip(self.mini_wave_canvas,
                    "Click the waveform to open the editor for this sample: trim markers, "
                    "zoom, normalize and fade in/out, then \"Apply to Pad\". "
                    "An orange shaded area marks the part the P-6 would cut off at the "
                    "current rate, pitch and mono setting.")
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
        add_tooltip(load_btn,
                    "Opens the sample browser with preview and waveform. There you can "
                    "drag the green/red markers to load only the marked region onto "
                    "this pad. WAV and (with ffmpeg) MP3.")
        self.play_btn = RoundedButton(btn_row, text="\u25b6", command=self.toggle_play_pad,
                                       bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_PANEL,
                                       width=36, height=28, state="disabled", font=(UI_FAMILY, 11, "bold"))
        self.play_btn.pack(side="left", padx=2)
        add_tooltip(self.play_btn,
                    "Plays this pad as it will sound on the P-6 (with rate, pitch and "
                    "mono applied) and shows it in the large waveform below.")
        self.remove_btn = RoundedButton(btn_row, text="\u23cf", command=self.remove_sample,
                                         bg=BTN_RED, fg="#FFFFFF", parent_bg=BG_PANEL,
                                         width=36, height=28, state="disabled", font=(UI_FAMILY, 11, "bold"))
        self.remove_btn.pack(side="left", padx=2)
        add_tooltip(self.remove_btn,
                    "Clears this pad in the app. The source file itself is not deleted. "
                    "Can be undone with Ctrl+Z.")
        chop_btn = RoundedButton(btn_row, text="Chop", command=self.open_chop,
                                  bg=BTN_ORANGE, fg="#FFFFFF", parent_bg=BG_PANEL,
                                  width=56, height=28)
        chop_btn.pack(side="left", padx=2)
        add_tooltip(chop_btn,
                    "Builds a multisample: several samples are lined up in equal slices "
                    "into one file that the P-6 plays back per slice. The result lands "
                    "on this pad.")

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

    def set_file(self, path, from_sync=False, display_name=None, keep_settings=False):
        """Puts a sample on this pad.

        keep_settings=False (the default) treats `path` as a NEW sample:
        the target rate is auto-detected from the file and pitch/mono go
        back to their defaults, which is what you want when loading,
        dropping or importing something.

        keep_settings=True is for replacing this pad's sample with an
        EDITED version of itself (the waveform editor's "Apply to Pad").
        Re-detecting there threw away deliberate choices: a pad set to
        22050 Hz to save space jumped back to 44100 the moment an edit was
        applied, because trimming preserves the source file's own rate -
        and pitch and Mono were reset along with it."""
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
        # Trimmed/chopped samples are written to a randomly-named temp file
        # (trim_XXXXXXXX.wav) - display_name lets callers show the original,
        # recognizable filename instead of that generated one.
        self.display_name = display_name or os.path.basename(path)
        label_text = self.display_name + (" (converted)" if converted else "")
        if from_sync:
            label_text += " [on device]"
        self.label.config(text=label_text, fg=FG_TEXT)
        self.play_btn.config_state("normal")
        self.remove_btn.config_state("normal")

        if not keep_settings:
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
            "display_name": self.display_name,
        }

    def apply_state(self, state):
        """Restores a previously saved state (or clears the pad if None) -
        used when switching banks, instead of destroying/recreating widgets."""
        if not state or not state.get("filepath"):
            self.clear_pad()
            return
        if not os.path.exists(state["filepath"]):
            # The file this pad used to point to is gone (deleted outside
            # the app, a stale preset reference, etc.) - clear the pad
            # instead of leaving it in a broken half-loaded state where
            # rate detection etc. would silently fail.
            print(f"PAD_{self.pad_num}: referenced file no longer exists, clearing pad: "
                  f"{state['filepath']}")
            self.clear_pad()
            return
        self.set_file(state["filepath"], from_sync=state.get("from_sync", False),
                       display_name=state.get("display_name"))
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
        self.display_name = None
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

    def _bind_internal_drag(self, widget):
        """Wires up the drag-to-swap gesture on a passive (non-interactive)
        widget - the sample name label and the panel's border/title area
        are good handles since neither does anything else on click."""
        try:
            widget.config(cursor="fleur")
        except tk.TclError:
            pass  # not every platform has this cursor name; harmless if so
        widget.bind("<ButtonPress-1>", self._on_internal_drag_start)
        widget.bind("<B1-Motion>", self._on_internal_drag_motion)
        widget.bind("<ButtonRelease-1>", self._on_internal_drag_end)

    def _on_internal_drag_start(self, event):
        self._internal_drag_started_at = (event.x_root, event.y_root)

    def _on_internal_drag_motion(self, event):
        start = getattr(self, "_internal_drag_started_at", None)
        if start is None or not hasattr(self.app, "_pad_at_screen_pos"):
            return
        # Small threshold so a plain click doesn't get treated as a drag.
        if not getattr(self, "_internal_drag_active", False):
            dx, dy = event.x_root - start[0], event.y_root - start[1]
            if (dx * dx + dy * dy) < 36:  # ~6px
                return
            self._internal_drag_active = True
        hovered = self.app._pad_at_screen_pos(event.x_root, event.y_root)
        for pad, slot in self.app.pad_widgets.items():
            if pad == hovered and pad != self.pad_num:
                slot.panel.set_border_color(ACCENT_ORANGE, width=3)
            else:
                color, width = self.app._pad_idle_color(pad)
                slot.panel.set_border_color(color, width=width)

    def _on_internal_drag_end(self, event):
        was_active = getattr(self, "_internal_drag_active", False)
        self._internal_drag_active = False
        self._internal_drag_started_at = None
        if hasattr(self.app, "_clear_all_pad_highlights"):
            self.app._clear_all_pad_highlights()
        if not was_active or not hasattr(self.app, "_pad_at_screen_pos"):
            return
        target = self.app._pad_at_screen_pos(event.x_root, event.y_root)
        if target is not None and target != self.pad_num and hasattr(self.app, "swap_pads"):
            self.app.swap_pads(self.pad_num, target)

    def load_sample(self):
        global LAST_SAMPLE_DIR
        if hasattr(self.app, "stop_playback_waveform"):
            self.app.stop_playback_waveform()
        # Prefer the last folder the user actually browsed to (shared across
        # all pads) over this pad's own sample's folder - that's often a
        # temp folder (trim/chop output), which isn't where the user wants
        # to keep browsing. This also matches the common workflow of loading
        # several samples from the same folder onto multiple pads in a row.
        if LAST_SAMPLE_DIR:
            initial_dir = LAST_SAMPLE_DIR
        elif self.filepath and os.path.dirname(self.filepath):
            initial_dir = os.path.dirname(self.filepath)
        else:
            initial_dir = None
        dialog = AudioPreviewDialog(self.app.root, initial_dir=initial_dir)
        self.app.root.wait_window(dialog)
        if dialog.selected_path:
            if hasattr(self.app, "_push_undo"):
                self.app._push_undo()
            self.set_file(dialog.selected_path, display_name=dialog.selected_display_name)
            # Use the folder the user actually browsed to, not
            # os.path.dirname(selected_path) - if the sample was trimmed,
            # selected_path points into the app temp folder, which
            # would otherwise corrupt LAST_SAMPLE_DIR for every pad after.
            chosen_dir = getattr(dialog, "current_dir", None) or os.path.dirname(dialog.selected_path)
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
            if hasattr(self.app, "_push_undo"):
                self.app._push_undo()
            self.set_file(dialog.result_path)
            self.target_rate.set(dialog.rate_var.get())
            self.update_warning()
            chosen_dir = dialog.current_dir
            LAST_SAMPLE_DIR = chosen_dir
            save_last_sample_dir(chosen_dir)

    def effective_mono(self):
        """Whether this pad should be treated as mono - either because the
        active bank's 'Force Mono (this bank)' switch is on, or because
        this pad's own Mono checkbox is checked."""
        bank = self.app.current_bank.get() if hasattr(self.app, "current_bank") else None
        global_force = self.app.bank_force_mono(bank) if bank and hasattr(self.app, "bank_force_mono") else False
        return global_force or self.mono_var.get()

    def update_mono_lock(self):
        """Greys out this pad's own Mono checkbox while the active bank's
        Force Mono switch is on (it's already forced, so the individual
        choice doesn't matter until that bank's switch is off again)."""
        bank = self.app.current_bank.get() if hasattr(self.app, "current_bank") else None
        global_force = self.app.bank_force_mono(bank) if bank and hasattr(self.app, "bank_force_mono") else False
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
        if hasattr(self.app, "highlight_playing_pad"):
            self.app.highlight_playing_pad(self.pad_num)

    def open_waveform_view(self, event=None):
        if not (self.filepath and os.path.exists(self.filepath)):
            return
        name = self.display_name or os.path.basename(self.filepath)
        if hasattr(self.app, "stop_playback_waveform"):
            self.app.stop_playback_waveform()
        PadWaveformViewDialog(self.app.root, self.app, self.pad_num, self.filepath, name)

    def toggle_play_pad(self):
        currently_playing = (getattr(self.app, "_currently_playing_pad", None) == self.pad_num
                              and getattr(self.app, "main_wave_is_playing", False))
        if currently_playing:
            self.app.stop_playback_waveform()
        else:
            self.play_sample()

    def set_play_button_state(self, is_playing):
        """Shows a grey Stop square in place of the green Play triangle
        while this pad is the one currently playing."""
        if is_playing:
            self.play_btn.bg_color = BG_INPUT
            self.play_btn.text = "\u25a0"
        else:
            # BTN_GREEN, not ACCENT_GREEN: this reassigns the fill at runtime,
            # so using the raw accent here would undo the readable button
            # color the moment a pad was played and stopped once.
            self.play_btn.bg_color = BTN_GREEN
            self.play_btn.text = "\u25b6"
        self.play_btn._draw()

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
        quick visual glance at what's loaded. The part that exceeds the
        P-6's length limit for the current rate/pitch/mono settings is
        shaded orange, exactly like the big waveform views; the trace
        itself always stays the normal waveform color. Shows both channels
        stacked when the sample is stereo (unless Force Mono is on)."""
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
        cut_frac = compute_truncate_fraction(self.filepath, self.target_rate.get(),
                                             self.pitch_cents.get(), force_mono)
        self._mini_wave_cache = (data, force_mono, cut_frac)
        self._redraw_mini_waveform_at_current_width()

    def _redraw_mini_waveform_at_current_width(self, event=None):
        """Draws the cached mini-waveform data at whatever width the canvas
        currently reports. Bound to <Configure> instead of calling
        update_idletasks() (which forces an expensive synchronous X11
        round-trip on Linux) - Tk fires <Configure> naturally once the real
        layout size is known, so this just redraws cheaply from cache."""
        if not getattr(self, "_mini_wave_cache", None):
            return
        data, force_mono, cut_frac = self._mini_wave_cache
        width_px = max(self.mini_wave_canvas.winfo_width(), self.mini_wave_width)
        color = WAVE_COLOR
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
        # Drawn last so it sits on top of the trace. The mini view is never
        # zoomed, so the fraction maps straight onto the canvas width.
        self.mini_wave_canvas.delete("truncate")
        if cut_frac is not None:
            draw_truncate_overlay(self.mini_wave_canvas, cut_frac * width_px,
                                   width_px, self.mini_wave_height)

    def remove_sample(self):
        if self.filepath:
            if hasattr(self.app, "_push_undo"):
                self.app._push_undo()
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
                self.app.show_status(f"PAD_{self.pad_num}: removed from the device.")

        self.clear_pad()
        if hasattr(self.app, "clear_playback_waveform"):
            self.app.clear_playback_waveform()
        if hasattr(self.app, "update_storage_display"):
            self.app.update_storage_display()
        if hasattr(self.app, "update_pad_warnings"):
            self.app.update_pad_warnings()


class P6ManagerApp:
    def __init__(self, root):
        global _DND_APP
        self.root = root
        _DND_APP = self  # so dnd_status_text() can report the live registration state
        self.root.title(f"{APP_NAME} {APP_SUBTITLE} {APP_VERSION}")
        self.root.configure(bg=BG_DARK)
        self.root.resizable(True, True)
        self.root.bind_all("<Control-z>", lambda e: self.undo())
        self.root.bind_all("<Control-Shift-Z>", lambda e: self.redo())
        self.root.bind_all("<Control-y>", lambda e: self.redo())
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
        self._undo_stack = []
        self._redo_stack = []
        self._active_bank = None  # which bank's state is currently loaded into pad_widgets
        self._currently_playing_pad = None  # which pad's waveform is shown in the main view

        # Status bar - packed FIRST with side="bottom" so it reliably keeps
        # its spot at the very bottom of the window regardless of how the
        # rest of the layout fills/expands.
        self._status_clear_job = None

        top = tk.Frame(root, padx=14, pady=14, bg=BG_DARK)
        top.pack(fill="x")
        bank_lbl = tk.Label(top, text="Bank:")
        style_label(bank_lbl, font=(UI_FAMILY, 11, "bold"))
        bank_lbl.pack(side="left")
        bank_menu = RoundedDropdown(top, self.current_bank, BANKS, command=self.switch_bank,
                                    parent_bg=BG_DARK, width=70, height=30,
                                    value_color_fn=self._bank_dropdown_color)
        bank_menu.pack(side="left", padx=8)
        add_tooltip(bank_menu,
                    "Switches the 6 pads below to another bank (A-H). Each bank keeps its "
                    "own pads and settings; banks that already contain samples are shown "
                    "in blue.")

        # One Force Mono flag PER BANK, not a single global one - a single
        # shared flag can't be expressed correctly once presets can hold a
        # different mono/stereo intent per bank (e.g. bank A always mono,
        # bank C always stereo). The checkbox below always shows/controls
        # whichever bank is currently active, and is re-pointed at the
        # right BooleanVar on every bank switch.
        self.force_mono_vars = {bank: tk.BooleanVar(value=False) for bank in BANKS}
        self.force_mono_cb = tk.Checkbutton(
            top, text="Force Mono (this bank)", variable=self.force_mono_vars[self.current_bank.get()],
            command=self.on_force_mono_changed)
        style_checkbutton(self.force_mono_cb)
        self.force_mono_cb.pack(side="left", padx=(4, 0))
        add_tooltip(self.force_mono_cb,
                    "Exports every pad of the CURRENT bank as mono - roughly halves the "
                    "size on the device. Stored per bank, so other banks keep their own "
                    "setting.")

        self.path_label = tk.Label(top, text=f"IMPORT Path: {self.import_root}")
        style_label(self.path_label, fg=FG_MUTED, font=(UI_FAMILY, 9))
        self.path_label.pack(side="left", padx=20)

        settings_btn = RoundedButton(top, text="\u2699", command=self.open_settings,
                                      bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                      width=36, height=30, font=(UI_FAMILY, 13, "bold"))
        settings_btn.pack(side="right", padx=4)
        add_tooltip(settings_btn,
                    "Settings: IMPORT folder, theme, tooltips, ffmpeg/ffprobe paths, "
                    "defaults and temporary files.")

        self.redo_btn = RoundedButton(top, text="\u21b7", command=self.redo,
                                       bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                       width=36, height=30, font=(UI_FAMILY, 13, "bold"),
                                       state="disabled")
        self.redo_btn.pack(side="right", padx=(0, 4))
        add_tooltip(self.redo_btn,
                    "Redo the change you just undid.\nShortcut: Ctrl+Shift+Z or Ctrl+Y")
        self.undo_btn = RoundedButton(top, text="\u21b6", command=self.undo,
                                       bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                       width=36, height=30, font=(UI_FAMILY, 13, "bold"),
                                       state="disabled")
        self.undo_btn.pack(side="right", padx=(4, 0))
        add_tooltip(self.undo_btn,
                    "Undo the last pad change (load, remove, swap, apply edit, clear "
                    "bank, load preset ...).\nShortcut: Ctrl+Z")

        preset_btn = RoundedButton(top, text="Preset \u25be", command=self.open_preset_menu,
                                    bg=BG_INPUT, fg=FG_TEXT, parent_bg=BG_DARK,
                                    width=90, height=30, font=(UI_FAMILY, 9, "bold"))
        preset_btn.pack(side="right", padx=4)
        self.preset_btn = preset_btn
        add_tooltip(preset_btn,
                    "Save or load presets, or reopen a recent one. A preset stores the "
                    "selected banks including their samples, so it stays usable even "
                    "after the temp folder is cleared.")

        self.pad_container = tk.Frame(root, padx=14, bg=BG_DARK)
        self.pad_container.pack(fill="x", pady=(6, 0))
        self.pad_container.grid_columnconfigure((0, 1, 2), weight=1)
        self.pad_widgets = {}
        _log_timing("  before creating 6 pad widgets")
        for pad in PADS:
            self.pad_widgets[pad] = SampleSlot(self.pad_container, pad, self)
        _log_timing("  after creating 6 pad widgets")

        if DND_AVAILABLE:
            self._last_dnd_hover_pad = None
            self._last_drop_time = 0.0
            # Registering the DND target immediately here, mid-construction,
            # happens BEFORE the window manager has actually mapped this
            # window on screen. XDND requires a fully mapped window with its
            # XdndAware property set before the WM will reliably recognize
            # it as a drop target - registering too early is a plausible
            # cause of "sometimes the drop just never arrives" on Linux.
            # <Map> fires once the window is genuinely on screen, so defer
            # registration to that point instead.
            self.root.bind("<Map>", self._setup_dnd_targets, add="+")

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

        # Fixed-position transient status message - always shows/clears in
        # the same spot, so quick "done" notifications don't shove anything
        # else around.
        self.status_label = tk.Label(hint_col, text="", anchor="w", justify="left")
        style_label(self.status_label, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 9, "bold"))
        self.status_label.pack(anchor="w", fill="x")

        # Persistent warnings (storage limit, "sample too long" per pad)
        # live in a small scrollable text area below the status line, so a
        # long list of warnings scrolls in place instead of growing the
        # whole window downward.
        ensure_dark_treeview_style()
        warn_frame = tk.Frame(hint_col, bg=BG_PANEL)
        warn_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.warn_scrollbar = ttk.Scrollbar(warn_frame, style="Dark.Vertical.TScrollbar", orient="vertical")
        # Not packed here on purpose - _autohide_warn_scrollbar() packs/
        # unpacks it on demand, only showing it when content actually
        # overflows the visible area.
        self.warnings_text = tk.Text(warn_frame, height=3, wrap="word", bg=BG_PANEL, fg=FG_TEXT,
                                      relief="flat", bd=0, font=(UI_FAMILY, 8), highlightthickness=0,
                                      yscrollcommand=self._autohide_warn_scrollbar,
                                      state="disabled", cursor="arrow")
        self.warnings_text.tag_configure("storage", foreground=ACCENT_RED, font=(UI_FAMILY, 9, "bold"))
        self.warnings_text.tag_configure("padwarn", foreground=ACCENT_ORANGE, font=(UI_FAMILY, 8))
        self.warnings_text.pack(side="left", fill="both", expand=True)
        self.warn_scrollbar.config(command=self.warnings_text.yview)
        self._storage_hint_text = ""
        self._pad_warnings_text = ""

        # ----- playback waveform (reacts to Play/Preview on any pad) -----
        wave_header = tk.Frame(self.storage_panel.body, bg=BG_PANEL)
        wave_header.pack(fill="x", pady=(10, 0))
        self.main_wave_name_label = tk.Label(wave_header, text="No sample playing")
        style_label(self.main_wave_name_label, bg=BG_PANEL, fg=FG_MUTED, font=(UI_FAMILY, 9))
        self.main_wave_name_label.pack(side="left")
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
        copy_all_btn = RoundedButton(bottom, text="Banks \u2192 P6", command=self.open_copy_banks_dialog,
                                      bg=BTN_GREEN, fg="#FFFFFF", parent_bg=BG_DARK, width=120)
        copy_all_btn.pack(side="left", padx=4)
        add_tooltip(copy_all_btn,
                    "Writes the banks you pick to the P-6: each pad's sample is converted "
                    "to its rate/pitch/mono settings and copied to "
                    "IMPORT/BANK_x/PAD_n/ on the device, replacing whatever was there.")
        import_bank_btn = RoundedButton(bottom, text="P6 \u2192 Bank", command=self.open_import_bank_dialog,
                                         bg=BTN_BLUE, fg="#FFFFFF", parent_bg=BG_DARK, width=120)
        import_bank_btn.pack(side="left", padx=4)
        add_tooltip(import_bank_btn,
                    "The other direction: guides you through the P-6's own export "
                    "procedure and loads the resulting EXPORT folder onto the currently "
                    "active bank. Rate, pitch and mono come in at their defaults.")
        clear_btn = RoundedButton(bottom, text="Bank Clear", command=self.open_clear_banks_dialog,
                                   bg=BTN_ORANGE, fg="#FFFFFF", parent_bg=BG_DARK, width=110)
        clear_btn.pack(side="left", padx=4)
        add_tooltip(clear_btn,
                    "Pick which banks to empty (the active one is preselected). Only the "
                    "pads in the app are cleared - no files on disk or on the device are "
                    "touched. Can be undone with Ctrl+Z.")
        # Extra gap before the destructive one - it permanently deletes
        # files off the device, so it shouldn't sit flush against the
        # everyday buttons where it can be mis-clicked.
        wipe_btn = RoundedButton(bottom, text="Wipe P6 IMPORT Folder", command=self.wipe_import_folder,
                                  bg=BTN_RED, fg="#FFFFFF", parent_bg=BG_DARK, width=180)
        wipe_btn.pack(side="left", padx=(28, 4))
        add_tooltip(wipe_btn,
                    "CAUTION: permanently deletes every sample file in the P-6 IMPORT "
                    "folder, across all banks. Cannot be undone. Your pads in the app "
                    "stay as they are - only the device-side copies are removed.")

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
            if DEBUG_STARTUP:
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
            draw_truncate_overlay(self.main_wave_canvas, x_cut, width_px,
                                   self.main_wave_height)
            self.main_wave_duration_label.config(
                text=f"Length: {duration:.2f}s   (max {limit:.2f}s)", fg=ACCENT_ORANGE)
        else:
            self.main_wave_duration_label.config(text=f"Length: {duration:.2f}s", fg=ACCENT_BLUE)

    def _refresh_playing_pad_button(self):
        """Keeps every pad's Play/Stop button in sync with whether audio is
        actually playing right now (not just which pad's waveform is shown
        - that stays visible after playback ends, but the button shouldn't)."""
        if not hasattr(self, "pad_widgets"):
            return
        playing_pad = self._currently_playing_pad if getattr(self, "main_wave_is_playing", False) else None
        for pad, slot in self.pad_widgets.items():
            slot.set_play_button_state(pad == playing_pad)

    def stop_playback_waveform(self):
        try:
            sd.stop()
        except Exception:
            pass
        self.main_wave_is_playing = False
        self._main_wave_play_id += 1  # invalidate any still-scheduled playhead tick
        if hasattr(self, "main_wave_canvas"):
            self.main_wave_canvas.delete("playhead")
        self._refresh_playing_pad_button()

    def clear_playback_waveform(self):
        """Stops playback and empties the main waveform view entirely -
        used when switching banks, since the currently shown sample may not
        even belong to the newly selected bank anymore."""
        self.stop_playback_waveform()
        self._currently_playing_pad = None
        if hasattr(self, "pad_widgets"):
            self._clear_all_pad_highlights()
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
        self._refresh_playing_pad_button()
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
            self._refresh_playing_pad_button()

    def open_settings(self):
        dialog = SettingsDialog(self.root, self)
        self.root.wait_window(dialog)

    def open_preset_menu(self):
        menu = tk.Menu(self.root, tearoff=0, bg=BG_PANEL, fg=FG_TEXT,
                        activebackground=ACCENT_BLUE, activeforeground="#FFFFFF",
                        font=(UI_FAMILY, 9))
        menu.add_command(label="Save Preset...", command=self.open_save_preset_dialog)
        menu.add_command(label="Load Preset...", command=self.open_load_preset_dialog)

        recents = [p for p in load_recent_presets() if is_preset_folder(p)]
        if recents:
            menu.add_separator()
            recent_menu = tk.Menu(menu, tearoff=0, bg=BG_PANEL, fg=FG_TEXT,
                                   activebackground=ACCENT_BLUE, activeforeground="#FFFFFF",
                                   font=(UI_FAMILY, 9))
            for path in recents:
                name = os.path.basename(path.rstrip(os.sep)) or path
                recent_menu.add_command(
                    label=name, command=lambda p=path: self.open_load_preset_dialog(preselect=p))
            menu.add_cascade(label="Recent", menu=recent_menu)

        x = self.preset_btn.winfo_rootx()
        y = self.preset_btn.winfo_rooty() + self.preset_btn.winfo_height()
        # tk_popup() manages its own grab/dismiss lifecycle (click outside,
        # Escape) internally - calling grab_release() right after used to
        # interfere with that on some platforms (notably Linux/X11), leaving
        # the menu stuck open with no way to dismiss it without picking an
        # item. Escape is bound explicitly too, as a guaranteed fallback.
        menu.bind("<Escape>", lambda e: menu.unpost())
        menu.tk_popup(x, y)

    def open_save_preset_dialog(self):
        initial_dir = os.path.dirname(load_recent_presets()[0]) if load_recent_presets() \
            else os.path.expanduser("~")
        dialog = PresetSaveDialog(self.root, self, initial_dir=initial_dir)
        self.root.wait_window(dialog)
        if dialog.result_dir:
            self.show_status(f"Preset saved: {os.path.basename(dialog.result_dir)}")

    def open_load_preset_dialog(self, preselect=None):
        initial_dir = os.path.dirname(preselect) if preselect else (
            os.path.dirname(load_recent_presets()[0]) if load_recent_presets()
            else os.path.expanduser("~"))
        dialog = PresetLoadDialog(self.root, self, initial_dir=initial_dir, preselect_path=preselect)
        self.root.wait_window(dialog)
        if dialog.result_dir and dialog.result_banks:
            self.load_preset_from_folder(dialog.result_dir, dialog.result_banks,
                                          target_bank_override=dialog.result_target_override)

    def show_progress(self, message):
        """Live progress update during a longer operation (e.g. exporting
        several banks). Unlike show_status(), this doesn't start an
        auto-clear timer - the caller is expected to follow up with another
        show_progress() or a final show_status() - and it forces an
        immediate repaint so each step is actually visible instead of only
        appearing once the whole synchronous operation finishes."""
        if self._status_clear_job:
            self.root.after_cancel(self._status_clear_job)
            self._status_clear_job = None
        self.status_label.config(text=message, fg=FG_MUTED)
        self.root.update_idletasks()

    def show_status(self, message, kind="success", duration_ms=5000):
        """Shows a brief, self-clearing message in the fixed status line
        above the warnings area - for pure 'this finished, nothing to
        decide' notifications. Errors/warnings/questions still use
        dark_showerror/dark_showwarning/dark_askyesno, since those need
        real attention."""
        colors = {
            "success": ACCENT_GREEN,
            "info": FG_MUTED,
            "warning": ACCENT_ORANGE,
        }
        self.status_label.config(text=message, fg=colors.get(kind, FG_MUTED))
        if self._status_clear_job:
            self.root.after_cancel(self._status_clear_job)
        self._status_clear_job = self.root.after(duration_ms, self._clear_status)

    def _clear_status(self):
        self.status_label.config(text="")
        self._status_clear_job = None

    def _pad_at_screen_pos(self, x_root, y_root):
        """Which pad (1-6) contains the given absolute screen coordinates,
        or None if outside all of them."""
        for pad, slot in self.pad_widgets.items():
            panel = slot.panel
            px, py = panel.winfo_rootx(), panel.winfo_rooty()
            pw, ph = panel.winfo_width(), panel.winfo_height()
            if px <= x_root <= px + pw and py <= y_root <= py + ph:
                return pad
        return None

    def _pad_idle_color(self, pad):
        """Border color/width a pad should show when nothing else (a drag
        hover) is overriding it - blue if this is the pad whose waveform is
        currently shown in the main playback view, otherwise the default."""
        if pad == getattr(self, "_currently_playing_pad", None):
            return ACCENT_BLUE, 3
        return BORDER_LIGHT, 1

    def _clear_all_pad_highlights(self):
        for pad, slot in self.pad_widgets.items():
            color, width = self._pad_idle_color(pad)
            slot.panel.set_border_color(color, width=width)

    def highlight_playing_pad(self, pad):
        """Highlights whichever pad's sample is currently shown in the main
        waveform area, in the accent blue - same visual language as the
        drag-hover highlight, distinct color so the two are never confused."""
        self._currently_playing_pad = pad
        self._clear_all_pad_highlights()
        self._refresh_playing_pad_button()

    def swap_pads(self, pad_a, pad_b):
        """Swaps two pads' entire contents (sample, rate, pitch, mono) -
        used by the in-app drag-to-swap gesture. Reuses get_state()/
        apply_state(), the same mechanism already used for bank switching:
        read both pads' state, write each back into the other's slot."""
        if pad_a == pad_b:
            return
        self._push_undo()
        state_a = self.pad_widgets[pad_a].get_state()
        state_b = self.pad_widgets[pad_b].get_state()
        self.pad_widgets[pad_a].apply_state(state_b)
        self.pad_widgets[pad_b].apply_state(state_a)
        # The highlight and the Stop button track a pad NUMBER, but the
        # sample just moved to the other pad - so they have to move with it.
        # Without this the blue border stayed on the pad the sample left,
        # pointing at content that isn't playing.
        playing = getattr(self, "_currently_playing_pad", None)
        if playing == pad_a:
            self._currently_playing_pad = pad_b
        elif playing == pad_b:
            self._currently_playing_pad = pad_a
        self._clear_all_pad_highlights()
        self._refresh_playing_pad_button()
        self.update_storage_display()
        self.update_pad_warnings()
        self.show_status(f"Swapped PAD_{pad_a} and PAD_{pad_b}.")

    def _setup_dnd_targets(self, event=None):
        """Registers the drop target(s) once the window is actually mapped
        on screen (see the <Map> binding in __init__ for why this is
        deferred rather than done immediately). <Map> can fire more than
        once (e.g. de-iconify), so this guards against registering twice.

        Both registrations are guarded and the "done" flag is only set once
        at least one of them succeeded. Previously the flag was set up front
        and the pad_container registration was unguarded: if it raised (a
        tkdnd build issue, a compositor quirk), the exception surfaced only
        as a Tk callback traceback, the root-level fallback never ran, and
        the flag already said "done" - so a later <Map> wouldn't retry and
        drag & drop was dead for the whole session with no visible reason."""
        if getattr(self, "_dnd_targets_ready", False):
            return
        self._dnd_registered = []

        def register(widget, label):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<DropPosition>>", self._on_pad_drag_position)
                widget.dnd_bind("<<DragLeave>>", self._on_pad_drag_leave)
                widget.dnd_bind("<<Drop>>", self._on_pad_drop)
                self._dnd_registered.append(label)
                _log_timing(f"  drop target registered on {label}")
                return True
            except Exception as e:
                print(f"Drop target registration on {label} failed: {e}")
                return False

        # One centralized drop target instead of one per pad widget:
        # per-widget <<DragEnter>>/<<DragLeave>> turned out not to fire at
        # all on some Linux/XDND setups, even though <<Drop>> itself does.
        # Using cursor coordinates against each pad's on-screen bounds
        # sidesteps that - it only depends on events confirmed to arrive.
        register(self.pad_container, "pad container")
        # Belt-and-suspenders: also register on root itself, in case this
        # XDND setup only reliably recognizes the top-level window as a
        # target, not a nested child frame.
        register(self.root, "main window")

        if self._dnd_registered:
            self._dnd_targets_ready = True
        else:
            # Leave the flag unset so the next <Map> gets another go.
            print("Drag & drop: no drop target could be registered - "
                  "dropping files onto pads will not work this session.")

    def _on_pad_drag_position(self, event):
        """Fires repeatedly as the cursor moves during a drag over
        pad_container - used to highlight whichever pad is currently under
        the cursor. (If this doesn't fire either on a given platform, drop
        itself still works via _on_pad_drop - only the live highlight is
        lost, not the functionality.)"""
        if not getattr(self, "_dnd_position_seen", False):
            self._dnd_position_seen = True
            _log_timing("  first <<DropPosition>> received - XDND is reaching the app")
        hovered = self._pad_at_screen_pos(event.x_root, event.y_root)
        self._last_dnd_hover_pad = hovered
        for pad, slot in self.pad_widgets.items():
            if pad == hovered:
                slot.panel.set_border_color(ACCENT_GREEN, width=3)
            else:
                color, width = self._pad_idle_color(pad)
                slot.panel.set_border_color(color, width=width)
        return event.action

    def _on_pad_drag_leave(self, event):
        self._clear_all_pad_highlights()
        self._last_dnd_hover_pad = None
        return event.action

    def _on_pad_drop(self, event):
        _log_timing("  <<Drop>> received")
        now = time.time()
        if now - getattr(self, "_last_drop_time", 0.0) < 0.3:
            return "break"
        self._last_drop_time = now
        self._clear_all_pad_highlights()
        pad = self._pad_at_screen_pos(event.x_root, event.y_root)
        if pad is None:
            # The exact drop-time coordinates can land a hair outside any
            # pad's bounds even though DropPosition was hitting one cleanly
            # moments before (tiny coordinate discrepancy between the two
            # event types) - falling back to whichever pad was highlighted
            # last avoids silently dropping the file on the floor.
            pad = getattr(self, "_last_dnd_hover_pad", None)
        self._last_dnd_hover_pad = None
        if pad is None:
            dark_showwarning("Drop Not Recognized",
                             "Couldn't tell which pad that was dropped on - try dropping "
                             "more toward the center of a pad.")
            return "break"
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        audio_paths = [p for p in paths if p.lower().endswith((".wav", ".mp3"))]
        if not audio_paths:
            dark_showwarning("No Audio Files",
                             "The dropped item(s) don't look like .wav or .mp3 files.")
            return "break"

        self.stop_playback_waveform()
        self._push_undo()
        # Load the first file onto the pad under the cursor; if more than
        # one was dropped, keep filling the following pads in order.
        for offset, path in enumerate(audio_paths):
            target_num = pad + offset
            if target_num > len(PADS):
                break
            self.pad_widgets[target_num].set_file(path)
        return "break"

    def bank_force_mono(self, bank):
        """The Force Mono flag for a specific bank (per-bank, not global)."""
        var = self.force_mono_vars.get(bank)
        return bool(var.get()) if var else False

    def _snapshot_state(self):
        """A deep-copyable snapshot of everything undo/redo cares about:
        every bank's pad assignments plus each bank's own Force Mono flag.
        Cheap to copy (just paths/numbers, no audio data)."""
        self._save_active_bank_state()
        return {
            "slots": copy.deepcopy(self.slots),
            "force_mono": {b: v.get() for b, v in self.force_mono_vars.items()},
        }

    def _push_undo(self):
        """Call BEFORE a structural change (load/remove sample, delete bank,
        load preset) so that state can be restored afterward. Caps history
        at MAX_UNDO_STEPS and clears redo, since a new action invalidates
        whatever was previously undone."""
        snapshot = self._snapshot_state()
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > MAX_UNDO_STEPS:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

    def _restore_snapshot(self, snapshot):
        self.clear_playback_waveform()
        self.slots = copy.deepcopy(snapshot["slots"])
        for bank, value in snapshot["force_mono"].items():
            if bank in self.force_mono_vars:
                self.force_mono_vars[bank].set(value)
        self.build_pad_slots(self.current_bank.get())
        self.update_storage_display()
        self.update_pad_warnings()

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot_state())
        if len(self._redo_stack) > MAX_UNDO_STEPS:
            self._redo_stack.pop(0)
        snapshot = self._undo_stack.pop()
        self._restore_snapshot(snapshot)
        self._update_undo_redo_buttons()

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot_state())
        if len(self._undo_stack) > MAX_UNDO_STEPS:
            self._undo_stack.pop(0)
        snapshot = self._redo_stack.pop()
        self._restore_snapshot(snapshot)
        self._update_undo_redo_buttons()

    def _update_undo_redo_buttons(self):
        if hasattr(self, "undo_btn"):
            self.undo_btn.config_state("normal" if self._undo_stack else "disabled")
        if hasattr(self, "redo_btn"):
            self.redo_btn.config_state("normal" if self._redo_stack else "disabled")

    def _is_temp_path(self, filepath):
        try:
            abs_fp = os.path.abspath(filepath)
            abs_temp = os.path.abspath(TEMP_DIR)
            return abs_fp == abs_temp or abs_fp.startswith(abs_temp + os.sep)
        except Exception:
            return False

    def clear_pads_referencing_missing_files(self):
        """After clearing the temp folder, any pad still pointing at one of
        those now-deleted files (a trim/chop/normalize result) should
        visually reflect that immediately - name, mini waveform, everything
        - rather than keep showing stale info until something unrelated
        (like a bank switch) happens to re-check it. Only touches pads
        whose file was actually inside the temp folder, so an unrelated
        missing file (e.g. a source on an unplugged drive) is left alone."""
        self._save_active_bank_state()
        cleared_count = 0
        for bank in BANKS:
            pad_states = self.slots.get(bank, {})
            for pad in PADS:
                state = pad_states.get(pad)
                if not state or not state.get("filepath"):
                    continue
                filepath = state["filepath"]
                if self._is_temp_path(filepath) and not os.path.exists(filepath):
                    self.slots[bank][pad] = None
                    cleared_count += 1
        self.build_pad_slots(self.current_bank.get())
        self.update_storage_display()
        self.update_pad_warnings()
        return cleared_count

    def bank_has_samples(self, bank):
        for pad in PADS:
            state = self._get_pad_state(bank, pad)
            if state and state.get("filepath"):
                return True
        return False

    def _bank_dropdown_color(self, bank):
        """Accent color for a bank in the Bank dropdown if it currently
        holds any samples - gives an at-a-glance overview of which of the
        8 banks are occupied without having to click through each one."""
        return ACCENT_BLUE if self.bank_has_samples(bank) else None

    def save_preset_to_folder(self, target_dir, name, banks_to_save):
        """Saves the given banks into <target_dir>/<name>/, copying each
        pad's sample into BANK_X/PAD_Y/ alongside a preset.json manifest.
        If a preset of that name already exists, only the checked banks are
        overwritten - banks not selected keep whatever was previously saved
        for them (partial overwrite)."""
        preset_dir = os.path.join(target_dir, name)
        os.makedirs(preset_dir, exist_ok=True)

        existing = read_preset_manifest(preset_dir) or {}
        banks_data = existing.get("banks", {})

        self._save_active_bank_state()  # capture live edits before reading state

        for bank in banks_to_save:
            bank_dir = os.path.join(preset_dir, f"BANK_{bank}")
            # Copy everything into a STAGING folder first, and only replace
            # bank_dir once every copy has succeeded. This matters because a
            # pad's live filepath can itself point INTO bank_dir (e.g. this
            # exact preset was loaded earlier, then re-saved) - deleting
            # bank_dir up front would destroy the very file we're about to
            # read from, silently turning it into a dangling reference.
            staging_dir = os.path.join(preset_dir, f".BANK_{bank}_staging_{uuid.uuid4().hex[:6]}")
            os.makedirs(staging_dir, exist_ok=True)

            pad_entries = {}
            for pad in PADS:
                state = self._get_pad_state(bank, pad)
                if not state or not state.get("filepath") or not os.path.exists(state["filepath"]):
                    pad_entries[str(pad)] = None
                    continue
                src = state["filepath"]
                pad_dir = os.path.join(staging_dir, f"PAD_{pad}")
                os.makedirs(pad_dir, exist_ok=True)
                dest = os.path.join(pad_dir, os.path.basename(src))
                try:
                    shutil.copy2(src, dest)
                except Exception as e:
                    print(f"Could not copy sample for BANK_{bank}/PAD_{pad}: {e}")
                    pad_entries[str(pad)] = None
                    continue
                # The P-6's own .PRM settings file, if this sample still has
                # one. A preset is meant to be self-contained, and without
                # this the settings survived the import into the temp folder
                # but were dropped the moment the bank was saved as a preset
                # - so loading that preset and exporting it back to the
                # device silently lost them.
                prm_src = self._find_prm_for(src)
                if prm_src:
                    try:
                        shutil.copy2(prm_src, os.path.splitext(dest)[0] + ".PRM")
                    except Exception as e:
                        # Losing the settings file costs the pad its saved
                        # parameters, not its audio - keep the pad.
                        print(f"Could not copy settings file for "
                              f"BANK_{bank}/PAD_{pad}: {e}")
                rel_path = f"BANK_{bank}/" + os.path.relpath(dest, staging_dir).replace(os.sep, "/")
                pad_entries[str(pad)] = {
                    "filepath": rel_path,
                    "target_rate": state.get("target_rate"),
                    "pitch_cents": state.get("pitch_cents", 0),
                    "mono": state.get("mono", False),
                    "display_name": state.get("display_name") or os.path.basename(src),
                }

            # Only now, with every sample safely copied out, swap the old
            # bank folder for the new one. If this step fails (disk full,
            # permissions, AV interference), clean up the staging folder so
            # it doesn't accumulate as hidden junk, and skip this bank
            # rather than aborting the entire save.
            try:
                if os.path.isdir(bank_dir):
                    shutil.rmtree(bank_dir, ignore_errors=True)
                os.rename(staging_dir, bank_dir)
            except Exception as e:
                shutil.rmtree(staging_dir, ignore_errors=True)
                print(f"Could not finalize BANK_{bank} in preset: {e}")
                continue

            # force_mono is stored PER BANK, not as one global flag - each
            # bank can have its own mono/stereo intent.
            banks_data[bank] = {
                "pads": pad_entries,
                "force_mono": self.bank_force_mono(bank),
            }

        manifest = {
            "format_version": PRESET_FORMAT_VERSION,
            "banks": banks_data,
        }
        write_preset_manifest(preset_dir, manifest)
        add_recent_preset(preset_dir)
        return preset_dir

    def load_preset_from_folder(self, preset_dir, banks_to_load, target_bank_override=None):
        """Loads the checked banks from a preset folder. Banks not present
        in the manifest, or not checked, are left untouched. If
        target_bank_override is set (only meaningful with a single bank in
        banks_to_load), that bank's data is loaded into the override bank
        instead of its own original letter."""
        manifest = read_preset_manifest(preset_dir)
        if not manifest:
            dark_showerror("Invalid Preset", "No valid preset.json found in this folder.")
            return False
        self._push_undo()

        banks_data = manifest.get("banks", {})
        self._save_active_bank_state()
        # A preset replaces whole banks of pads, so anything currently shown
        # in the main waveform view is about to become stale - clear it, the
        # same way bank switching / eject / delete bank already do.
        self.clear_playback_waveform()
        missing_samples = []

        for bank in banks_to_load:
            bank_entry = banks_data.get(bank)
            if bank_entry is None:
                continue
            pad_entries = bank_entry.get("pads", {})
            target_bank = target_bank_override or bank
            new_slot = {}
            for pad in PADS:
                entry = pad_entries.get(str(pad))
                if not entry:
                    new_slot[pad] = None
                    continue
                rel_path = entry.get("filepath", "")
                abs_path = os.path.normpath(os.path.join(preset_dir, *rel_path.split("/"))) if rel_path else ""
                if not rel_path or not os.path.exists(abs_path):
                    missing_samples.append(f"BANK_{target_bank}/PAD_{pad} (from BANK_{bank})")
                    new_slot[pad] = None
                    continue
                new_slot[pad] = {
                    "filepath": abs_path,
                    "target_rate": entry.get("target_rate") or 44100,
                    "pitch_cents": entry.get("pitch_cents", 0),
                    "mono": entry.get("mono", False),
                    "from_sync": False,
                    "display_name": entry.get("display_name"),
                }
            self.slots[target_bank] = new_slot
            # force_mono is per-bank - the loaded bank's flag follows it to
            # wherever it lands (its own slot, or the override target). The
            # checkbox auto-updates since it's bound to this same BooleanVar.
            if target_bank in self.force_mono_vars:
                self.force_mono_vars[target_bank].set(bool(bank_entry.get("force_mono", False)))

        self.build_pad_slots(self.current_bank.get())
        self.on_force_mono_changed()  # refreshes pad locks/waveforms/storage for the new state
        add_recent_preset(preset_dir)

        if missing_samples:
            dark_showwarning(
                "Some Samples Missing",
                "The following pads reference sample files that could not be found:\n\n"
                + "\n".join(missing_samples)
            )
        return True

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
        if not hasattr(self, "warnings_text"):
            return
        bank = self.current_bank.get()
        global_force = self.bank_force_mono(bank)
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
        self._pad_warnings_text = "\n".join(messages)
        self._refresh_warnings_display()

    def _autohide_warn_scrollbar(self, first, last):
        """yscrollcommand callback - just positions the scrollbar thumb.
        Visibility itself is decided by _update_warn_scrollbar_visibility()
        based on actual line count, not this fraction (which can land a
        hair under 1.0 from sub-pixel rounding even when content visually
        fits, causing the scrollbar to appear one line too early)."""
        self.warn_scrollbar.set(first, last)

    def _update_warn_scrollbar_visibility(self):
        try:
            line_count = int(self.warnings_text.count("1.0", "end", "displaylines")[0])
        except (TypeError, IndexError):
            line_count = 1
        if line_count > 3:
            if not self.warn_scrollbar.winfo_ismapped():
                self.warn_scrollbar.pack(side="right", fill="y")
        else:
            if self.warn_scrollbar.winfo_ismapped():
                self.warn_scrollbar.pack_forget()

    def _refresh_warnings_display(self):
        """Rebuilds the scrollable warnings text area from its two sources
        (storage-limit hint, per-pad length warnings), each with its own
        color via tags."""
        self.warnings_text.config(state="normal")
        self.warnings_text.delete("1.0", tk.END)
        if self._storage_hint_text:
            self.warnings_text.insert(tk.END, self._storage_hint_text + "\n", "storage")
        if self._pad_warnings_text:
            self.warnings_text.insert(tk.END, self._pad_warnings_text, "padwarn")
        self.warnings_text.config(state="disabled")
        self._update_warn_scrollbar_visibility()

    def on_force_mono_changed(self):
        """This bank's Force Mono toggled: refresh every pad's waveform/
        warnings, lock/unlock their individual Mono checkboxes, and stop
        playback (settings changed)."""
        self.stop_playback_waveform()
        for pad in PADS:
            self.pad_widgets[pad].update_mono_lock()
            self.pad_widgets[pad].update_mini_waveform()
        self.update_storage_display()
        self.update_pad_warnings()
        if self.bank_force_mono(self.current_bank.get()):
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
        global_force = self.bank_force_mono(bank)
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
            self._storage_hint_text = f"Max. total file size per upload is {limit_mb:.0f} MB"
        else:
            self._storage_hint_text = ""
        self._refresh_warnings_display()

    def switch_bank(self, bank):
        self._save_active_bank_state()
        self.clear_playback_waveform()
        self.force_mono_cb.config(variable=self.force_mono_vars[bank])
        self.build_pad_slots(bank)

    @staticmethod
    def _find_prm_for(filepath):
        """The .PRM settings file the P-6 stores next to a sample, if this
        sample still has one. Matched on the base name, which is how the
        device pairs them."""
        base = os.path.splitext(filepath)[0]
        for ext in (".PRM", ".prm"):
            if os.path.isfile(base + ext):
                return base + ext
        return None

    def banks_with_prm_files(self, banks):
        """Which of `banks` have at least one pad whose sample still carries
        its .PRM settings file. Used to ask about copying them only when
        there's actually something to copy."""
        found = []
        for bank in banks:
            for pad in PADS:
                state = self._get_pad_state(bank, pad)
                if state and state.get("filepath") and self._find_prm_for(state["filepath"]):
                    found.append(bank)
                    break
        return found

    def export_bank(self, bank, include_prm=False):
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
            try:
                os.makedirs(pad_path, exist_ok=True)
            except Exception as e:
                # e.g. device unplugged mid-export, read-only mount, disk full.
                # Report and skip this pad rather than aborting the whole run.
                dark_showerror("Copy Error", f"PAD_{pad}: could not create folder:\n{e}")
                skipped += 1
                continue

            try:
                pad_mono = self.bank_force_mono(bank) or state.get("mono", False)
                export_path = compute_export_ready_path(
                    filepath, state.get("target_rate") or 44100, state.get("pitch_cents", 0),
                    pad_mono)
            except Exception:
                export_path = filepath

            dest = os.path.join(pad_path, os.path.basename(export_path))
            # Settings file for THIS pad, if the sample still has one and the
            # user opted in. Named after the exported sample so the device
            # pairs them up the same way it does its own exports.
            prm_src = self._find_prm_for(filepath) if include_prm else None
            prm_dest = os.path.splitext(dest)[0] + ".PRM" if prm_src else None

            try:
                keep = {os.path.abspath(export_path)}
                if prm_dest:
                    keep.add(os.path.abspath(prm_dest))
                for existing_file in os.listdir(pad_path):
                    existing_full = os.path.join(pad_path, existing_file)
                    if os.path.abspath(existing_full) not in keep:
                        try:
                            os.remove(existing_full)
                        except Exception as e:
                            print(f"Could not delete old file ({existing_full}): {e}")
            except Exception as e:
                print(f"Could not read pad folder ({pad_path}): {e}")

            if prm_src:
                try:
                    if os.path.abspath(prm_src) != os.path.abspath(prm_dest):
                        shutil.copy2(prm_src, prm_dest)
                except Exception as e:
                    # A missing settings file costs the pad its saved
                    # parameters, not its audio - don't fail the export.
                    print(f"PAD_{pad}: could not copy settings file: {e}")

            if os.path.abspath(export_path) == os.path.abspath(dest):
                copied += 1
                continue

            try:
                shutil.copy2(export_path, dest)
                copied += 1
            except Exception as e:
                dark_showerror("Copy Error", f"PAD_{pad}: {e}")

        return copied, skipped

    def open_clear_banks_dialog(self):
        """Asks which banks to empty, then clears them in one undo step."""
        self._save_active_bank_state()
        dialog = ClearBanksDialog(self.root, self)
        self.root.wait_window(dialog)
        if not dialog.confirmed:
            return
        banks = dialog.selected_banks()
        if banks:
            self.clear_banks(banks)

    def clear_banks(self, banks):
        """Resets the given banks' pad assignments and settings
        (rate/pitch/mono, Force Mono) back to empty - purely in-app, no
        files are touched on disk. Safe and fully covered by undo, so no
        confirmation is needed.

        One undo entry for the whole operation, not one per bank, so a
        single Ctrl+Z brings everything back."""
        self.clear_playback_waveform()
        self._push_undo()
        active = self.current_bank.get()
        for bank in banks:
            if bank == active:
                # The live widgets are the source of truth for the bank on
                # screen, so it has to be cleared through them, not just in
                # the stored dict - otherwise the pads would still show the
                # old samples until the next bank switch.
                for pad in PADS:
                    self.pad_widgets[pad].clear_pad()
            self.slots[bank] = {p: None for p in PADS}
            if bank in self.force_mono_vars:
                self.force_mono_vars[bank].set(False)
        self.update_storage_display()
        self.update_pad_warnings()
        bank_word = "Bank" if len(banks) == 1 else "Banks"
        self.show_status(f"{bank_word} {', '.join(banks)} cleared.")

    def wipe_import_folder(self):
        """Permanently deletes every sample file already copied to the
        device, across ALL banks (not just the active one). This only
        touches the device-side files - pad assignments in the app itself
        point at their original source files elsewhere and are unaffected,
        so nothing here needs undo. It IS a real, irreversible disk
        operation though, so this keeps a confirmation dialog."""
        files_found = []
        for bank in BANKS:
            bank_path = os.path.join(self.import_root, f"BANK_{bank}")
            if not os.path.isdir(bank_path):
                continue
            for pad in PADS:
                pad_path = os.path.join(bank_path, f"PAD_{pad}")
                if os.path.isdir(pad_path):
                    try:
                        for fname in os.listdir(pad_path):
                            if fname.lower().endswith((".wav", ".mp3")):
                                files_found.append(f"BANK_{bank}/PAD_{pad}/{fname}")
                    except Exception as e:
                        print(f"Could not read {pad_path}: {e}")

        if not files_found:
            self.show_status("The IMPORT folder contains no samples in any bank.", kind="info")
            return

        warning_lines = [
            f"Really delete all {len(files_found)} sample file(s) across every bank from:",
            self.import_root,
            "",
            "This permanently removes them from the device and cannot be undone.",
            "",
            "(Your pad assignments in the app itself are not affected - only files "
            "already copied to the device. You can re-export with \"Copy Banks to P6\" "
            "afterward if needed.)",
        ]
        if not dark_askyesno("Confirm Wipe IMPORT Folder", chr(10).join(warning_lines)):
            return

        self._set_busy(True)
        deleted_count = 0
        errors = []
        try:
            for bank in BANKS:
                bank_path = os.path.join(self.import_root, f"BANK_{bank}")
                if not os.path.isdir(bank_path):
                    continue
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
                                        errors.append(f"BANK_{bank}/{fname}: {e}")
                        except Exception as e:
                            errors.append(f"BANK_{bank}/PAD_{pad}: {e}")
        finally:
            self._set_busy(False)

        if errors:
            dark_showerror("Partial Errors", "Some files could not be deleted:\n" + chr(10).join(errors))
        else:
            self.show_status(f"{deleted_count} file(s) permanently removed from the device across all banks.")

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

    def open_import_bank_dialog(self):
        self._save_active_bank_state()
        ImportBankDialog(self.root, self)

    def open_copy_banks_dialog(self):
        self._save_active_bank_state()
        dialog = CopyBanksDialog(self.root, self)
        self.root.wait_window(dialog)
        if not dialog.confirmed:
            return
        banks = dialog.selected_banks()
        if not banks:
            return
        if not self._confirm_if_over_limit(banks):
            return

        # Only ask when there's actually something to carry over. The .PRM
        # files hold the per-pad settings the device itself wrote; copying
        # them restores those on the P-6, but they were saved for the sample
        # as it was imported - so if the sample has been edited since, the
        # settings may no longer match, which is why this is a choice rather
        # than automatic.
        prm_banks = self.banks_with_prm_files(banks)
        include_prm = False
        if prm_banks:
            include_prm = dark_askyesno(
                "Copy Settings Files?",
                f"Bank(s) {', '.join(prm_banks)} still have the P-6's own .PRM settings "
                "files for some pads.\n\nCopy them to the device along with the samples?\n\n"
                "Yes: the pads keep the settings they had on the P-6.\n"
                "No: only the audio is copied, and the device applies its defaults.")

        total_c, total_s = 0, 0
        self._set_busy(True)
        try:
            for i, bank in enumerate(banks, 1):
                self.show_progress(f"Copying Bank {bank} to P6 \u2026 ({i}/{len(banks)})")
                c, s = self.export_bank(bank, include_prm=include_prm)
                total_c += c
                total_s += s
        finally:
            self._set_busy(False)
        bank_word = "bank" if len(banks) == 1 else "banks"
        self.show_status(f"{len(banks)} {bank_word} ({', '.join(banks)}): {total_c} copied, {total_s} empty.")



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
            "pydub not found",
            "The Python package 'pydub' was not found.\n\n"
            "The following will not work:\n"
            "- Sample rate, pitch and mono conversion on export\n"
            "- The Chop feature (building multisamples)\n"
            "- MP3 files (loading, previewing, length display)\n\n"
            "WAV files can still be loaded and exported unchanged.\n"
            "Install with: pip install pydub",
            parent=root
        )
        _pydub_warning_shown = True  # already told them - don't nag again per-feature
    elif not FFMPEG_AVAILABLE:
        dark_showwarning(
            "ffmpeg not found",
            "pydub is installed, but ffmpeg was not found (neither on PATH "
            "nor at /usr/bin/ffmpeg).\n\n"
            "The following will not work:\n"
            "- MP3 files (loading, previewing, length display)\n"
            "- Some internal format checks\n\n"
            "WAV files including rate/pitch/mono conversion and Chop usually still "
            "work, since those don't require ffmpeg.\n"
            "Install with e.g.: apt install ffmpeg / brew install ffmpeg",
            parent=root
        )


if __name__ == "__main__":
    _log_timing("module fully loaded (all imports + class/function defs)")
    LAST_SAMPLE_DIR = load_last_sample_dir()
    apply_saved_ffmpeg_overrides()
    apply_saved_storage_threshold()
    root = None
    if DND_AVAILABLE:
        try:
            root = TkinterDnD.Tk()
        except Exception as e:
            print(f"tkinterdnd2 failed to initialize ({e}), continuing without drag & drop")
            DND_AVAILABLE = False
    if root is None:
        root = tk.Tk()
    _log_timing("tk.Tk() root window created")
    _verify_ui_font(root)
    check_startup_dependencies(root)
    _log_timing("dependency check done")
    app = P6ManagerApp(root)
    _log_timing("P6ManagerApp constructed (full UI built)")

    if DEBUG_STARTUP:
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
