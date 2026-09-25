# PyP6 - Roland P-6 Sample Manager

![Roland-P6-sample-manager](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6main.png)

**Version 4.2.3** - © 2026 Brian Siemund

## Overview

PyP6 is a free desktop application (Python + Tkinter) for managing WAV/MP3
samples across the 8 sample banks (A-H) and 6 pads per bank of the Roland
AIRA P-6. It handles auditioning before loading, MP3-to-WAV conversion,
per-pad sample rate / pitch / mono conversion, non-destructive editing
(trim, normalize, fade), transfers in both directions between the app and
the device, presets that carry their samples with them, multi-sample "Chop"
files built from several sources at once, **time-stretching that changes
length without moving pitch**, and a **wavetable synthesizer** that turns a
pad into 255 waveforms you step through with the START knob.

> **Windows users:** a prebuilt **Windows x64 executable** is available,
> built with PyInstaller and bundling **all dependencies, including
> ffmpeg/ffprobe**. No Python installation, pip packages, or separate
> ffmpeg setup are required - just download and run. See Section 2.1.

If it saves you time, there is a Ko-fi link under **Settings → Donate**:
[ko-fi.com/j0kerpack](https://ko-fi.com/j0kerpack). The app is free and
stays free.

---

## What's New in 4.x

### Length and tempo without pitch shift

The P-6 can only shorten a sample by playing it faster, which drags the
pitch up with it. PyP6 does it properly instead.

- **Type a length, or a tempo.** The pad editor takes a target in seconds,
  or in **BPM** once a tempo has been found in the sample. Enter 140 and a
  120 BPM loop comes out at 140, same key.
- **Three methods.** *Move pieces* cuts at the strikes and slides them
  together - attacks stay intact, tails get shorter; best for drum loops,
  and only for shortening. *Stretch spectrum* rebuilds the file at the new
  length, works in both directions and on sustained material, at the cost
  of softening attacks. *Auto* picks pieces when shortening a loop with
  clear strikes and the spectrum method otherwise.
- **Fit** shortens the sample to exactly what the pad allows at its current
  rate and channel setting, again without moving the pitch.
- **Keep length while pitching.** Pitching is vari-speed, so it normally
  changes the length too. Tick this and the length is held to whatever the
  Length/BPM field says.

### Beat detection and slicing

- **Detect Hits** finds the strikes in a rhythm loop and marks a cut before
  each one. A sensitivity slider re-runs the search live; **Del Line**
  merges two cuts that landed too close together.
- Every marked piece can go into the chop order at once, or **one at a
  time** - click a piece and use **Add Sel.**, or **drag it straight onto
  the list**. The marked region of an untouched waveform can be dragged
  over the same way.
- **Tempo detection** feeds the BPM field described above. It is deliberate
  about the octave question: a loop that could be read as 70 or 140 BPM is
  resolved towards the musically likely reading rather than the
  mathematically first one, and the alternative is offered in a tooltip.

### All-banks view

A second view shows **all 8 banks at once**, 6 pads per row, stripped down
to the waveform and the Load / Play / Eject / Chop / Synth buttons. A violet
stripe marks a pad that will export as mono.

**Drag mode** turns the pads grey and switches their buttons off, so a pad
can be dragged onto any other - across banks, not just within one. Dropping
a **bank letter** onto another row swaps two entire banks, Force Mono
included. Everything swaps rather than overwrites, and `Ctrl+Z` undoes it.

### Single-cycle import - Chain, Pairs, Multi

Browse to a folder of single-cycle files, pick the ones you want, and a
**Mode** dropdown decides what is made from them:

| Mode | Result |
|---|---|
| **Chain** | 1→2, 2→3, 3→4 - one unbroken sweep through every shape. Middle shapes are used twice, once to end a family and once to start the next. |
| **Pairs** | 1→2, 3→4, 5→6 - each shape used exactly once. Half as many families, each morph standing on its own. |
| **Multi** | Everything into **one** family, up to 255 shapes. No morphing at all: each step *is* a whole waveform, so START steps between them instead of sweeping. |

Files are read in natural order, so `MG2` comes before `MG10`.

**Whole wavetables are recognised and split.** A file that holds 33
waveforms in a row - the format most wavetables are shared in, including
32-bit float files that Python's own `wave` module refuses - is detected,
cut into its frames, and listed one row per waveform (`NAME [05/33]`). Each
frame can be reordered, removed or auditioned on its own; clicking the file
itself plays all of its waveforms in turn.

### Built-in collections

178 waveform families in five folders:

- **Basic** - 19 plain oscillator shapes: saw, saw + sub, vintage saw,
  vintage square, pulse/PWM, triangle, sine, wavefolder, hard sync, FM,
  phase distortion, staircase, vowel formant, organ, piano, strings, brass,
  bell/metal, noise morph. Simple mode bakes the classic sixteen of these;
  the three vintage variations are there for Advanced to pick.
- **Vintage Collection 1 and 2** - fat analogue shapes in the Moog-style
  ladder tradition.
- **Hot Microwaves Collection 1 and 2** - 63 morphing families each,
  digital wavetables after the Waldorf Microwave. Both are **continuous
  sweeps**: neighbouring families join without a seam, so several in a row
  run through as one movement.
- **Hot MW 1 Big 255WF** - 255 Microwave waveforms in a single multi family
  that fills the whole table on its own. It sits at the head of Collection
  1, which therefore lists 64 entries.

Simple mode now shows a description panel for whichever set is selected,
with its family count and what it sounds like.

### Sharing your own waveforms

Waveform folders you create are written to `~/.pyp6/user_wave_families/` as
`.p6wf` packs, one per folder - kept up to date by the app itself, so there
is nothing to export. Drop a pack someone sent you into that folder and it
is simply there the next time the Synth dialog opens, with a note saying how
many shapes arrived and which folder they went into. No restart needed.

Only folders you do not already have are read, so a pack sits harmlessly
beside a folder of the same name you have edited since. Multi families
travel in packs too, and everything travels inside presets as well.

### Everywhere else

- **Undo/redo is 25 steps deep** (was 5), across all banks.
- **Eleven themes** - `dark`, `tokyo`, `dracula`, `modern`, `matrix`,
  `ice`, `devil`, `synthwave`, `latte`, `snow`, `bright`. All checked for
  readable contrast; text in `matrix` and `devil` is neutral grey rather
  than tinted, and `snow` is a neutral light grey for working in daylight.
- **The pad tells you what it holds.** A wavetable pad's header reads
  `WAVETABLE · Vintage Collection 1`, or `· Custom Selection` if the
  families were picked by hand.
- **PRM sidecars follow the pad.** A sample imported from the device keeps
  its `.PRM` - filter, envelopes, level, pan and loop - and the PHRASE
  number is rewritten to wherever the pad ends up, however it got there:
  swapped, copied to another bank, or loaded from a preset into a different
  slot.
- **Status messages take the line.** A message like "Swapped A1 and C3"
  temporarily covers the file name on the right and is shortened to fit
  rather than clipped, at any window size.
- **The file browser knows your drives.** Windows drive letters (read from
  the drive bitmask, so an empty card reader is not spun up), macOS
  `/Volumes`, Linux `/media` and `/mnt`, each with a drive icon rather than
  a folder one.

---

## 1. System Requirements

- Windows, Linux (tested on Ubuntu/Debian-based distributions), or macOS
- Python 3.10 or newer (3.13+ requires one extra package, see 2.2.2) -
  **not needed if you use the prebuilt Windows executable**
- A working audio output device
- ffmpeg (required for MP3 support, pitch/rate/mono conversion, time
  stretching and the Chop feature; WAV-only workflows can run without it,
  with reduced functionality)
  - **already bundled** in the prebuilt Windows executable
- `tkinterdnd2` (optional) - only needed for dropping files from the file
  manager onto a pad. Everything else works without it. Also bundled in the
  prebuilt Windows executable.

### 1.1 Tested configuration

4.2.3 was developed and tested against:

```
Python:      3.14.4 on linux
Tcl/Tk:      8.6.17
NumPy:       2.5.1
soundfile:   0.14.0
sounddevice: 0.5.5
pydub:       0.25.1
```

### 1.2 A note on Tcl/Tk 9.0

Python 3.13 and newer can be built against **Tcl/Tk 9.0**, and the
python.org installers are moving that way. PyP6 runs on both — there is
no separate build, and the app calls no raw Tcl — but **several users
have reported that things behave differently under Tk 9.0**, and a few
features are known not to work correctly there:

- **Drag & drop does not work under Tcl/Tk 9.** `tkinterdnd2` ships a
  native Tcl extension built against Tcl 8, and `package require tkdnd`
  fails outright under Tcl 9. This is not a bug PyP6 can work around —
  the extension itself has no Tcl-9 build yet. PyP6 detects the failure
  and reports the actual reason in Settings → About instead of claiming
  the package is missing, but **drag & drop stays disabled until
  `tkinterdnd2`/`tkdnd` ships a Tcl-9-compatible build.** If you rely on
  drag & drop, install PyP6 on a Python built against Tcl/Tk 8.6.
- **X11 mouse buttons 4-7 are no longer script-visible** (TIP 474). On
  Linux under Tk 9, raw `<Button-4>`/`<Button-5>` scroll events may not
  fire the way older Tk versions did. PyP6 binds `<MouseWheel>` alongside
  `<Button-4>`/`<Button-5>` everywhere and reads only the sign of
  `event.delta` as a workaround, but users on Tk 9 builds have still
  reported inconsistent scroll behavior in some list views — if
  scrolling feels wrong, this is the likely cause.
- **Synth window widgets can render incorrectly under Tk 9.** Some users
  have reported layout and rendering glitches with widgets in the Synth
  window specifically — this is separate from the older macOS 12 /
  Tcl 8.6-pre-8.6.15 unpainted-family-list issue described below. Root
  cause is still being narrowed down; **a fix is in progress.** Until
  then, if the Synth window looks broken, check the startup console note
  for your Tcl/Tk version — switching to a Tcl/Tk 8.6 build is the
  current workaround. I will soon work on a solution.

The app reads `info patchlevel` at startup and prints a short note on the
console when it finds 9.0 or newer, so you can quickly check which Tcl/Tk
version you're actually running if something looks off.

**Recommendation:** if you hit missing drag & drop, odd scroll behavior,
Synth window rendering glitches, or other UI issues, check the startup
console note first. Installing PyP6 with a python.org interpreter built
against **Tcl/Tk 8.6** avoids all of the above and is currently the most
reliable configuration overall.

**For building a macOS executable, a python.org interpreter with Tcl/Tk
8.6 is currently the most reliable base.** On macOS 12 with Tcl/Tk older
than 8.6.15 the Synth dialog can come up with its family lists unpainted;
8.6.15 and later fix it.

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

### 2.2 Option B: Run from Source

#### 2.2.1 Install Python

Download and install Python from [python.org](https://www.python.org/downloads/).
Run commands in the **Command Prompt / PowerShell**, not inside the
interactive Python console (the `>>>` prompt) - `pip` only works in a
regular terminal.

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

> `matplotlib` is not required - waveforms are drawn directly on the Tkinter
> canvas. The wavetable synthesizer needs nothing beyond `numpy`.

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
4. Verify with `ffmpeg -version`.

Alternatively, Settings lets you point at `ffmpeg.exe`/`ffprobe.exe`
manually. Settings → About shows which binary is actually in use.

#### 2.2.4 Run the application

```
python PyP6-Roland-P6-Sample-Manager_4_2_3.py
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

**Python 3.13 and newer:**

```bash
pip install audioop-lts
```

```bash
# optional - drag & drop from the file manager (see the caveat in 5.5)
pip install tkinterdnd2
```

Run the app (the venv must be reactivated in every new terminal session):

```bash
cd ~
source p6env/bin/activate
python3 -u ./PyP6-Roland-P6-Sample-Manager_4_2_3.py
```

> If a feature appears to be missing, check **Settings → About** first - it
> reports which optional components actually loaded. A package installed
> outside the active venv is the most common cause.

---

## 4. File Placement & Configuration

```
~/.pyp6/config.json            settings
~/.pyp6/temp/                  trimmed / normalized / faded / chopped / stretched samples
~/.pyp6/wavetables/            the WAV and PRM files of your wavetable pads
~/.pyp6/waveforms.json         waveforms you drew or loaded yourself
~/.pyp6/user_wave_families/    .p6wf packs, one per waveform folder
```

(on Windows: `C:\Users\<you>\.pyp6\`)

Saved settings include the last used IMPORT folder, theme, tooltip
visibility, default autoplay and slice count, storage warning threshold,
recent presets, and any manual ffmpeg/ffprobe path overrides. Delete
`config.json` to reset all preferences.

The **temp folder** holds every edited sample not saved into a preset.
Settings → Temporary Files shows its size and can clear it - pads still
pointing at a deleted file are cleared with it, so save a preset first if
you want to keep those edits. Wavetables and your own waveforms live
outside `temp/` and are never touched.

---

## 5. Using the Application

### 5.1 Selecting the IMPORT folder

On first launch the tool auto-detects a mounted P-6 IMPORT folder in the
background, so the window opens immediately. Use **Settings → IMPORT Folder
→ Change...** to point it elsewhere; this is remembered across restarts.
The device appears as a drive called P-6 containing an `IMPORT` folder, with
`BANK_A` to `BANK_H` inside it.

Detection is case-insensitive, so a FAT volume that presents the folder as
`import` is found too. Mount points that refuse to be listed - `/media/root`
and similar - are skipped silently; that is normal.

### 5.2 Loading a sample onto a pad

![Sample import](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6Load.png)

Click "Load" on any pad for a file browser with folder navigation, sortable
columns (Name / Length / Size), waveform preview and audition playback.
Drag the green and red bracket markers to load only the marked region,
optionally tick **Normalize**, then confirm with "Select" or a double-click.
Trimmed and normalized results go to the temp folder - your original file is
never modified.

### 5.3 Per-pad sound settings

- **Sample Rate** - 44100, 22050, 14700 or 11025 Hz. Lossless to change: the
  file is not rewritten, the rate is applied on export, so you can drop to
  22050 and back.
- **Pitch** - ±1200 cents in 100-cent steps, via +/- buttons, direct entry,
  or "Reset".
- **Mono** - forces this pad's sample to mono. The bank-wide "Force Mono
  (this bank)" switch overrides it and greys it out while active.

If the result would exceed the P-6's maximum recording time for the chosen
rate/channel combination, a warning appears in the central warnings area and
the excess is shaded orange in the pad's mini waveform.

### 5.4 Editing a loaded sample

Click a pad's **mini waveform** to open the editor:

- Drag the green/red markers to shorten the sample, mouse wheel to zoom
- **Normalize** and **Fade In / Fade Out** (0 to 1.0 s in logarithmic
  steps), previewed live
- **Length / BPM** - type a target length in seconds, or a tempo in BPM once
  one has been detected, and pick a method (see 5.14). The pitch does not
  move either way.
- **Fit** shortens the sample to exactly what the pad allows
- **Keep length** holds the length while you change the pitch
- The header shows the file's own rate and the pad's export settings, and
  the orange area marks what the P-6 would cut off
- **Apply to Pad** writes the result to a new temp file and puts it back on
  the pad, keeping rate, pitch and mono. The original is untouched and
  `Ctrl+Z` undoes it.

Chop multisamples are a special case: trim and fade are disabled, since
either would shift the fixed slice boundaries. Normalize stays available.

### 5.5 Rearranging pads and dropping files

Drag a pad's **sample name** (or the pad frame) onto another pad to swap the
two including their settings. The target is outlined in orange while you
drag. Swaps are undoable.

In the **all-banks view** (5.6) the same gesture works across banks, and a
bank letter dropped on another row swaps two whole banks.

You can also **drop audio files from your file manager** onto a pad; the pad
under the cursor is outlined in green. Dropping several at once fills the
following pads in order. Requires `tkinterdnd2`.

> **Platform note:** drag & drop is reliable on **Windows**. On Linux it
> works, but under **Wayland** an occasional drop is missed - drag it again,
> or use "Load". Under Tcl/Tk 9.0 it switches off entirely; see 1.2.
> **Settings → About** distinguishes the cases.

### 5.6 The all-banks view

![All banks view](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6MultiBank.png)

The view dropdown in the top bar switches between **Single bank** and **All
banks**. The all-banks grid shows every bank as a row of 6 compact pads -
waveform, name, and the five buttons - with a violet stripe on any pad that
will export as mono. Clicking a pad's waveform opens it in the big waveform
area below and switches the app to that bank.

**Drag mode** (the button beside the path) greys the pads out and switches
their buttons off so nothing fires by accident, then lets you drag pads onto
pads, or bank letters onto rows. Everything swaps; nothing is overwritten.

### 5.7 Playback and removal

"▶" plays the pad's sample exactly as it will sound after export (rate,
pitch and mono applied) and mirrors it in the large waveform at the bottom,
with a live playhead; the playing pad is outlined in blue and its button
becomes a Stop square. "⏏" clears the pad, optionally deleting the matching
file from the device.

### 5.8 Transferring to and from the device

- **Banks → P6** opens a dialog where you tick the banks to export, with a
  live total-size readout. Each pad is converted to its rate/pitch/mono and
  written to `IMPORT/BANK_x/PAD_n/`. A `.PRM` sidecar travels with it, its
  PHRASE number rewritten for the slot the pad is actually in.
- **P6 → Bank** walks you through the device's own export procedure and
  reads the resulting EXPORT folder into the **active bank**. The device
  does not store rate/pitch/mono, so those arrive at their defaults.
- The P-6 must be in storage mode before an upload (hold Record and switch
  power on). After uploading, press a key on the device and wait for
  "done".

### 5.9 Clearing

- **Clear Bank** empties the pads of the chosen banks **in the app only** -
  nothing on disk or on the device is touched. Undoable.
- **Wipe P6 IMPORT Folder** permanently deletes every sample in the device's
  IMPORT folder across all banks, after a confirmation listing what will go.
  Your pads stay as they are. This cannot be undone.

### 5.10 Presets

- **Save Preset...** - pick a folder and a name, tick which banks to
  include. The samples are copied into the preset folder, along with their
  `.PRM` sidecars, so a preset stays usable after the temp folder is
  cleared. Saving over an existing preset replaces only the banks you
  checked.
- **Load Preset...** - click a preset folder to see which banks it contains,
  then tick the ones to load. With exactly one bank selected you can load it
  into the *current* bank instead of its original slot; the PHRASE numbers
  are rewritten to match on export. Loading is undoable.
- **Recent** - the last five presets, one click away.

Saving and loading both verify that every sample listed is present and
readable, and say plainly what is missing.

### 5.11 Chop - building a multi-sample from several files

![Sample chop slice tool](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6Chop.png)

Click "Chop" on any pad to combine several short samples into one WAV ready
for the P-6's built-in **Chop** function in Sample Edit (Voice) mode.

**Credit:** inspired by and conceptually based on
[p6-wave-slice](https://github.com/warreneblackwell/p6-wave-slice) by
**Warren Blackwell**.

**Workflow**

1. Browse to a folder of WAV/MP3 files and move the ones you want to the
   right - with the arrow, or by dragging them across.
2. **Drag across the waveform** to mark a region, then drag the selection
   onto the list to add just that part. You can pull several separate
   regions out of one long recording this way.
3. Or use **Detect Hits** on a rhythm loop: it marks a cut before each
   strike, the sensitivity slider re-runs the search live, and **Del Line**
   merges two cuts that landed too close. **Add Slices →** takes the lot;
   clicking one piece and then **Add Sel.** - or dragging it onto the list -
   takes just that one.
4. Reorder the selection with ↑/↓ (or `Alt+Up` / `Alt+Down`) - that order is
   the slice order on the device. `Del` removes an entry.
5. Choose the number of **Slices** (1, 2, 4, 8, 16, 24, 32, 48 or 64), the
   target **Sample Rate**, and **Stereo**/Mono. Stereo locks once the list
   isn't empty so mono and stereo entries cannot mix; clear the list to
   change it.
6. Choose a **Normalize** mode: `Off`, `Per sample` (each slice lifted
   individually, for sources recorded at different volumes) or `Whole file`
   (only the finished multisample is lifted, keeping the balance between
   slices).
7. **Build Multisample** resamples and converts each file, trims leading
   silence, pads or truncates each slice to a uniform length, and
   concatenates them in order. Fewer files than slices leaves the remainder
   silent, so the output always matches the slice count.

Anything that would be cut for exceeding the per-slice time is shaded orange
while you work.

**Slice duration reference**

| Sample Rate | Channels | Max Duration | 32 Slices | 64 Slices |
|-------------|----------|--------------|-----------|-----------|
| 44.1 kHz    | Mono     | 5.9s         | 184ms     | 92ms      |
| 22.05 kHz   | Mono     | 11.8s        | 369ms     | 184ms     |
| 14.7 kHz    | Mono     | 17.8s        | 556ms     | 278ms     |
| 11.025 kHz  | Mono     | 23.7s        | 741ms     | 370ms     |
| 44.1 kHz    | Stereo   | 2.95s        | 92ms      | 46ms      |
| 22.05 kHz   | Stereo   | 5.9s         | 184ms     | 92ms      |

### 5.12 Wavetable synthesizer

![Wavetable synthesizer](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/Pyp6Synth.png)

The P-6 has no oscillators, but its START knob steps through a sample in 256
positions. Build the sample so every position lands exactly on one waveform
cycle and the knob becomes a wavetable position control. That is what
**Synth** on each pad does: one WAV holding 255 single-cycle waveforms, plus
a `.PRM` telling the P-6 how to loop them.

**On the pad**

1. Choose a **register** - Bass (around C2), Mid or Lead - and the **root
   note** within ±6 semitones.
2. **Plays up to** tells the app how far above that note you intend to
   play, up to three octaves. The waveforms are band-limited to stay clean
   over that range.
   The info line spells out what that leaves you: the harmonics the table
   will hold, the highest note the P-6 will play it at - three octaves
   above the root, a limit of the device that no setting here moves - and,
   while it is lower, the note above which the table stops being clean.
3. **Simple** mode builds from one ready-made set: a collection, a random
   mix, or a multi family that fills the table on its own. The panel beside
   the list describes whichever set is selected. **Advanced** mode lets you
   pick families one by one, up to 16, and put them in order.
4. Click a family to hear a sweep through it and watch its waveforms in the
   Morph display.
5. Choose an **init patch** (Init, Acid, Bass, Pad, Reso...) and **Build
   Wavetable**.

**On the P-6**

Transfer the bank, then set **SIZE to 1** and turn **START**. Positions 0 to
254 each select one waveform. Position 255 sits past the last one and does
not produce a usable sound - that is a property of the P-6, not a fault in
the table.

You can play the pad in the app too. It runs the whole table front to back,
slowed down so each waveform is audible, with the layout shown at the
bottom of the window. On a wavetable pad, clicking the big waveform jumps to
the start of the zone you clicked.

> Editing a wavetable pad's audio would throw START out of step with the
> waveforms, so Load and Chop are greyed out on it. **Synth** stays
> available and reopens with your settings. Eject first if you want a normal
> sample there.

### 5.13 Your own waveforms

![Wavetable synthesizer](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6Draw.png)
The ✎ button between the two lists opens the **Waveform Creator**.

- **Draw** two shapes, A and B, and the family morphs from one to the other.
  Start from a sine, triangle, saw or square and draw over it; **Smooth**
  takes the shakiness out of a freehand line.
- **Load...** reads a single-cycle WAV instead. Its rate, bit depth and
  length do not matter, only its shape; the pitch comes from the root note.
  Single-cycle files are auditioned as a held note.
- The **orange line** shows what the P-6 will hold, over your blue drawing.
  They differ wherever your line is sharper than a segment can carry.
  
![Wavetable synthesizer](https://github.com/j0kerpack/Roland-P6-sample-manager/blob/main/PyP6PyP6ImportSingleCycle.png)
**Import single cycles** with the folder button. The browser on the left
lists the files; what you send across builds up in the **Cycle Order** list
on the right, and the import button counts it (**Import 12**). Files holding
a complete wavetable are split into their frames automatically, and a single
frame can be taken across on its own.

- Reorder the list with ↑/↓ - that order is the order the shapes are
  combined in, so it decides how the sweep runs.
- Click an entry to hear it at the root note, or remove it again.
- The **Mode** dropdown in the same row decides what is built from the
  order: Chain, Pairs or Multi, as described under "What's New".

It speaks up before building, rather than leaving you to notice afterwards:
when a file turned out to be a whole wavetable and was cut into frames; when
**Pairs** has an odd number left over (the last shape is kept as a static
one, not dropped); and when **Multi** holds more than 255 shapes (the rest
are left out) or fewer (the remaining steps stay silent, and the family
wants the step order to itself). Past 64 selected files it asks once whether
you really meant the whole folder.

Your waveforms live in `~/.pyp6/waveforms.json` and are available in every
wavetable afterwards. Each folder is also written as a `.p6wf` pack under
`~/.pyp6/user_wave_families/`, which is how you hand one to someone else -
they drop it in the same folder and it is there the next time they open the
Synth dialog. They travel inside presets too.

> A normal sample is not a single-cycle file. Load a drum loop here and the
> whole recording is squeezed into one waveform. The app warns you first.

### 5.14 Changing length without changing pitch

In the pad editor, set the unit to **Length** (seconds) or **BPM**, type a
target, press Enter. BPM only appears in the dropdown once a tempo has been
found in the sample; if the reading looks an octave off, the tooltip offers
the alternative.

| Method | What it does | Best for |
|---|---|---|
| **Move pieces** | Cuts at the strikes and slides them together. Attacks intact, tails shortened. | Drum loops. Shortening only. |
| **Stretch spectrum** | Rebuilds the file at the new length. | Sustained material, and any lengthening. Softens attacks. |
| **Auto** | Pieces when shortening a loop with clear strikes, spectrum otherwise. | Leaving it alone. |

Tempo detection deliberately refuses very sparse loops rather than
guessing - a file with fewer than four clear onsets gets no BPM reading, and
the Length unit stays available.

### 5.15 Settings

- **IMPORT Folder** - where "Banks → P6" writes
- **Appearance** - one of eleven themes (restart required) and a tooltip
  switch (applies immediately)
- **Audio Components** - the state of pydub and ffmpeg, plus manual
  ffmpeg/ffprobe path overrides
- **Defaults** - how Autoplay starts out in the preview windows, default
  Chop slice count, storage warning threshold in MB, and UI scale
- **Temporary Files** - current size and "Clear Now"
- **About** - version, author, and the exact state of every optional
  component, with "Copy Info" for bug reports
- **Donate** - the Ko-fi link, in a window you can copy it from

---

## 6. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `SyntaxError: invalid syntax` on `pip install ...` | You typed it inside the Python console (`>>>`). Run `exit()` first, then use a regular terminal. |
| `'pip' is not recognized` | Use `python -m pip install ...`. |
| `ModuleNotFoundError: No module named 'audioop'` | Python 3.13+: `pip install audioop-lts`. Not applicable to the prebuilt executable. |
| A feature is silently missing | **Settings → About** reports what actually loaded, which separates "package missing" from "package present but not working". Installing into the wrong venv is the usual cause. |
| MP3 preview/conversion fails | ffmpeg not installed or not on PATH; verify with `ffmpeg -version`. |
| ffmpeg not found when started from Finder (macOS) | Finder does not pass your shell PATH. PyP6 searches the usual install locations itself; if it still fails, set the path in Settings. |
| `[swallowed] _child_dir_ci: PermissionError` in the debug log | Normal. `/media/root` and similar are not readable by you; detection skips them and carries on. Only shown with `PYP6_DEBUG=1`. |
| Dropping files onto pads never works | **Settings → About**: `tkinterdnd2 not available` → `pip install tkinterdnd2` into the venv you start the app from. Under Tcl/Tk 9.0 it cannot work at all; see 1.2. |
| A drop is occasionally missed on Linux | Known under Wayland. Drag again, or use "Load". |
| Startup problems you want to diagnose | Launch with `PYP6_DEBUG=1` for a timed startup log naming every phase and which optional components loaded. |
| The Synth dialog opens with empty lists (macOS) | Tcl/Tk older than 8.6.15. Update Tk, or use a python.org interpreter. |
| The wavetable sounds wrong on the device | SIZE must be 1. At any other SIZE the loop covers more or less than one waveform. |
| START at maximum sounds wrong | Position 255 is past the last waveform. Use 0 to 254. |
| A family barely changes across its steps | Give it more room by putting fewer families in the step order. |
| A multi family lost most of its waveforms | It was sharing the step order. A multi family needs all 255 steps; the app warns when you add a second family beside one. |
| Load and Chop are greyed out on a pad | It holds a wavetable. Eject to use the pad normally; Synth stays available. |
| A single-cycle file sounds like a mangled version of the whole file | It is not a single cycle - it holds a complete wavetable. The folder import splits those automatically; the file list shows `Multi wavetable · 33 × 2048` when it recognises one. |
| A loaded single-cycle file sounds dull | Short files carry less detail. That is the file, not the app. |
| Stretching lengthens instead of shortens, or vice versa | In BPM the arrows are inverted relative to Length, because a higher tempo is a shorter file. The tooltip on the field says which way round it is. |
| "Move pieces" refuses | It only shortens, and only where clear strikes were found. Use Stretch spectrum, or Auto. |
| Samples not detected on the device | Confirm the IMPORT folder path via Settings. |
| A pad went empty on its own | Its sample was an edited file in the temp folder and the temp folder was cleared. Save edits into a preset. |
| Chop output sounds heavily cut off | Slice count too high for the sample length/rate. Anything shaded orange is what gets cut. |
| Samples not transferring to the P-6 | Only a limited amount can go at once (warning threshold, default 10 MB). Export in smaller groups. |
| Windows flags the executable as unsafe | Expected for an unsigned third-party `.exe`; choose "Run anyway" if you trust the source. |

---

## 7. Building Your Own Standalone Windows Executable (Optional)

```
pip install pyinstaller

python -m PyInstaller PyP6-Roland-P6-Sample-Manager_4_2_3.py -y -w --onefile ^
  --icon=icon.ico ^
  --collect-data tkinterdnd2 ^
  --add-binary "C:\ffmpeg\bin\ffmpeg.exe;." ^
  --add-binary "C:\ffmpeg\bin\ffprobe.exe;." ^
  --clean
```

Notes:

- The logo is built into the script. Drop a `pyp6logo.png` next to it to use
  your own instead.
- Use `;` as the `--add-binary` separator on Windows, not `:`.
- `--collect-data tkinterdnd2` is required for drag & drop in the build: the
  package ships native Tcl extension files, not just `.py` modules. Leave it
  out and the `.exe` still builds - it just won't accept dropped files.
- `--clean` clears PyInstaller's cache; use it whenever you changed the
  source.
- The app resolves bundled files via `sys._MEIPASS`, so bundled
  ffmpeg/ffprobe are found without a PATH entry on the end user's machine.
- Some antivirus software flags a bundled `ffmpeg.exe` extracted at runtime.
  If ffmpeg-dependent features stop working on one machine, check the
  Windows Defender protection history.

**macOS:** build against a python.org interpreter with **Tcl/Tk 8.6** - see
1.2.

---

## 8. Credits

- Chop/multi-sample concept inspired by
  [p6-wave-slice](https://github.com/warreneblackwell/p6-wave-slice) by
  **Warren Blackwell**, a command-line utility that batch-processes WAV
  samples into P-6 Chop-ready files.

---

## 9. Support

PyP6 is free and stays free. If it saved you some time:
[ko-fi.com/j0kerpack](https://ko-fi.com/j0kerpack) - also reachable from
**Settings → Donate**.

---

Roland, AIRA and P-6 are trademarks of Roland Corporation. Waldorf and
Microwave are trademarks of Waldorf Music GmbH; Moog is a trademark of Moog
Music Inc. Named only to describe how the built-in waveforms sound. This is
an independent project, not affiliated with, endorsed by or supported by any
of them.
