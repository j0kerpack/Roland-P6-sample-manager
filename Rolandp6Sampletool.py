import os
import shutil
import wave
import contextlib
import json
import tkinter as tk
from tkinter import filedialog, messagebox
import sounddevice as sd
import soundfile as sf

try:
    from pydub import AudioSegment
    AudioSegment.converter = "/usr/bin/ffmpeg"
    AudioSegment.ffmpeg = "/usr/bin/ffmpeg"
    AudioSegment.ffprobe = "/usr/bin/ffprobe"
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

CONFIG_FILE = os.path.expanduser("~/.p6tool_config.json")
DEFAULT_IMPORT_ROOT = "/run/media/bds/P-6/IMPORT"
LAST_SAMPLE_DIR = None

BANKS = [chr(c) for c in range(ord("A"), ord("H") + 1)]
PADS = list(range(1, 7))
TARGET_RATES = [44100, 22050, 14700, 11025]

MAX_SECONDS = {
    (44100, 1): 5.9, (44100, 2): 2.95,
    (22050, 1): 11.8, (22050, 2): 5.9,
    (14700, 1): 17.8, (14700, 2): 8.9,
    (11025, 1): 23.7, (11025, 2): 11.85,
}


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
    return DEFAULT_IMPORT_ROOT


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


class AudioPreviewDialog(tk.Toplevel):
    def __init__(self, parent, initial_dir=None):
        super().__init__(parent)
        self.title("Select Sample (with Preview)")
        self.geometry("520x420")
        self.selected_path = None
        self.current_dir = initial_dir or os.path.expanduser("~")
        self.autoplay_var = tk.BooleanVar(value=False)

        top = tk.Frame(self, padx=8, pady=8)
        top.pack(fill="x")
        self.path_label = tk.Label(top, text=self.current_dir, anchor="w", fg="gray")
        self.path_label.pack(side="left", fill="x", expand=True)
        tk.Button(top, text="Change Folder", command=self.choose_folder).pack(side="right")

        list_frame = tk.Frame(self, padx=8, pady=4)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<Double-Button-1>", self.on_confirm)

        autoplay_row = tk.Frame(self, padx=8)
        autoplay_row.pack(fill="x")
        tk.Checkbutton(autoplay_row, text="Autoplay (play sound on click)",
                       variable=self.autoplay_var).pack(side="left")

        btn_row = tk.Frame(self, padx=8, pady=8)
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="Preview", command=self.preview_selected,
                  bg="#03A9F4", fg="white").pack(side="left", padx=4)
        tk.Button(btn_row, text="Stop", command=self.stop_preview).pack(side="left", padx=4)
        tk.Button(btn_row, text="Cancel", command=self.on_cancel).pack(side="right", padx=4)
        tk.Button(btn_row, text="Select", command=self.on_confirm,
                  bg="#4CAF50", fg="white").pack(side="right", padx=4)

        self.refresh_list()
        self.transient(parent)
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
            if self.autoplay_var.get():
                self.preview_selected()
        else:
            self.selected_path = None

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
        new_dir = filedialog.askdirectory(initialdir=self.current_dir, parent=self)
        self.grab_set()
        self.lift()
        self.focus_force()
        if new_dir:
            self.current_dir = new_dir
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


class SampleSlot:
    def __init__(self, parent, pad_num, app):
        self.pad_num = pad_num
        self.app = app
        self.filepath = None
        self.target_rate = tk.IntVar(value=44100)

        self.frame = tk.LabelFrame(parent, text=f"PAD_{pad_num}", padx=8, pady=8)
        self.frame.grid(row=(pad_num - 1) // 3, column=(pad_num - 1) % 3,
                         padx=6, pady=6, sticky="nsew")

        self.label = tk.Label(self.frame, text="No sample loaded", width=30,
                               anchor="w", fg="gray")
        self.label.pack(fill="x")

        rate_row = tk.Frame(self.frame)
        rate_row.pack(fill="x", pady=2)
        tk.Label(rate_row, text="Sample Rate:").pack(side="left")
        tk.OptionMenu(rate_row, self.target_rate, *TARGET_RATES,
                      command=lambda _: self.update_warning()).pack(side="left", padx=4)

        self.warn_label = tk.Label(self.frame, text="", fg="red", wraplength=220,
                                    justify="left", anchor="w")
        self.warn_label.pack(fill="x")

        btn_row = tk.Frame(self.frame)
        btn_row.pack(fill="x", pady=4)
        tk.Button(btn_row, text="Load...", command=self.load_sample).pack(side="left", padx=2)
        self.play_btn = tk.Button(btn_row, text="Play", command=self.play_sample, state="disabled")
        self.play_btn.pack(side="left", padx=2)
        self.remove_btn = tk.Button(btn_row, text="Remove", command=self.remove_sample, state="disabled")
        self.remove_btn.pack(side="left", padx=2)

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
        self.label.config(text=label_text, fg="black")
        self.play_btn.config(state="normal")
        self.remove_btn.config(state="normal")
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
        self.label.config(text="No sample loaded", fg="gray")
        self.warn_label.config(text="")
        self.play_btn.config(state="disabled")
        self.remove_btn.config(state="disabled")


class P6ManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Roland AIRA P-6 Sample Manager v1.0.0")
        self.import_root = load_last_import_root()
        self.current_bank = tk.StringVar(value=BANKS[0])
        self.slots = {b: {} for b in BANKS}

        top = tk.Frame(root, padx=10, pady=10)
        top.pack(fill="x")
        tk.Label(top, text="Bank:", font=("Arial", 11, "bold")).pack(side="left")
        tk.OptionMenu(top, self.current_bank, *BANKS, command=self.switch_bank).pack(side="left", padx=6)

        self.path_label = tk.Label(top, text=f"IMPORT Path: {self.import_root}", fg="gray")
        self.path_label.pack(side="left", padx=20)

        tk.Button(top, text="Choose Folder", command=self.choose_import_folder,
                  bg="#9C27B0", fg="white").pack(side="right", padx=4)
        tk.Button(top, text="Sync with Device", command=self.sync_from_device,
                  bg="#FF9800", fg="white").pack(side="right", padx=4)
        tk.Button(top, text="Delete Bank", command=self.delete_current_bank,
                  bg="#F44336", fg="white").pack(side="right", padx=4)

        self.pad_container = tk.Frame(root, padx=10, pady=10)
        self.pad_container.pack(fill="both", expand=True)

        bottom = tk.Frame(root, padx=10, pady=10)
        bottom.pack(fill="x")
        tk.Button(bottom, text="Copy Current Bank", command=self.export_current_bank,
                  bg="#4CAF50", fg="white").pack(side="left", padx=4)
        tk.Button(bottom, text="Copy ALL Banks", command=self.export_all_banks,
                  bg="#2196F3", fg="white").pack(side="left", padx=4)

        self.build_pad_slots(self.current_bank.get())
        self.sync_from_device(initial=True)

    def choose_import_folder(self):
        new_path = filedialog.askdirectory(
            title="Select P-6 IMPORT Folder",
            initialdir=self.import_root if os.path.isdir(self.import_root) else os.path.expanduser("~")
        )
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
