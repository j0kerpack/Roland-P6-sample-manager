# PyP6 - Roland P-6 Sample Manager
![Roland-P6-sample-manager](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6-Roland-P6-Sample-Manager_2_8_0.png)

**Version 2.8.0** - © 2026 Brian Siemund

## Overview

PyP6 is a free desktop application (Python + Tkinter) for managing WAV/MP3 samples
across the 8 sample banks (A-H) and 6 pads per bank of the Roland AIRA P-6.
It supports auditioning samples before loading, automatic MP3-to-WAV
conversion, per-pad sample rate/pitch/mono conversion, non-destructive
editing (trim, normalize, fade), transfers in both directions between the
app and the device, presets that keep their samples with them, and building
multi-sample "Chop" files from several source samples at once.

> **Windows users:** a prebuilt **Windows x64 executable** is available,
> built with PyInstaller and bundling **all dependencies, including
> ffmpeg/ffprobe**. No Python installation, pip packages, or separate
> ffmpeg setup are required - just download and run. See Section 2.1.

---

## What's New in 2.8.0

- **Tooltips** - hover help on the pad controls, the bank buttons,
  Undo/Redo, and the Chop and Load windows. Switchable in
  Settings → Appearance; the change applies immediately.
- **Chop: three normalize modes** - `Off`, `Per sample` (each slice is
  lifted to full level on its own, for source samples recorded at
  different volumes) and `Whole file` (only the finished multisample is
  lifted, so the balance between slices is preserved). The waveform
  preview and preview playback now show exactly what the chosen mode will
  produce.
- **About dialog** - reachable from Settings. Shows the version, author,
  and the live state of every optional component (pydub, ffmpeg,
  drag & drop, the resolved ffmpeg path, the config and temp folders),
  with a "Copy Info" button for pasting into a bug report.
- **Consistent truncation display** - the part of a sample that exceeds the
  P-6's recording-time limit is shaded orange in *every* waveform view
  (pad mini waveform, main playback waveform, pad editor, Chop). All four
  now derive the cut point from a single shared calculation, so they can't
  disagree.
- **Readable button colors** - on the dark themes the colored buttons are
  derived from the theme accent with the lightness reduced until white
  text is properly legible, and the saturation eased back so a large block
  of color doesn't dominate the window.

### Earlier in the 2.x line

- **Undo / Redo** - 5 steps across all banks, covering loading, removing,
  swapping, applying an edit, clearing a bank and loading a preset.
  `Ctrl+Z` / `Ctrl+Shift+Z` or `Ctrl+Y`.
- **Presets** - save any selection of banks (including their samples) into
  a self-contained preset folder and load them back later. Saving over an
  existing preset only replaces the banks you checked. A "Recent" list
  gives quick access to the last five.
- **Pad waveform editor** - click a pad's mini waveform to open it: trim
  markers, zoom, normalize, and logarithmic fade-in/fade-out, previewed
  live, then written back with "Apply to Pad" (undoable). The pad's rate,
  pitch and mono settings are preserved.
- **Drag to swap pads** - drag a pad's sample name (or its frame) onto
  another pad to exchange the two, including all their settings. The
  target pad is outlined in orange while you drag.
- **Drag & drop from the file manager** - drop audio files straight onto a
  pad (optional, requires `tkinterdnd2`). Reliable on Windows; on Linux it
  works but an occasional drop can be missed under Wayland. See 5.5.
- **Bank ↔ device in both directions** - "Banks → P6" exports any
  selection of banks with a live total-size readout, "P6 → Bank" walks you
  through the device's own export procedure and reads the resulting
  EXPORT folder back into the active bank.
- **Per-bank Force Mono** - the mono switch is stored per bank, so bank A
  can be mono while bank C stays stereo. It travels with the bank into
  presets.
- **Six themes** - `dark`, `tokyo`, `dracula`, `modern`, `latte` and
  `bright`, all checked for readable contrast.
- **Managed temp folder** - trimmed, normalized, faded and chopped
  intermediate files live under `~/.pyp6/temp` and can be inspected and
  cleared from Settings.

---

## 1. System Requirements

- Windows, Linux (tested on Ubuntu/Debian-based distributions), or macOS
- Python 3.10 or newer (3.13+ requires one extra package, see 2.2.2) -
  **not needed if you use the prebuilt Windows executable**
- A working audio output device
- ffmpeg (required for MP3 support, pitch/rate/mono conversion, and the Chop
  feature; WAV-only workflows can run without it, with reduced functionality)
  - **already bundled** in the prebuilt Windows executable, no separate
    install needed
- `tkinterdnd2` (optional) - only needed for dropping files from the file
  manager onto a pad. Everything else works without it. Also already
  bundled in the prebuilt Windows executable.

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

Optional, for dropping files from Explorer onto a pad:

```
pip install tkinterdnd2
```

**Python 3.13 and newer** removed the `audioop` module that `pydub` depends
on internally. Install the backport as well:

```
pip install audioop-lts
```

> Note: `matplotlib` is not required - waveforms are drawn directly on the
> Tkinter canvas.

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
Settings → About shows which ffmpeg binary is actually in use.

#### 2.2.4 Run the application

```
python PyP6-Roland-P6-Sample-Manager_2_8_0.py
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

# optional - drag & drop from the file manager (see the caveat in 5.5)
pip install tkinterdnd2
```

Run the app (the venv must be reactivated in every new terminal session):

```bash
cd ~
source p6env/bin/activate
python3 -u ./PyP6-Roland-P6-Sample-Manager_2_8_0.py
```

> If a feature appears to be missing, check **Settings → About** first - it
> reports which optional components were actually loaded. A package
> installed outside the active venv is the most common cause.

---

## 4. File Placement & Configuration

The tool keeps everything it needs under a single folder:

```
~/.pyp6/config.json     settings
~/.pyp6/temp/           trimmed / normalized / faded / chopped samples
```

(on Windows: `C:\Users\<you>\.pyp6\`)

Saved settings include the last used IMPORT folder, theme, tooltip
visibility, default autoplay and slice count, storage warning threshold,
recent presets, and any manual ffmpeg/ffprobe path overrides. The folder is
created and updated automatically; no manual setup is required. Delete
`config.json` to reset all preferences to defaults.

The **temp folder** holds every edited sample that hasn't been saved into a
preset. Settings → Temporary Files shows its size and can clear it - pads
still pointing at a deleted file are cleared along with it, so save a preset
first if you want to keep those edits.

---

## 5. Using the Application

### 5.1 Selecting the IMPORT folder

On first launch, the tool attempts to auto-detect a mounted P-6 IMPORT
folder in the background, so the window opens immediately. Use
**Settings → IMPORT Folder → Change...** to point it to the correct location
if needed. This is remembered across restarts.

### 5.2 Loading a sample onto a pad
![Sample import](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6-Roland-P6-Sample-Manager_2_8_0_sample_loader.png)

Click "Load" on any pad to open a file browser with folder navigation,
sortable columns (Name / Length / Size), waveform preview and audition
playback (with optional Autoplay). Drag the green and red bracket markers
to load only the marked region, optionally tick **Normalize**, then confirm
with "Select" or by double-clicking a file. Trimmed and normalized results
are written to the temp folder - your original file is never modified.

### 5.3 Per-pad sound settings

Each pad offers:

- **Sample Rate** - 44100, 22050, 14700, or 11025 Hz
- **Pitch** - ±1200 cents in 100-cent steps, via +/- buttons, direct entry,
  or "Reset"
- **Mono** - forces this pad's sample to mono. The bank-wide
  "Force Mono (this bank)" switch in the top bar overrides it and greys it
  out while active.

If the resulting sample would exceed the P-6's maximum recording time for
the chosen rate/channel combination, a warning appears in the central
warnings area and the excess portion is shaded orange in the pad's mini
waveform.

### 5.4 Editing a loaded sample

Click a pad's **mini waveform** to open the editor:

- Drag the green/red markers to shorten the sample, mouse wheel to zoom
- **Normalize** and **Fade In / Fade Out** (0 to 1.0 s in logarithmic
  steps), all previewed live in the waveform
- The header shows both the file's own rate and the pad's export settings,
  and the orange area marks what the P-6 would cut off at those settings
- **Apply to Pad** writes the result to a new temp file and puts it back on
  the pad. The pad's rate, pitch and mono settings are kept, the original
  file is untouched, and `Ctrl+Z` undoes it.

Chop multisamples are a special case: trim and fade are disabled for them,
since either would shift the fixed slice boundaries the device relies on.
Normalize remains available.

### 5.5 Rearranging pads and dropping files

Drag a pad's **sample name** (or the pad frame itself) onto another pad to
swap the two, including their rate, pitch and mono settings. The target pad
is outlined in orange while you drag. Swaps are undoable.

You can also **drop audio files from your file manager** straight onto a
pad; the pad under the cursor is outlined in green while you drag over it.
Dropping several files at once fills the following pads in order. This
requires the optional `tkinterdnd2` package.

> **Platform note:** drag & drop is reliable on **Windows**. On Linux it
> works, but under **Wayland** an occasional drop is missed - the file
> simply doesn't land on the pad. Just drag it again, or use the pad's
> "Load" button. Nothing else in the app is affected by this.
>
> If drops never work at all, that's a different problem:
> **Settings → About** distinguishes the two by showing whether drag & drop
> actually initialised.

### 5.6 Playback and removal

"▶" plays the pad's sample exactly as it will sound after export (rate,
pitch, and mono applied) and mirrors it in the large waveform at the bottom
of the window, with a live playhead; the playing pad is outlined in blue and
its button turns into a Stop square. "⏏" clears the pad, optionally deleting
the matching file from the device if one exists.

### 5.7 Transferring to and from the device

- **Banks → P6** opens a dialog where you tick the banks to export, with a
  live total-size readout so you can see up front how much you're about to
  transfer. Each pad's sample is converted according to its rate/pitch/mono
  settings and written to `IMPORT/BANK_x/PAD_n/`, replacing whatever was
  there. If a bank exceeds your configured storage threshold, you'll be
  asked to confirm.
- **P6 → Bank** walks you through the device's own export procedure and then
  reads the resulting EXPORT folder into the **currently active bank**. The
  device doesn't store rate/pitch/mono in its export, so those come in at
  their defaults - only the audio itself is transferred.
- The P-6 must be in storage mode before an upload (hold Record and switch
  power on). After uploading, press a key on the device and wait until the
  display shows "done".

### 5.8 Clearing

- **Clear Bank** empties all 6 pads of the current bank **in the app only** -
  no files on disk or on the device are touched. Undoable with `Ctrl+Z`.
- **Wipe P6 IMPORT Folder** permanently deletes every sample file in the
  device's IMPORT folder, across all banks, after a confirmation listing
  what will be removed. Your pads in the app stay as they are. This cannot
  be undone.

### 5.9 Presets

Use the **Preset** button in the top bar:

- **Save Preset...** - pick a folder and a name, tick which banks to
  include. The samples themselves are copied into the preset folder, so a
  preset stays usable even after the temp folder is cleared. Saving over an
  existing preset replaces only the banks you checked and leaves its other
  banks alone.
- **Load Preset...** - click a preset folder to see which banks it contains,
  then tick the ones to load. With exactly one bank selected you can load it
  into the *current* bank instead of its original slot. Loading is undoable.
- **Recent** - the last five presets, one click away.

### 5.10 Chop feature - building a multi-sample from several files
![Sample chop slice tool](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6-Roland-P6-Sample-Manager_2_8_0_chop_slice.png)
Click "Chop" on any pad to combine several short samples (e.g. one-shot
kicks, snares, hi-hats) into a single WAV file ready to be split into equal
slices using the P-6's built-in **Chop** function in Sample Edit (Voice)
mode.

**Credit:** This feature is inspired by and conceptually based on the
command-line tool [p6-wave-slice](https://github.com/warreneblackwell/p6-wave-slice)
by **Warren Blackwell**.

**Workflow:**

1. Browse to a folder of source WAV/MP3 files and "Add to Selection" the
   ones you want.
2. Preview/audition files; drag the green/red markers on the waveform to add
   only a trimmed region of a sample, and use the zoom controls for precise
   trimming on longer files. You can pull several separate regions out of
   the same long file this way.
3. Reorder the selection with ↑/↓ (or `Alt+Up` / `Alt+Down`) - that order is
   the slice order on the device. `Del` removes an entry.
4. Choose the number of **Slices** (1-64), the target **Sample Rate**, and
   **Stereo**/Mono. Stereo locks itself once the list isn't empty, so mono
   and stereo entries can't get mixed; clear the selection to change it.
5. Choose a **Normalize** mode:
   - `Off` - levels stay exactly as they are.
   - `Per sample` - every slice is lifted to full level individually. Use
     this when the source samples were recorded at different volumes.
   - `Whole file` - only the finished multisample is lifted; the balance
     between slices is preserved.
6. Click "Build Multisample". The tool resamples/converts each file, trims
   leading silence, truncates or pads each slice to a uniform length, and
   concatenates everything in order. If you selected fewer files than
   slices, the remaining slices are automatically filled with silence so the
   output always matches your chosen slice count.
7. The finished file loads directly onto the pad you opened Chop from.

Anything that would be cut off for exceeding the per-slice time is shaded
orange in the waveform while you work, so you can see it before building.

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

### 5.11 Settings

Open Settings (gear icon) for:

- **IMPORT Folder** - the location on the P-6 drive that "Banks → P6"
  writes to
- **Appearance** - one of six themes (`dark`, `tokyo`, `dracula`, `modern`,
  `latte`, `bright`; restart required), and a switch for tooltips (applies
  immediately)
- **Audio Components** - the state of pydub and ffmpeg, plus manual
  ffmpeg/ffprobe path overrides (not needed with the prebuilt executable,
  where ffmpeg is bundled)
- **Defaults** - autoplay behavior for the Load/Chop dialogs, default Chop
  slice count, and the storage warning threshold in MB
- **Temporary Files** - current size, and "Clear Now"
- **About** - version, author, and the exact state of every optional
  component, with "Copy Info"

---

## 6. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `SyntaxError: invalid syntax` on `pip install ...` | You typed the command inside the Python console (`>>>`). Run `exit()` first, then use a regular terminal. |
| `'pip' is not recognized` | Use `python -m pip install ...` instead. |
| `ModuleNotFoundError: No module named 'audioop'` | Python 3.13+: run `pip install audioop-lts`. Not applicable when using the prebuilt executable. |
| A feature is silently missing | Open **Settings → About**. It reports what actually loaded, which distinguishes "package missing" from "package present but not working". Installing into the wrong venv is the usual cause. |
| pydub missing warning | Run `pip install pydub` (and `audioop-lts` on Python 3.13+). Should not occur with the prebuilt executable. |
| ffmpeg warning, but conversion still works | You may be running an outdated build/cache - rebuild with `--clean`, or check Settings for a stale manual ffmpeg path override. About shows the path actually in use. |
| MP3 preview/conversion fails | ffmpeg not installed or not on PATH; verify with `ffmpeg -version`. Not applicable when using the prebuilt executable, since ffmpeg is bundled. |
| `'pyinstaller' is not recognized` | Its Scripts folder isn't on PATH; run `python -m PyInstaller ...` instead, or add the Scripts folder to PATH. |
| Windows flags the executable as unrecognized/unsafe | Expected for an unsigned third-party `.exe`; choose "Run anyway" in SmartScreen if you trust the source. |
| Samples not detected on the device | Confirm the IMPORT folder path via Settings. |
| A pad went empty on its own | Its sample was an edited file in the temp folder, and the temp folder was cleared. Save edited samples into a preset to keep them. |
| Chop output sounds heavily cut off | Slice count too high for the sample length/rate; reduce the slice count or use a lower sample rate/mono. Anything shaded orange in the waveform is what gets cut. |
| Samples not transferring to the P-6 | Only a limited amount (configurable warning threshold, default 10 MB) can be transferred at once; export banks in smaller groups. |
| Dropping files onto pads never works | Check **Settings → About**: if it says `tkinterdnd2 not available`, run `pip install tkinterdnd2` - into the venv you actually start the app from, which is the usual catch. |
| A drop is occasionally missed on Linux | Known under Wayland; About will show drag & drop as `active`. Drag the file again, or use the pad's "Load" button. |
| Startup problems you want to diagnose | Launch with `PYP6_DEBUG=1` for a timed startup log listing every phase and which optional components loaded. |

---

## 7. Building Your Own Standalone Windows Executable (Optional)

If you'd rather build the `.exe` yourself instead of using the prebuilt one
(e.g. after modifying the source), you can package PyP6 with PyInstaller and
bundle ffmpeg/ffprobe the same way the prebuilt executable does:

```
pip install pyinstaller

python -m PyInstaller PyP6-Roland-P6-Sample-Manager_2_8_0.py -y -w --onefile ^
  --icon=icon.ico ^
  --add-data "pyp6logo.png;." ^
  --collect-data tkinterdnd2 ^
  --add-binary "C:\ffmpeg\bin\ffmpeg.exe;." ^
  --add-binary "C:\ffmpeg\bin\ffprobe.exe;." ^
  --clean
```

Notes:

- Use `;` as the separator for `--add-data`/`--add-binary` on Windows (not
  `:`, which is reserved for Unix/macOS paths).
- `--collect-data tkinterdnd2` is required if you want drag & drop in the
  build: the package ships native Tcl extension files, not just `.py`
  modules, and PyInstaller will not pick those up on its own. Leave the
  line out and the `.exe` builds fine - it just won't accept dropped
  files.
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

---

Roland, AIRA and P-6 are trademarks of Roland Corporation. This is an
independent project and is not affiliated with, endorsed by or supported by
Roland.
