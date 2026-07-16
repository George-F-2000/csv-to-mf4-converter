"""
viewer.py
================================================================================
Slim MF4 viewer - sanity-check MDF files without AVL Drive.

Opens one or MORE .mf4/.mdf files with asammdf (the same library the
converter uses to write them), lists every channel with its unit and sample
count, and plots checked signals against time. Opening several files lets
runs be overlaid and compared - e.g. a baseline maneuver vs. a tuning change.

How the pieces fit:
  - asammdf reads each file. `mdf.channels_db` maps each channel name to its
    (group, index) location inside the file; `mdf.get()` pulls one channel's
    samples + timestamps + unit on demand, so files are never fully loaded.
  - The signal list is a two-level tree: file nodes at the top, that file's
    signals underneath. tkinter's Treeview has no native checkbox column, so
    each signal row's label starts with a checkbox character (U+2610 empty /
    U+2611 checked) that a click handler toggles.
  - matplotlib draws the plots. In stacked mode, checked signals are grouped
    BY NAME - the same signal from every file shares one subplot, one color
    per file - which is what makes run-to-run comparison readable.
  - Measurement cursors (MATLAB / CANalyzer style) are vertical axvline()s
    dragged via matplotlib mouse events; the readout table interpolates
    every plotted trace at the cursor times (np.interp) and shows C1, C2
    and the C2-C1 delta, one row per file+signal.

Run from source:   .venv\\Scripts\\python.exe viewer.py [file.mf4 ...]
================================================================================
"""

import csv
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np

import matplotlib
matplotlib.use("TkAgg")   # select the tkinter drawing backend before pyplot-ish imports
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.transforms import blended_transform_factory

from asammdf import MDF


UNCHECKED = "☐"   # empty checkbox glyph
CHECKED = "☑"     # ticked checkbox glyph

ACCENT = "#0b5ed7"     # button/plot accent blue
ACCENT_DARK = "#0a53be"

CURSOR_COLORS = ("#d62728", "#2ca02c")   # C1 red, C2 green
GRAB_PIXELS = 8   # how close (in pixels) a click must be to grab a cursor


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


def file_color(file_index):
    """One consistent color per file, so run A is always the same color
    in every subplot."""
    return "C{}".format(file_index % 10)


class ViewerApp:
    def __init__(self, root):
        self.root = root
        root.title("MF4 Viewer")
        root.geometry("1280x680")
        root.minsize(900, 500)
        set_window_icon(root, "mf4viewer.ico")

        # ttk theming: 'vista' is the native-looking theme on Windows.
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview.Heading", padding=4)

        self.files = []        # [{"path", "label", "mdf"}]
        self.entries = {}      # signal item id -> (file idx, name, group, index)
        self.checked = set()   # signal item ids whose checkbox is ticked
        self.comments = {}     # signal item id -> channel comment (hover bar)

        # plot/cursor state
        self.axes = []                    # axes of the current plot
        self.plotted = []                 # [(row label, Signal)] currently drawn
        self.data_xrange = (0.0, 1.0)     # min/max time of the current plot
        self.cursor_mode = 0              # 0 = off, 1, 2
        self.cursors = []                 # [{'x', 'lines', 'label'}]
        self.last_cursor_x = [None, None]  # remembered across re-plots
        self.dragging = None              # index of cursor being dragged

        # --- top toolbar: everything the user acts on, in one row --------------
        top = tk.Frame(root)
        top.pack(fill="x", padx=10, pady=8)

        tk.Button(top, text="Open MF4…",
                  command=self.open_file).pack(side="left")

        # accent-colored primary action, right where the eye starts
        tk.Button(top, text="▶  Plot Selected Signals",
                  command=self.plot_selected,
                  bg=ACCENT, fg="white", activebackground=ACCENT_DARK,
                  activeforeground="white", relief="flat",
                  padx=12).pack(side="left", padx=(8, 0))

        self.stacked_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Stacked axes",
                        variable=self.stacked_var).pack(side="left", padx=10)

        tk.Label(top, text="Cursors:").pack(side="left", padx=(6, 2))
        self.cursor_combo = ttk.Combobox(
            top, values=("Off", "1 cursor", "2 cursors"),
            state="readonly", width=9)
        self.cursor_combo.set("Off")
        self.cursor_combo.pack(side="left")
        self.cursor_combo.bind("<<ComboboxSelected>>", self.set_cursor_mode)

        tk.Button(top, text="Check all",
                  command=lambda: self.set_all(True)).pack(side="left", padx=(10, 0))
        tk.Button(top, text="Uncheck all",
                  command=lambda: self.set_all(False)).pack(side="left", padx=(4, 0))
        tk.Button(top, text="Export CSV…",
                  command=self.export_csv).pack(side="left", padx=(10, 0))
        tk.Button(top, text="Close all files",
                  command=self.close_all).pack(side="left", padx=(4, 0))

        self.info_var = tk.StringVar(value="No file loaded.")
        tk.Label(top, textvariable=self.info_var, anchor="e",
                 fg="#444444").pack(side="right")

        ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10)

        # --- resizable split: signal tree | plot area ---------------------------
        paned = ttk.PanedWindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        # left pane: two-level tree - file nodes with signal check-rows under them
        left = tk.Frame(paned)
        paned.add(left, weight=1)

        # selectmode="none" removes the blue row highlight - the checkbox
        # glyph is the selection state now, not the highlight.
        self.tree = ttk.Treeview(left, columns=("unit", "samples"),
                                 show="tree headings", selectmode="none")
        self.tree.heading("#0", text="Signals")
        self.tree.heading("unit", text="Unit")
        self.tree.heading("samples", text="Samples")
        self.tree.column("#0", width=185, stretch=True)
        self.tree.column("unit", width=55, anchor="center", stretch=False)
        self.tree.column("samples", width=62, anchor="e", stretch=False)
        self.tree.tag_configure("file", font=("Segoe UI", 9, "bold"))

        tree_scroll = ttk.Scrollbar(left, orient="vertical",
                                    command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        # comment bar: shows the hovered signal's description (the channel
        # comment stored in the MF4). Packed to the bottom edge FIRST so it
        # keeps its strip; the tree then expands into the rest.
        self.comment_var = tk.StringVar(
            value="hover a signal to see its description")
        comment_bar = tk.Label(left, textvariable=self.comment_var,
                               anchor="w", justify="left", wraplength=290,
                               fg="#555555", font=("Segoe UI", 8))
        comment_bar.pack(side="bottom", fill="x", pady=(3, 0))
        comment_bar.bind("<Configure>", lambda e: comment_bar.config(
            wraplength=max(e.width - 8, 100)))

        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="left", fill="y")

        # one click on a signal row toggles its checkbox;
        # double-click on a FILE row sets that file's time offset
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Motion>", self.on_tree_motion)
        self.tree.bind("<Leave>", lambda e: self.comment_var.set(
            "hover a signal to see its description"))

        # right pane: plot on the left, cursor readout panel on the right
        right = tk.Frame(paned)
        paned.add(right, weight=3)

        # cursor readout - hidden until cursors are switched on
        self.readout_frame = ttk.LabelFrame(right, text="Cursor values")
        self.readout = ttk.Treeview(
            self.readout_frame, columns=("c1", "c2", "delta"),
            show="tree headings", selectmode="none", height=8)
        self.readout.heading("#0", text="Signal")
        self.readout.heading("c1", text="C1 (red)")
        self.readout.heading("c2", text="C2 (green)")
        self.readout.heading("delta", text="Δ (C2−C1)")
        self.readout.column("#0", width=190, stretch=True)
        for col in ("c1", "c2", "delta"):
            self.readout.column(col, width=85, anchor="e", stretch=False)
        self.readout.pack(fill="both", expand=True, padx=4, pady=4)

        self.plot_frame = tk.Frame(right)
        self.plot_frame.pack(side="left", fill="both", expand=True)

        self.fig = Figure(dpi=100, facecolor="white")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.mpl_toolbar = NavigationToolbar2Tk(self.canvas, self.plot_frame)
        self.mpl_toolbar.update()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # mouse events for cursor dragging
        self.canvas.mpl_connect("button_press_event", self.on_press)
        self.canvas.mpl_connect("motion_notify_event", self.on_motion)
        self.canvas.mpl_connect("button_release_event", self.on_release)

        self.show_placeholder("Open one or more MF4 files, tick some signals,\n"
                              "then press 'Plot Selected Signals'")

        # files dragged onto the exe arrive as command-line arguments
        for arg in sys.argv[1:]:
            if os.path.isfile(arg):
                self.add_file(arg)

    # --- checkbox handling ------------------------------------------------------

    def on_tree_motion(self, event):
        """Show the hovered signal's channel comment in the bar below."""
        item = self.tree.identify_row(event.y)
        if item in self.entries:
            name = self.entries[item][1]
            comment = self.comments.get(item, "")
            self.comment_var.set("{}  —  {}".format(name, comment)
                                 if comment else name + "  (no description)")
        elif item:   # a file row
            self.comment_var.set("")

    def on_tree_click(self, event):
        item = self.tree.identify_row(event.y)
        if item in self.entries:   # signal rows only; file rows just expand
            self.set_checked(item, item not in self.checked)

    def set_checked(self, item, state):
        name = self.entries[item][1]
        if state:
            self.checked.add(item)
            self.tree.item(item, text="{} {}".format(CHECKED, name))
        else:
            self.checked.discard(item)
            self.tree.item(item, text="{} {}".format(UNCHECKED, name))

    def set_all(self, state):
        for item in self.entries:
            self.set_checked(item, state)

    # --- per-file time offset ----------------------------------------------------

    def on_tree_double_click(self, event):
        item = self.tree.identify_row(event.y)
        for f in self.files:
            if f["item"] == item:
                self.ask_offset(f)
                return "break"   # don't let Treeview also toggle expansion

    def ask_offset(self, f):
        value = simpledialog.askfloat(
            "Time offset",
            "Shift '{}' along the time axis by how many seconds?\n"
            "(positive = later, negative = earlier, 0 = none)".format(f["label"]),
            initialvalue=f["offset"], parent=self.root)
        if value is None:
            return
        f["offset"] = float(value)
        suffix = "" if not f["offset"] else "  [{:+g} s]".format(f["offset"])
        self.tree.item(f["item"], text=f["label"] + suffix)
        if self.checked and self.plotted:
            self.plot_selected()   # refresh the comparison with the new shift

    # --- file loading ----------------------------------------------------------

    def open_file(self):
        paths = filedialog.askopenfilenames(
            title="Open MDF file(s)",
            filetypes=[("MDF files", "*.mf4 *.mdf *.dat"), ("All files", "*.*")])
        for path in paths:
            self.add_file(path)

    def add_file(self, path):
        """Open one more MF4 and append it to the tree (does not replace)."""
        path = os.path.abspath(path)
        if any(f["path"] == path for f in self.files):
            messagebox.showinfo("Already open",
                                os.path.basename(path) + " is already open.")
            return
        try:
            mdf = MDF(path)
        except Exception as exc:
            messagebox.showerror(
                "Cannot read file",
                "asammdf could not open this file - it is likely not a valid "
                "MDF file.\n\n{}: {}".format(type(exc).__name__, exc))
            return

        label = os.path.basename(path)
        if any(f["label"] == label for f in self.files):
            label = "{} ({})".format(label, len(self.files) + 1)
        file_idx = len(self.files)

        # file node, then that file's signals underneath
        file_item = self.tree.insert("", "end", text=label, open=True,
                                     values=("", ""), tags=("file",))
        self.files.append({"path": path, "label": label, "mdf": mdf,
                           "offset": 0.0, "item": file_item})
        masters = getattr(mdf, "masters_db", {})   # {group: master channel index}
        n_signals = 0
        for name in sorted(mdf.channels_db, key=str.lower):
            for group, index in mdf.channels_db[name]:
                if masters.get(group) == index:
                    continue   # skip time channels - time is always the x axis
                unit, samples, comment = "", "", ""
                try:
                    ch = mdf.groups[group].channels[index]
                    unit = getattr(ch, "unit", "") or ""
                    comment = getattr(ch, "comment", "") or ""
                    samples = mdf.groups[group].channel_group.cycles_nr
                except Exception:
                    pass
                item = self.tree.insert(
                    file_item, "end", text="{} {}".format(UNCHECKED, name),
                    values=(unit, samples))
                self.entries[item] = (file_idx, name, group, index)
                self.comments[item] = comment
                n_signals += 1

        self.update_info()
        self.root.title("MF4 Viewer - " + ", ".join(f["label"] for f in self.files))

    def close_all(self):
        for f in self.files:
            try:
                f["mdf"].close()
            except Exception:
                pass
        self.files = []
        self.entries = {}
        self.checked = set()
        self.comments = {}
        self.tree.delete(*self.tree.get_children())
        self.update_info()
        self.root.title("MF4 Viewer")
        self.show_placeholder("Open one or more MF4 files, tick some signals,\n"
                              "then press 'Plot Selected Signals'")

    def update_info(self):
        if not self.files:
            self.info_var.set("No file loaded.")
        elif len(self.files) == 1:
            f = self.files[0]
            self.info_var.set("{}  —  MDF v{}, {} signals".format(
                f["label"], f["mdf"].version,
                sum(1 for e in self.entries.values() if e[0] == 0)))
        else:
            self.info_var.set("{} files, {} signals  —  colors: one per file".format(
                len(self.files), len(self.entries)))

    # --- plotting ---------------------------------------------------------------

    def show_placeholder(self, message):
        self.axes = []
        self.plotted = []
        self.cursors = []
        self.readout_frame.pack_forget()
        self.fig.clf()
        ax = self.fig.add_subplot(111)
        ax.text(0.5, 0.5, message, ha="center", va="center",
                fontsize=11, color="#888888", transform=ax.transAxes)
        ax.set_axis_off()
        self.canvas.draw()

    @staticmethod
    def tidy_axes(ax):
        """Shared cosmetic treatment: light grid, no boxed-in look."""
        ax.grid(alpha=0.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    def gather_checked(self):
        """Checked signals in tree order -> [(file_idx, Signal)] with each
        file's time offset already applied to the timestamps. Reads the data
        via mdf.get(); pops an error dialog and returns None on failure."""
        picked = []
        for file_item in self.tree.get_children():
            for item in self.tree.get_children(file_item):
                if item not in self.checked:
                    continue
                file_idx, name, group, index = self.entries[item]
                try:
                    sig = self.files[file_idx]["mdf"].get(
                        name, group=group, index=index)
                    if self.files[file_idx]["offset"]:
                        sig.timestamps = sig.timestamps + self.files[file_idx]["offset"]
                except Exception as exc:
                    messagebox.showerror(
                        "Read error", "Could not read '{}' from {}:\n{}".format(
                            name, self.files[file_idx]["label"], exc))
                    return None
                if sig.samples.dtype.kind not in "iufb":
                    messagebox.showwarning(
                        "Not plottable",
                        "'{}' holds non-numeric data and was skipped.".format(name))
                    continue
                picked.append((file_idx, sig))
        return picked

    def plot_selected(self):
        if not self.files:
            messagebox.showinfo("No file", "Open an MF4 file first.")
            return
        picked = self.gather_checked()
        if picked is None:
            return
        if not picked:
            messagebox.showinfo("No signals",
                                "Tick one or more signals in the list first.")
            return

        multi_file = len(self.files) > 1
        self.plotted = []
        for file_idx, sig in picked:
            prefix = self.files[file_idx]["label"] + ": " if multi_file else ""
            row = prefix + (sig.name if not sig.unit
                            else "{} [{}]".format(sig.name, sig.unit))
            self.plotted.append((row, sig))

        self.fig.clf()
        self.cursors = []
        if self.stacked_var.get():
            # group by signal NAME: the same signal from every file shares a
            # subplot (one color per file) - the run-comparison view. With a
            # single file this degrades to one subplot per signal, as before.
            names = list(dict.fromkeys(sig.name for _f, sig in picked))
            axes = self.fig.subplots(len(names), 1, sharex=True)
            if len(names) == 1:
                axes = [axes]
            for pos, name in enumerate(names):
                ax = axes[pos]
                unit = ""
                for file_idx, sig in picked:
                    if sig.name != name:
                        continue
                    unit = unit or sig.unit
                    color = (file_color(file_idx) if multi_file
                             else "C{}".format(pos % 10))
                    ax.plot(sig.timestamps, sig.samples,
                            linewidth=0.9, color=color)
                ax.set_ylabel(name if not unit else "{}\n[{}]".format(name, unit),
                              fontsize=8)
                self.tidy_axes(ax)
            axes[-1].set_xlabel("time [s]")
            if multi_file:
                # one legend, top subplot: which color is which file
                used = list(dict.fromkeys(f for f, _s in picked))
                axes[0].legend(
                    [Line2D([0], [0], color=file_color(f), linewidth=2)
                     for f in used],
                    [self.files[f]["label"] for f in used],
                    fontsize=7, loc="best")
            self.axes = list(axes)
        else:
            # everything on one axis with a legend - good for same-unit signals
            ax = self.fig.add_subplot(111)
            for (row, sig), (_f, _s) in zip(self.plotted, picked):
                ax.plot(sig.timestamps, sig.samples, linewidth=0.9, label=row)
            ax.legend(fontsize=8)
            self.tidy_axes(ax)
            ax.set_xlabel("time [s]")
            self.axes = [ax]

        sigs = [sig for _f, sig in picked]
        self.data_xrange = (float(min(s.timestamps[0] for s in sigs)),
                            float(max(s.timestamps[-1] for s in sigs)))

        self.fig.tight_layout()
        self.rebuild_cursors()   # re-add cursors on the fresh axes (draws too)

    # --- CSV export ---------------------------------------------------------------

    def export_csv(self):
        """Write the checked signals to one CSV: a single time column on a
        uniform grid (the finest median sample step among the signals), one
        column per signal, resampled with np.interp. Cells outside a
        signal's own time range are left blank rather than extrapolated, so
        shorter runs don't produce fake flat tails in downstream plots.
        File time offsets are applied (gather_checked does that)."""
        if not self.files:
            messagebox.showinfo("No file", "Open an MF4 file first.")
            return
        picked = self.gather_checked()
        if picked is None:
            return
        if not picked:
            messagebox.showinfo("No signals",
                                "Tick one or more signals in the list first.")
            return

        out_path = filedialog.asksaveasfilename(
            title="Export checked signals as CSV",
            defaultextension=".csv",
            initialfile="signals_export.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not out_path:
            return

        multi_file = len(self.files) > 1
        columns = []   # (header, t, y)
        for file_idx, sig in picked:
            prefix = self.files[file_idx]["label"] + ": " if multi_file else ""
            header = prefix + (sig.name if not sig.unit
                               else "{} [{}]".format(sig.name, sig.unit))
            columns.append((header,
                            np.asarray(sig.timestamps, dtype=np.float64),
                            np.asarray(sig.samples, dtype=np.float64)))

        t_start = min(t[0] for _h, t, _y in columns)
        t_end = max(t[-1] for _h, t, _y in columns)
        dt = min(float(np.median(np.diff(t))) for _h, t, _y in columns)
        n_rows = int(round((t_end - t_start) / dt)) + 1
        if n_rows > 2_000_000:
            messagebox.showerror(
                "Too many rows",
                "This export would be {:,} rows (span {:.6g} s at {:.3g} s "
                "step). Zoom the selection down first.".format(
                    n_rows, t_end - t_start, dt))
            return
        grid = t_start + np.arange(n_rows) * dt

        try:
            with open(out_path, "w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["time [s]"] + [h for h, _t, _y in columns])
                resampled = []
                for _h, t, y in columns:
                    vals = np.interp(grid, t, y)
                    inside = (grid >= t[0] - dt / 2) & (grid <= t[-1] + dt / 2)
                    resampled.append((vals, inside))
                for k in range(n_rows):
                    row = ["{:.9g}".format(grid[k])]
                    row += ["{:.9g}".format(vals[k]) if inside[k] else ""
                            for vals, inside in resampled]
                    writer.writerow(row)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))
            return

        messagebox.showinfo(
            "Export complete",
            "{} signal(s), {:,} rows ({:.6g} s at {:.3g} s step)\n\n{}".format(
                len(columns), n_rows, t_end - t_start, dt, out_path))

    # --- measurement cursors ------------------------------------------------------

    def set_cursor_mode(self, event=None):
        self.cursor_mode = {"Off": 0, "1 cursor": 1,
                            "2 cursors": 2}[self.cursor_combo.get()]
        self.rebuild_cursors()

    def rebuild_cursors(self):
        """(Re)create cursor lines on the current axes to match cursor_mode."""
        for cur in self.cursors:
            for artist in cur["lines"] + [cur["label"]]:
                try:
                    artist.remove()
                except Exception:
                    pass
        self.cursors = []

        if not self.axes or self.cursor_mode == 0:
            self.readout_frame.pack_forget()
            self.canvas.draw_idle()
            return

        xmin, xmax = self.data_xrange
        defaults = (xmin + 0.25 * (xmax - xmin), xmin + 0.75 * (xmax - xmin))
        for i in range(self.cursor_mode):
            x = self.last_cursor_x[i]
            if x is None or not (xmin <= x <= xmax):
                x = defaults[i]
            self.last_cursor_x[i] = x
            lines = [ax.axvline(x, color=CURSOR_COLORS[i], linestyle="--",
                                linewidth=1.1) for ax in self.axes]
            # colored "C1"/"C2" tag riding on top of the line. The blended
            # transform pins x in data coordinates (so the tag follows the
            # cursor) but y in axes coordinates (so it sits just above the
            # top edge regardless of zoom).
            top_ax = self.axes[0]
            label = top_ax.text(
                x, 1.02, "C{}".format(i + 1),
                color=CURSOR_COLORS[i], fontsize=9, fontweight="bold",
                ha="center", va="bottom",
                transform=blended_transform_factory(
                    top_ax.transData, top_ax.transAxes))
            self.cursors.append({"x": x, "lines": lines, "label": label})

        # with one cursor, hide the C2 and delta columns
        self.readout.configure(
            displaycolumns=("c1",) if self.cursor_mode == 1
            else ("c1", "c2", "delta"))
        self.readout_frame.pack(side="right", fill="y", padx=(6, 0),
                                before=self.plot_frame)
        self.update_readout()
        self.canvas.draw_idle()

    def update_readout(self):
        """Interpolate every plotted trace at the cursor times."""
        self.readout.delete(*self.readout.get_children())
        if not self.cursors:
            return
        xs = [cur["x"] for cur in self.cursors]

        def fmt(value):
            return "{:.6g}".format(value)

        # first row: the cursor positions themselves (and Δt)
        row = [fmt(xs[0]),
               fmt(xs[1]) if len(xs) > 1 else "",
               fmt(xs[1] - xs[0]) if len(xs) > 1 else ""]
        self.readout.insert("", "end", text="time [s]", values=row)

        for row_label, sig in self.plotted:
            t = np.asarray(sig.timestamps, dtype=np.float64)
            y = np.asarray(sig.samples, dtype=np.float64)
            vals = [float(np.interp(x, t, y)) for x in xs]
            row = [fmt(vals[0]),
                   fmt(vals[1]) if len(vals) > 1 else "",
                   fmt(vals[1] - vals[0]) if len(vals) > 1 else ""]
            self.readout.insert("", "end", text=row_label, values=row)

    # mouse handling: press near a cursor grabs it, motion drags, release drops.
    # While a toolbar tool (zoom/pan) is active, toolbar.mode is non-empty and
    # we stand down so the two features don't fight over the mouse.

    def on_press(self, event):
        if (not self.cursors or event.inaxes is None
                or self.mpl_toolbar.mode):
            return
        for i, cur in enumerate(self.cursors):
            pixel_x = event.inaxes.transData.transform((cur["x"], 0))[0]
            if abs(event.x - pixel_x) <= GRAB_PIXELS:
                self.dragging = i
                return

    def on_motion(self, event):
        if self.dragging is None or event.xdata is None:
            return
        self.move_cursor(self.dragging, event.xdata)

    def on_release(self, event):
        self.dragging = None

    def move_cursor(self, index, x):
        xmin, xmax = self.data_xrange
        x = min(max(float(x), xmin), xmax)
        cur = self.cursors[index]
        cur["x"] = x
        self.last_cursor_x[index] = x
        for line in cur["lines"]:
            line.set_xdata([x, x])
        cur["label"].set_x(x)
        self.update_readout()
        self.canvas.draw_idle()


def main():
    root = tk.Tk()
    ViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
