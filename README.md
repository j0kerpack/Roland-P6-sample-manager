# Roland AIRA P-6 Sample Manager - Setup & User Guide

## Overview

This tool is a desktop application (Python + Tkinter) for managing WAV/MP3 samples
across the 8 sample banks (A-H) and 6 pads per bank of the Roland AIRA P-6.
It supports auditioning samples before loading, automatic MP3-to-WAV conversion,
sample rate conversion, syncing with samples already present on the device,
safe deletion of samples or entire banks, and building multi-sample "Chop"
files from several source samples at once.

---

## 1. System Requirements

- Linux (tested on Ubuntu/Debian-based distributions), it should work windows and MacOS (not tested)
- Python 3.10 or newer
- A working audio output device
- ffmpeg installed system-wide (required for MP3 support and for the Chop feature)

---

## 2. Required Components and Installation Commands

### 2.1 System packages (install once, system-wide)

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip ffmpeg libportaudio2 python3-tk
```

| Package         | Purpose                                              |
|-----------------|-------------------------------------------------------|
| python3-venv    | Creates isolated Python virtual environments           |
| ffmpeg          | Backend used by pydub for MP3 <-> WAV conversion and for building Chop files |
| libportaudio2   | Native audio backend required by sounddevice           |
| python3-tk      | Tkinter GUI toolkit used for the application window     |

### 2.2 Create and activate a Python virtual environment

```bash
cd ~
python3 -m venv p6env
source p6env/bin/activate
```

Your terminal prompt should now show `(p6env)` in front of the username,
indicating the virtual environment is active.

### 2.3 Python packages (installed inside the virtual environment)

With `p6env` activated, install the following Python libraries via pip:

```bash
pip install sounddevice soundfile pydub matplotlib numpy
```

| Library      | Purpose                                                        |
|--------------|-----------------------------------------------------------------|
| sounddevice  | Plays audio for the preview/audition feature                    |
| soundfile    | Reads WAV files and audio data for playback and waveform rendering |
| pydub        | Converts MP3 files to WAV, handles sample rate conversion, and builds combined Chop files (requires ffmpeg) |
| matplotlib   | Renders the waveform preview image shown in the sample browser  |
| numpy        | Numerical/array math library used internally by matplotlib and for processing audio sample arrays (waveform downsampling, mono conversion, etc.) |

Note: `tkinter`, `os`, `shutil`, `wave`, `contextlib`, `json`, and `uuid` are part
of the Python standard library and require no separate installation (as long as
`python3-tk` was installed in step 2.1).

---

## 3. File Placement

Save the application script as:

```
~/Rolandp6Sampletool.py
```

The tool stores its configuration (last used IMPORT folder and last used sample
source folder) in:

```
~/.p6tool_config.json
```

This file is created and updated automatically; no manual setup is required.

---

## 4. Starting the Application

Every time you open a new terminal session, the virtual environment must be
reactivated before running the tool:

```bash
cd ~
source p6env/bin/activate
python3 -u ./Rolandp6Sampletool.py
```

To verify the syntax of the script without running it (useful after manual edits):

```bash
python3 -m py_compile ~/Rolandp6Sampletool.py && echo "Syntax OK"
```

---

## 5. Using the Application

### 5.1 Selecting the IMPORT folder

On first launch, the tool defaults to:

```
/run/media/<user>/P-6/IMPORT
```

Use the "Choose Folder" button to point the tool to your P-6's actual IMPORT
folder if it differs, or if the device is mounted under a different path.
This selection is remembered across restarts.

### 5.2 Loading a sample onto a pad

Click "Load..." on any pad. A custom file browser opens that lets you:

- Navigate folders (double-click "[Folder] foldername" to enter, ".." to go up)
- Preview/audition a selected WAV or MP3 file with the "Preview" button, or
  enable "Autoplay" to hear each file automatically as you click on it
- See a rendered waveform image of the selected file
- Stop playback with "Stop"
- Confirm your choice with "Select" or by double-clicking the file

The last folder you loaded a sample from is remembered and automatically
reopened the next time you click "Load...", even across app restarts or
after deleting a bank.

### 5.3 Setting sample rate per pad

Each pad has a dropdown to choose the target sample rate (44100, 22050, 14700,
or 11025 Hz). If the sample duration exceeds the P-6's maximum recording time
for that rate/channel combination, a warning is displayed directly under the
pad.

### 5.4 Playing back a loaded sample

Click "Play" next to any pad to hear the currently loaded sample directly in
the app (independent of the audition feature in the load dialog).

### 5.5 Removing a sample from a pad

Click "Remove". If a matching file already exists in the corresponding PAD
folder on the device, you will be asked whether to also permanently delete
that file from the device.

### 5.6 Exporting to the device

- "Copy Current Bank": exports all loaded pads in the currently selected bank
  to the device, converting sample rates as configured. Any pre-existing file
  in a pad folder is automatically replaced (not duplicated).
- "Copy ALL Banks": performs the same export for every bank A-H.

### 5.7 Syncing from the device

Click "Sync with Device" to scan the device's IMPORT folder and load any
existing samples per bank/pad into the GUI, so you can see and manage what is
already present.

### 5.8 Deleting an entire bank

Click "Delete Bank" to remove all samples in the currently selected bank. A
confirmation dialog lists every file that will be deleted before anything is
removed. This action cannot be undone.

### 5.9 Chop feature - building a multi-sample from several files

Click "Chop..." on any pad to open the Chop dialog. This lets you combine
several short samples (e.g. a folder of one-shot kicks, snares, or hi-hats)
into a single WAV file that is ready to be split into equal slices using the
P-6's built-in **Chop** function in Sample Edit (Voice) mode.

**Credit:** This feature is inspired by and conceptually based on the
excellent command-line tool
[p6-wave-slice](https://github.com/warreneblackwell/p6-wave-slice) by
**Warren Blackwell**, which solves the same problem (batch-preparing samples
for the P-6's Chop workflow) as a standalone Go CLI utility. If you prefer a
scriptable, terminal-only workflow instead of this GUI tool, or want to
process very large sample libraries in bulk, check out his project directly.

**Workflow inside the dialog:**

1. Navigate to a folder containing your source WAV/MP3 files.
2. Select one or more files in the list and click "Add Selected" to add them
   to the build queue (or double-click a file to toggle it in/out).
3. Optionally enable "Autoplay" to hear each file as you click on it, or use
   "Preview" / "Stop" manually.
4. Choose the number of **Slices** (1, 2, 4, 8, 16, 24, 32, 48, or 64) - this
   must match what you plan to use in the P-6's Chop mode later.
5. Choose the target **Sample Rate** (44100, 22050, 14700, or 11025 Hz) and
   whether the output should be **Stereo** or Mono.
6. Optionally enable **Normalize** to maximize the volume of the combined
   output file.
7. Click "Build Multisample". The tool will:
   - Resample and convert channels for every selected file as configured
   - Trim leading silence from the start of each sample
   - Truncate any sample that is longer than its allotted slice duration, or
     pad it with silence if it is shorter, so every slice has exactly the
     same length
   - Concatenate all slices into a single WAV file in the correct order
   - Optionally normalize the combined result
8. If you selected more files than fit into the chosen number of slices, you
   will be warned and only the first N files (in list order) will be used.
9. The finished file is loaded directly onto the pad you opened Chop from, so
   you can preview it immediately or export it to the device like any other
   sample.

**How the slice duration is calculated:** the maximum total recording time for
a given sample rate/channel combination (see table below) is divided by the
number of slices you choose. For example, 32 slices at 44100 Hz/Mono gives
each slice about 184 ms; the same 32 slices at 44100 Hz/Stereo only gives
about 92 ms per slice, since stereo audio uses twice the storage per second.

| Sample Rate | Channels | Max Duration | 32 Slices | 64 Slices |
|-------------|----------|--------------|-----------|-----------|
| 44.1 kHz    | Mono     | 5.9s         | 184ms     | 92ms      |
| 22.05 kHz   | Mono     | 11.8s        | 369ms     | 184ms     |
| 14.7 kHz    | Mono     | 17.8s        | 556ms     | 278ms     |
| 11.025 kHz  | Mono     | 23.7s        | 741ms     | 370ms     |
| 44.1 kHz    | Stereo   | 2.95s        | 92ms      | 46ms      |
| 22.05 kHz   | Stereo   | 5.9s         | 184ms     | 92ms      |

**Tip:** for kicks, 80ms+ per slice is usually enough; for snares, aim for
120ms+. Use a lower sample rate or mono output if you need longer slices, or
reduce the number of slices.

After building the file with this tool, load it onto the P-6 as usual, enter
Sample Edit (Voice) mode, and use the device's own **Chop** function to split
it into the same number of slices you chose here - each slice will then be
mapped to a note starting at C4.

---

## 6. Troubleshooting

| Symptom                                             | Likely cause / fix                                                  |
|------------------------------------------------------|----------------------------------------------------------------------|
| `ModuleNotFoundError: No module named 'sounddevice'` | Virtual environment not activated, or packages not installed         |
| `ModuleNotFoundError: No module named 'numpy'`       | numpy not installed in the venv; run `pip install numpy`             |
| MP3 preview/conversion fails                          | ffmpeg not installed or not found; verify with `ffmpeg -version`     |
| Chop dialog shows "pydub missing" error               | pydub and/or ffmpeg not installed; run `pip install pydub` and install ffmpeg |
| `grab failed: window not viewable` on opening Load/Chop dialog | Update to the latest script version (safe-grab retry fix applied) |
| `SyntaxError` after manually editing the script       | Re-copy the full script exactly; avoid partial copy-paste of f-strings|
| Samples not detected during sync                      | Confirm the IMPORT folder path is correct via "Choose Folder"        |
| Chop output sounds heavily cut off                    | Your chosen slice count is too high for the sample length/rate; reduce slice count or use a lower sample rate |

---

## 7. Quick Reference: Full Setup From Scratch

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip ffmpeg libportaudio2 python3-tk

cd ~
python3 -m venv p6env
source p6env/bin/activate
pip install sounddevice soundfile pydub matplotlib numpy

# Place Rolandp6Sampletool.py in ~/

python3 -u ./Rolandp6Sampletool.py
```

---

## 8. Credits

- Roland AIRA P-6 Sample Manager - custom GUI tool for managing samples,
  banks, pads, and building Chop-ready multi-samples.
- Chop/multi-sample concept inspired by
  [p6-wave-slice](https://github.com/warreneblackwell/p6-wave-slice) by
  **Warren Blackwell**, a command-line utility that batch-processes WAV
  samples into P-6 Chop-ready files. Check out his project for a
  scriptable, GUI-free alternative workflow.
