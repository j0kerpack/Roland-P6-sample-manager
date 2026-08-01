# Roland AIRA P-6 Sample Manager - Setup & User Guide

## Overview

This tool is a desktop application (Python + Tkinter) for managing WAV/MP3 samples
across the 8 sample banks (A-H) and 6 pads per bank of the Roland AIRA P-6.
It supports auditioning samples before loading, automatic MP3-to-WAV conversion,
sample rate conversion, syncing with samples already present on the device,
and safe deletion of samples or entire banks.

---

## 1. System Requirements

- Linux (tested on Ubuntu/Debian-based distributions)
- Python 3.10 or newer
- A working audio output device
- ffmpeg installed system-wide (required for MP3 support)

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
| ffmpeg          | Backend used by pydub for MP3 <-> WAV conversion        |
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
pip install sounddevice soundfile pydub
```

| Library      | Purpose                                                        |
|--------------|-----------------------------------------------------------------|
| sounddevice  | Plays audio for the preview/audition feature                    |
| soundfile    | Reads WAV files and audio data for playback                     |
| pydub        | Converts MP3 files to WAV and handles sample rate conversion    |

Note: `tkinter`, `os`, `shutil`, `wave`, `contextlib`, and `json` are part of the
Python standard library and require no separate installation (as long as
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

Use the "Ordner waehlen" (Choose folder) button to point the tool to your P-6's
actual IMPORT folder if it differs, or if the device is mounted under a
different path. This selection is remembered across restarts.

### 5.2 Loading a sample onto a pad

Click "Laden..." (Load...) on any pad. A custom file browser opens that lets
you:

- Navigate folders (double-click "[Ordner] foldername" to enter, ".." to go up)
- Preview/audition a selected WAV or MP3 file with the "Vorhoeren" (Preview) button
- Stop playback with "Stop"
- Confirm your choice with "Auswaehlen" (Select) or by double-clicking the file

The last folder you loaded a sample from is remembered and automatically
reopened the next time you click "Laden...", even across app restarts or
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

Click "Entfernen" (Remove). If a matching file already exists in the
corresponding PAD folder on the device, you will be asked whether to also
permanently delete that file from the device.

### 5.6 Exporting to the device

- "Aktuelle Bank kopieren" (Copy current bank): exports all loaded pads in the
  currently selected bank to the device, converting sample rates as configured.
  Any pre-existing file in a pad folder is automatically replaced (not
  duplicated).
- "ALLE Baenke kopieren" (Copy ALL banks): performs the same export for every
  bank A-H.

### 5.7 Syncing from the device

Click "Sync mit Geraet" (Sync with device) to scan the device's IMPORT folder
and load any existing samples per bank/pad into the GUI, so you can see and
manage what is already present.

### 5.8 Deleting an entire bank

Click "Bank loeschen" (Delete bank) to remove all samples in the currently
selected bank. A confirmation dialog lists every file that will be deleted
before anything is removed. This action cannot be undone.

---

## 6. Troubleshooting

| Symptom                                             | Likely cause / fix                                                  |
|------------------------------------------------------|----------------------------------------------------------------------|
| `ModuleNotFoundError: No module named 'sounddevice'` | Virtual environment not activated, or packages not installed         |
| MP3 preview/conversion fails                          | ffmpeg not installed or not found; verify with `ffmpeg -version`     |
| Folder selection dialog freezes inside preview window | Update to the latest script version (grab_release fix applied)       |
| `SyntaxError` after manually editing the script       | Re-copy the full script exactly; avoid partial copy-paste of f-strings|
| Samples not detected during sync                      | Confirm the IMPORT folder path is correct via "Ordner waehlen"       |

---

## 7. Quick Reference: Full Setup From Scratch

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip ffmpeg libportaudio2 python3-tk

cd ~
python3 -m venv p6env
source p6env/bin/activate
pip install sounddevice soundfile pydub

# Place Rolandp6Sampletool.py in ~/

python3 -u ./Rolandp6Sampletool.py
```
