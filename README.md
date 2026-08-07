# PyP6 - Roland P-6 Sample Manager
![Roland-P6-sample-manager](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6-Roland-P6-Sample-Manager%202.0.3.png)

**Version 2.0.3**

## Overview

PyP6 is a free desktop application (Python + Tkinter) for managing WAV/MP3 samples
across the 8 sample banks (A-H) and 6 pads per bank of the Roland AIRA P-6.
It supports auditioning samples before loading, automatic MP3-to-WAV
conversion, per-pad sample rate/pitch/mono conversion, syncing with samples
already present on the device, safe deletion of samples or entire banks, and
building multi-sample "Chop" files from several source samples at once.

> **Windows users:** a prebuilt **Windows x64 executable** is available,
> built with PyInstaller and bundling **all dependencies, including
> ffmpeg/ffprobe**. No Python installation, pip packages, or separate
> ffmpeg setup are required - just download and run. See Section 2.1.

---

## What's New in 2.0.3

- **Pitch control per pad** - shift each pad's sample by up to ±1200 cents
  (in 100-cent steps) via +/- buttons or direct entry. Pitch is applied on
  Play/Preview/Export and factored into the duration-limit warning.
- **Mono conversion** - a global "Force Mono (all pads)" switch, plus an
  independent Mono checkbox per pad, for forcing stereo samples down to mono
  where the P-6's recording-time limit requires it.
- **Dark / Bright theme** - switch the entire UI theme in Settings (restart
  required to apply).
- **Central Settings dialog** - replaces the old scattered top-bar controls.
  Covers the IMPORT folder, theme, manual ffmpeg/ffprobe path overrides,
  default autoplay behavior, default Chop slice count, and a configurable
  storage warning threshold (in MB).
- **Consolidated startup dependency check** - a single, clear dialog on
  launch if pydub or ffmpeg is missing, instead of scattered errors the
  first time each affected feature is touched.
- **Zoomable waveform view** - zoom in/out (up to 30x) on the Chop dialog's
  waveform for precise trimming of long or detailed samples, with
  scrollbar/mouse-wheel panning.
- **Mini waveform preview per pad** - each pad now shows a small static
  waveform of its loaded sample, turning orange if the sample (including
  pitch and mono settings) would exceed the P-6's recording-time limit.
- **Improved file lists** - Browse/Selected lists in the Chop dialog are now
  proper sortable columns (Name / Length / Size) instead of fixed-width text.
- **Smarter Chop padding** - if you select fewer files than the chosen slice
  count, the remaining slices are now automatically filled with silence
  instead of producing a shorter-than-expected output file.
- **Storage limit confirmation** - exporting a bank (or all banks) that
  exceeds your configured storage threshold now asks for confirmation first,
  listing exactly which banks are over the limit.
- **16-bit PCM enforcement** - samples with a different bit depth (e.g.
  24-bit or 32-bit source files) are automatically converted to 16-bit PCM
  on export, as required by the P-6.
- **Non-blocking startup** - IMPORT folder auto-detection now runs in the
  background, so the UI opens immediately instead of freezing on slow
  filesystems (e.g. stale `/media` automounts on Linux).
- **Standalone Windows executable** - a prebuilt, self-contained
  `.exe` (x64) is now available, with ffmpeg/ffprobe and all other
  dependencies bundled directly inside it via PyInstaller. See Section 2.1.

---

## 1. System Requirements

- Windows, Linux (tested on Ubuntu/Debian-based distributions), or macOS
- Python 3.10 or newer (3.13+ requires one extra package, see 2.3) -
  **not needed if you use the prebuilt Windows executable**
- A working audio output device
- ffmpeg (required for MP3 support, pitch/rate/mono conversion, and the Chop
  feature; WAV-only workflows can run without it, with reduced functionality)
  - **already bundled** in the prebuilt Windows executable, no separate
    install needed

---

## 2. Installation (Windows)

### 2.1 Option A: Prebuilt Windows x64 Executable (recommended, no setup)

A standalone `.exe` is provided for 64-bit Windows. It is built with
PyInstaller in `--onefile` mode and has **every dependency bundled inside
it**, including Python itself, pydub, sounddevice/soundfile, and
**ffmpeg.exe / ffprobe.exe**. There is nothing else to install.

1. Download the `.exe`.
2. Double-click to run it - no Python, pip, or ffmpeg setup required.
3. Windows SmartScreen or your antivirus may flag an unsigned executable on
   first run; choose "Run anyway" / allow it if you trust the source.

This is the simplest way to get started on Windows and is the recommended
option unless you specifically want to run from source (e.g. to modify the
code yourself - see Option B, or Section 7 to build your own `.exe`).

### 2.2 Option B: Run from Source

If you prefer to run the Python script directly instead of the prebuilt
executable:

#### 2.2.1 Install Python

Download and install Python from [python.org](https://www.python.org/downloads/).
Make sure you run commands in the **Command Prompt / PowerShell**, not inside
the interactive Python console (the `>>>` prompt) - `pip` commands only work
in a regular terminal.

#### 2.2.2 Install Python packages

```
pip install sounddevice soundfile numpy pydub
```

**Python 3.13 and newer** removed the `audioop` module that `pydub` depends
on internally. Install the backport as well:

```
pip install audioop-lts
```

> Note: `matplotlib` is no longer required as of this version.

#### 2.2.3 Install ffmpeg

1. Download a Windows build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
   (the "essentials" or "full" build).
2. Extract it, e.g. to `C:\ffmpeg`, so that `C:\ffmpeg\bin` contains
   `ffmpeg.exe` and `ffprobe.exe`.
3. Add `C:\ffmpeg\bin` to your **Path** user environment variable:
   - Press the Windows key, type "environment variables", open
     "Edit environment variables"
   - Under "User variables", select **Path** → **Edit...** → **New** →
     enter `C:\ffmpeg\bin` → OK on all dialogs
   - Close and reopen any terminal windows for the change to take effect
4. Verify with:
   ```
   ffmpeg -version
   ```

Alternatively, PyP6's Settings dialog lets you manually point to
`ffmpeg.exe`/`ffprobe.exe` if you don't want to modify your system PATH.

#### 2.2.4 Run the application

```
python p6_manager.py
```

---

## 3. Installation (Linux)

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip ffmpeg libportaudio2 python3-tk

cd ~
python3 -m venv p6env
source p6env/bin/activate
pip install sounddevice soundfile pydub numpy
```

Run the app (venv must be reactivated in every new terminal session):

```bash
cd ~
source p6env/bin/activate
python3 -u ./p6_manager.py
```

---

## 4. File Placement & Configuration

The tool stores its configuration - last used IMPORT folder, theme, default
autoplay/slices, storage warning threshold, and any manual ffmpeg/ffprobe
path overrides - in:

```
~/.p6tool_config.json
```

(on Windows: `C:\Users\<you>\.p6tool_config.json`)

This file is created and updated automatically; no manual setup is required.
Delete it to reset all saved preferences to defaults. This applies whether
you're running the prebuilt executable or from source.

---

## 5. Using the Application

### 5.1 Selecting the IMPORT folder

On first launch, the tool attempts to auto-detect a mounted P-6 IMPORT
folder. Use **Settings → Change...** to point it to the correct location if
needed. This is remembered across restarts.

### 5.2 Loading a sample onto a pad
![Sample import](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6-Roland-P6-sample%202.0.3-manager-sample-loader.png)
Click "Load" on any pad to open a file browser with folder navigation,
waveform preview, and audition playback (with optional Autoplay). Confirm
with "Select" or by double-clicking a file.

### 5.3 Per-pad sound settings

Each pad offers:

- **Sample Rate** - 44100, 22050, 14700, or 11025 Hz
- **Pitch** - ±1200 cents in 100-cent steps, via +/- buttons, direct entry,
  or "Reset"
- **Mono** - forces this pad's sample to mono (also available globally via
  "Force Mono (all pads)" in the top bar)

A warning appears under the pad, and its mini waveform turns orange, if the
resulting sample would exceed the P-6's maximum recording time for the
chosen rate/channel combination.

### 5.4 Playback and removal

"▶" plays the pad's sample exactly as it will sound after export (rate,
pitch, and mono applied). "⏏" removes it from the pad, optionally deleting
the matching file from the device if one exists.

### 5.5 Exporting to the device

- **Copy Current Bank** / **Copy ALL Banks** - exports loaded pads,
  converting rate/pitch/mono and bit depth as configured. Existing files in
  a pad folder are replaced, not duplicated.
- If a bank (or the total) exceeds your configured storage warning
  threshold, you'll be asked to confirm before copying.
- The P-6 must be in storage mode before upload (hold Record and switch
  power on). After uploading, press a key on the device and wait until the
  display shows "done".

### 5.6 Syncing and deleting

- **Reload IMPORT Folder** scans the device and loads any existing samples
  per bank/pad into the GUI.
- **Delete Bank** removes all samples in the current bank after a
  confirmation listing every file to be deleted. This cannot be undone.

### 5.7 Chop feature - building a multi-sample from several files
![Sample chop slice tool](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6-Roland-P6-sample-manager%202.0.3-Chop-Feature.png)
Click "Chop" on any pad to combine several short samples (e.g. one-shot
kicks, snares, hi-hats) into a single WAV file ready to be split into equal
slices using the P-6's built-in **Chop** function in Sample Edit (Voice)
mode.

**Credit:** This feature is inspired by and conceptually based on the
command-line tool [p6-wave-slice](https://github.com/warreneblackwell/p6-wave-slice)
by **Warren Blackwell**.

**Workflow:**

1. Browse to a folder of source WAV/MP3 files; select and "Add to
   Selection" the ones you want (or double-click to toggle).
2. Preview/audition files; drag the green/red markers on the waveform to
   add only a trimmed region of a sample, and use the zoom controls for
   precise trimming on longer files.
3. Choose the number of **Slices** (1-64), target **Sample Rate**, and
   **Stereo**/Mono.
4. Optionally enable **Normalize**.
5. Click "Build Multisample". The tool resamples/converts each file, trims
   leading silence, truncates or pads each slice to a uniform length, and
   concatenates everything in order. If you selected fewer files than
   slices, the remaining slices are automatically filled with silence so
   the output always matches your chosen slice count.
6. The finished file loads directly onto the pad you opened Chop from.

**Slice duration reference:**

| Sample Rate | Channels | Max Duration | 32 Slices | 64 Slices |
|-------------|----------|--------------|-----------|-----------|
| 44.1 kHz    | Mono     | 5.9s         | 184ms     | 92ms      |
| 22.05 kHz   | Mono     | 11.8s        | 369ms     | 184ms     |
| 14.7 kHz    | Mono     | 17.8s        | 556ms     | 278ms     |
| 11.025 kHz  | Mono     | 23.7s        | 741ms     | 370ms     |
| 44.1 kHz    | Stereo   | 2.95s        | 92ms      | 46ms      |
| 22.05 kHz   | Stereo   | 5.9s         | 184ms     | 92ms      |

After building, load the file onto the P-6 as usual, enter Sample Edit
(Voice) mode, and use the device's own **Chop** function to split it into
the same number of slices chosen here.

### 5.8 Settings

Open Settings (gear icon) for:

- IMPORT folder location
- Dark/Bright theme (restart required)
- Manual ffmpeg/ffprobe path overrides (not needed when using the prebuilt
  executable, since ffmpeg is already bundled inside it)
- Default autoplay behavior for Load/Chop dialogs
- Default Chop slice count
- Storage warning threshold (MB)

---

## 6. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `SyntaxError: invalid syntax` on `pip install ...` | You typed the command inside the Python console (`>>>`). Run `exit()` first, then use a regular terminal. |
| `'pip' is not recognized` | Use `python -m pip install ...` instead. |
| `ModuleNotFoundError: No module named 'audioop'` | Python 3.13+: run `pip install audioop-lts`. Not applicable when using the prebuilt executable. |
| "pydub nicht gefunden" / pydub missing warning | Run `pip install pydub` (and `audioop-lts` on Python 3.13+). Should not occur with the prebuilt executable. |
| "ffmpeg nicht gefunden" warning, but conversion still works | You may be running an outdated build/cache - rebuild with `--clean`, or check Settings for a stale manual ffmpeg path override. |
| MP3 preview/conversion fails | ffmpeg not installed or not on PATH; verify with `ffmpeg -version`. Not applicable when using the prebuilt executable, since ffmpeg is bundled. |
| `'pyinstaller' is not recognized` | Its Scripts folder isn't on PATH; run `python -m PyInstaller ...` instead, or add the Scripts folder to PATH. |
| Windows flags the executable as unrecognized/unsafe | Expected for an unsigned third-party `.exe`; choose "Run anyway" in SmartScreen if you trust the source. |
| Samples not detected during sync | Confirm the IMPORT folder path via Settings. |
| Chop output sounds heavily cut off | Slice count too high for the sample length/rate; reduce slice count or use a lower sample rate/mono. |
| Samples not transferring to the P-6 | Only a limited amount (configurable warning threshold, default 10 MB) can be transferred at once; export banks in smaller groups. |

---

## 7. Building Your Own Standalone Windows Executable (Optional)

If you'd rather build the `.exe` yourself instead of using the prebuilt one
(e.g. after modifying the source), you can package PyP6 with PyInstaller and
bundle ffmpeg/ffprobe the same way the prebuilt executable does:

```
pip install pyinstaller

python -m PyInstaller p6_manager.py -y -w --onefile ^
  --icon=icon.ico ^
  --add-data "logo.png;." ^
  --add-binary "C:\ffmpeg\bin\ffmpeg.exe;." ^
  --add-binary "C:\ffmpeg\bin\ffprobe.exe;." ^
  --clean
```

Notes:

- Use `;` as the separator for `--add-data`/`--add-binary` on Windows (not
  `:`, which is reserved for Unix/macOS paths).
- `--clean` clears PyInstaller's cache before building - use it whenever
  you've changed the source and want to be sure the new build reflects it.
- The app resolves bundled files via `sys._MEIPASS` at runtime, so bundled
  ffmpeg/ffprobe are found automatically without requiring a system PATH
  entry on the end user's machine - this is exactly how the prebuilt
  executable achieves a fully self-contained, dependency-free install.
- Some antivirus software may flag or quarantine a bundled `ffmpeg.exe`
  extracted at runtime; if ffmpeg-dependent features silently stop working
  on a specific machine, check the Windows Defender protection history.

---

## 8. Credits

- Chop/multi-sample concept inspired by
  [p6-wave-slice](https://github.com/warreneblackwell/p6-wave-slice) by
  **Warren Blackwell**, a command-line utility that batch-processes WAV
  samples into P-6 Chop-ready files.
