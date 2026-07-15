"""
app.py
================================================================================
Tkinter GUI for the CSV -> MF4 converter.

Tkinter ships with Python itself (no pip install), which is why it's the
default choice for small tool GUIs like this one.

How a tkinter app is structured:
  1. Build the window and its widgets (buttons, listbox, log area).
  2. Wire each button to a callback function.
  3. Call mainloop() - tkinter then sits in an event loop, waiting for
     clicks and redrawing the window. Your code only runs inside callbacks.

The one tricky part: if a callback does slow work (like converting a big
CSV), the event loop is blocked and the window freezes ("Not Responding").
The fix is the standard worker-thread pattern used below:
  - the Convert button starts a background thread that does the work,
  - the thread never touches the GUI directly (tkinter is not thread-safe),
  - instead it pushes log lines onto a Queue,
  - and the GUI polls that queue every 100 ms with root.after() and
    appends whatever arrived to the log window.
================================================================================
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from converter import convert


def set_window_icon(root, icon_name):
    """Give the window (and its taskbar button) the app's own icon.

    PyInstaller unpacks files bundled with --add-data into a temp folder
    exposed as sys._MEIPASS; running from source, the .ico sits in assets/
    next to this file. The icon is cosmetic, so never let it stop the app.
    """
    base = getattr(sys, "_MEIPASS", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "assets"))
    try:
        root.iconbitmap(os.path.join(base, icon_name))
    except Exception:
        pass


class ConverterApp:
    def __init__(self, root):
        self.root = root
        root.title("CSV → MF4 Converter  (AVL Drive)")
        root.geometry("720x480")
        root.minsize(560, 380)
        set_window_icon(root, "csvtomf4.ico")

        self.files = []                 # CSV paths queued for conversion
        self.log_queue = queue.Queue()  # worker thread -> GUI messages
        self.working = False

        # --- top row: file selection buttons --------------------------------
        top = tk.Frame(root)
        top.pack(fill="x", padx=10, pady=(10, 5))

        tk.Button(top, text="Add CSV file(s)…",
                  command=self.pick_files).pack(side="left")
        tk.Button(top, text="Add folder…",
                  command=self.pick_folder).pack(side="left", padx=(6, 0))
        tk.Button(top, text="Clear list",
                  command=self.clear_files).pack(side="left", padx=(6, 0))

        # --- middle: list of queued files ------------------------------------
        mid = tk.LabelFrame(root, text="Files to convert (.mf4 is written next to each CSV)")
        mid.pack(fill="both", expand=False, padx=10, pady=5)

        self.file_list = tk.Listbox(mid, height=6)
        self.file_list.pack(fill="both", expand=True, padx=5, pady=5)

        # --- convert button ---------------------------------------------------
        self.convert_btn = tk.Button(root, text="Convert", height=2,
                                     command=self.start_conversion)
        self.convert_btn.pack(fill="x", padx=10, pady=5)

        # --- bottom: log window ----------------------------------------------
        bottom = tk.LabelFrame(root, text="Log")
        bottom.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.log_box = scrolledtext.ScrolledText(bottom, state="disabled",
                                                 font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=5, pady=5)

        # Files dragged onto the .exe arrive as command-line arguments -
        # pre-load them so drag-and-drop onto the exe still works.
        for arg in sys.argv[1:]:
            self.add_path(arg)

        # Start polling the queue. after(ms, fn) schedules fn on the GUI
        # thread - this is the only safe way to update widgets from work
        # done in another thread.
        self.root.after(100, self.drain_log_queue)

    # --- file selection callbacks --------------------------------------------

    def add_path(self, path):
        """Add one CSV file, or every CSV inside a folder."""
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if name.lower().endswith(".csv"):
                    self.add_path(os.path.join(path, name))
        elif path.lower().endswith(".csv") and path not in self.files:
            self.files.append(path)
            self.file_list.insert("end", path)

    def pick_files(self):
        for path in filedialog.askopenfilenames(
                title="Select CSV file(s)",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]):
            self.add_path(path)

    def pick_folder(self):
        folder = filedialog.askdirectory(title="Select a folder of CSV files")
        if folder:
            before = len(self.files)
            self.add_path(folder)
            if len(self.files) == before:
                messagebox.showinfo("No CSVs", "No .csv files found in that folder.")

    def clear_files(self):
        self.files = []
        self.file_list.delete(0, "end")

    # --- conversion (worker thread) -------------------------------------------

    def start_conversion(self):
        if self.working:
            return
        if not self.files:
            messagebox.showinfo("Nothing to convert",
                                "Add at least one CSV file first.")
            return

        self.working = True
        self.convert_btn.config(state="disabled", text="Converting…")

        # daemon=True means the thread won't keep the app alive if the
        # window is closed mid-conversion.
        threading.Thread(target=self.worker,
                         args=(list(self.files),), daemon=True).start()

    def worker(self, paths):
        """Runs on the background thread. GUI access only via the queue."""
        log = self.log_queue.put
        ok = failed = 0
        for csv_path in paths:
            log("Converting: " + os.path.basename(csv_path))
            try:
                convert(csv_path, log=log)
                ok += 1
            except Exception as exc:
                log("  ERROR: {}".format(exc))
                failed += 1
            log("")
        log("Done: {} converted, {} failed.".format(ok, failed))
        log(("__DONE_OK__", "__DONE_FAIL__")[failed > 0])

    # --- GUI-thread queue polling ----------------------------------------------

    def drain_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                if line in ("__DONE_OK__", "__DONE_FAIL__"):
                    self.working = False
                    self.convert_btn.config(state="normal", text="Convert")
                    continue
                self.log_box.config(state="normal")
                self.log_box.insert("end", line + "\n")
                self.log_box.see("end")
                self.log_box.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.drain_log_queue)


def main():
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
