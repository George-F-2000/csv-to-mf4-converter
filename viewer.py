"""
viewer.py
================================================================================
Slim MF4 viewer - sanity-check MDF files without AVL Drive.

Opens an .mf4/.mdf file with asammdf (the same library the converter uses to
write them), lists every channel with its unit and sample count, and plots
checked channels against time. If this app can open the file and the traces
look right, the file structure is valid MDF.

How the pieces fit:
  - asammdf reads the file. `mdf.channels_db` maps each channel name to its
    (group, index) location inside the file; `mdf.get()` pulls one channel's
    samples + timestamps + unit on demand, so we never load the whole file.
  - matplotlib draws the plots. Its Figure is embedded straight into the
    tkinter window via FigureCanvasTkAgg, and NavigationToolbar2Tk gives
    zoom-to-rectangle, pan, back/forward, home, and save-as-PNG for free.
  - Channel selection uses checkboxes. tkinter's Treeview has no native
    checkbox column, so each row's label starts with a checkbox character
    (U+2610 empty / U+2611 checked) that a click handler toggles - the
    standard tkinter idiom for check-lists.
  - Measurement cursors (like MATLAB data tips / CANalyzer cursors) are
    vertical axvline()s drawn on every subplot. matplotlib mouse events
    (button_press / motion_notify / button_release) implement dragging:
    press within a few pixels of a cursor grabs it, motion moves it, and a
    readout table on the right interpolates every plotted signal at the
    cursor times (np.interp) and shows C1, C2 and the C2-C1 delta.
  - Reading a channel is fast, so unlike the converter there is no worker
    thread here - everything runs on the GUI thread.

Run from source:   .venv\\Scripts\\python.exe viewer.py [file.mf4]
================================================================================
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

import matplotlib
matplotlib.use("TkAgg")   # select the tkinter drawing backend before pyplot-ish imports
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)
from matplotlib.figure import Figure
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

        self.mdf = None
        self.entries = {}      # treeview item id -> (channel name, group, index)
        self.checked = set()   # item ids whose checkbox is ticked

        # plot/cursor state
        self.axes = []                    # axes of the current plot
        self.plotted = []                 # Signals currently drawn
        self.data_xrange = (0.0, 1.0)     # min/max time of the current plot
        self.cursor_mode = 0              # 0 = off, 1, 2
        self.cursors = []                 # [{'x': float, 'lines': [Line2D,...]}]
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

        self.info_var = tk.StringVar(value="No file loaded.")
        tk.Label(top, textvariable=self.info_var, anchor="e",
                 fg="#444444").pack(side="right")

        ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10)

        # --- resizable split: channel list | plot area -------------------------
        paned = ttk.PanedWindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        # left pane: channel check-list
        left = tk.Frame(paned)
        paned.add(left, weight=1)

        # selectmode="none" removes the blue row highlight - the checkbox
        # glyph is the selection state now, not the highlight.
        self.tree = ttk.Treeview(left, columns=("unit", "samples"),
                                 show="tree headings", selectmode="none")
        self.tree.heading("#0", text="Signals")
        self.tree.heading("unit", text="Unit")
        self.tree.heading("samples", text="Samples")
        self.tree.column("#0", width=160, stretch=True)
        self.tree.column("unit", width=55, anchor="center", stretch=False)
        self.tree.column("samples", width=62, anchor="e", stretch=False)

        tree_scroll = ttk.Scrollbar(left, orient="vertical",
                                    command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="left", fill="y")

        # one click anywhere on a row toggles its checkbox
        self.tree.bind("<Button-1>", self.on_tree_click)

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
        self.readout.column("#0", width=150, stretch=True)
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

        self.show_placeholder("Open an MF4 file, tick some signals,\n"
                              "then press 'Plot Selected Signals'")

        # a file dragged onto the exe arrives as a command-line argument
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self.load(sys.argv[1])

    # --- checkbox handling ------------------------------------------------------

    def on_tree_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.set_checked(item, item not in self.checked)

    def set_checked(self, item, state):
        name = self.entries[item][0]
        if state:
            self.checked.add(item)
            self.tree.item(item, text="{} {}".format(CHECKED, name))
        else:
            self.checked.discard(item)
            self.tree.item(item, text="{} {}".format(UNCHECKED, name))

    def set_all(self, state):
        for item in self.entries:
            self.set_checked(item, state)

    # --- file loading ----------------------------------------------------------

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open MDF file",
            filetypes=[("MDF files", "*.mf4 *.mdf *.dat"), ("All files", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        try:
            mdf = MDF(path)
        except Exception as exc:
            messagebox.showerror(
                "Cannot read file",
                "asammdf could not open this file - it is likely not a valid "
                "MDF file.\n\n{}: {}".format(type(exc).__name__, exc))
            return

        if self.mdf is not None:
            self.mdf.close()
        self.mdf = mdf

        self.tree.delete(*self.tree.get_children())
        self.entries = {}
        self.checked = set()

        # channels_db: {name: [(group, index), ...]} - a name can appear in
        # several groups, so keep (group, index) with every tree row.
        masters = getattr(mdf, "masters_db", {})   # {group: master channel index}
        n_channels = 0
        for name in sorted(mdf.channels_db, key=str.lower):
            for group, index in mdf.channels_db[name]:
                if masters.get(group) == index:
                    continue   # skip time channels - time is always the x axis
                unit, samples = "", ""
                try:
                    unit = getattr(mdf.groups[group].channels[index], "unit", "") or ""
                    samples = mdf.groups[group].channel_group.cycles_nr
                except Exception:
                    pass
                item = self.tree.insert(
                    "", "end", text="{} {}".format(UNCHECKED, name),
                    values=(unit, samples))
                self.entries[item] = (name, group, index)
                n_channels += 1

        # file summary: duration from the first group's time channel
        duration = ""
        try:
            t = mdf.get_master(0)
            if len(t) > 1:
                duration = ", {:.4g} s".format(float(t[-1] - t[0]))
        except Exception:
            pass
        self.info_var.set("{}  —  MDF v{}, {} channels{}".format(
            os.path.basename(path), mdf.version, n_channels, duration))
        self.root.title("MF4 Viewer - " + os.path.basename(path))
        self.show_placeholder("Tick some signals, then press "
                              "'Plot Selected Signals'")

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

    def plot_selected(self):
        if self.mdf is None:
            messagebox.showinfo("No file", "Open an MF4 file first.")
            return
        selected = [self.entries[i] for i in self.tree.get_children()
                    if i in self.checked]
        if not selected:
            messagebox.showinfo("No signals",
                                "Tick one or more signals in the list first.")
            return

        signals = []
        for name, group, index in selected:
            try:
                sig = self.mdf.get(name, group=group, index=index)
            except Exception as exc:
                messagebox.showerror("Read error",
                                     "Could not read '{}':\n{}".format(name, exc))
                return
            if sig.samples.dtype.kind not in "iufb":   # int/uint/float/bool only
                messagebox.showwarning(
                    "Not plottable",
                    "'{}' holds non-numeric data and was skipped.".format(name))
                continue
            signals.append(sig)
        if not signals:
            return

        self.fig.clf()
        self.cursors = []
        if self.stacked_var.get():
            # one subplot per channel, sharing the x axis: zooming time in
            # one plot zooms all of them - ideal for comparing mixed units.
            axes = self.fig.subplots(len(signals), 1, sharex=True)
            if len(signals) == 1:
                axes = [axes]
            for i, (ax, sig) in enumerate(zip(axes, signals)):
                ax.plot(sig.timestamps, sig.samples, linewidth=0.9,
                        color="C{}".format(i % 10))
                label = sig.name if not sig.unit else "{}\n[{}]".format(sig.name, sig.unit)
                ax.set_ylabel(label, fontsize=8)
                self.tidy_axes(ax)
            axes[-1].set_xlabel("time [s]")
            self.axes = list(axes)
        else:
            # everything on one axis with a legend - good for same-unit signals
            ax = self.fig.add_subplot(111)
            for sig in signals:
                label = sig.name if not sig.unit else "{} [{}]".format(sig.name, sig.unit)
                ax.plot(sig.timestamps, sig.samples, linewidth=0.9, label=label)
            ax.legend(fontsize=8)
            self.tidy_axes(ax)
            ax.set_xlabel("time [s]")
            self.axes = [ax]

        self.plotted = signals
        self.data_xrange = (float(min(s.timestamps[0] for s in signals)),
                            float(max(s.timestamps[-1] for s in signals)))

        self.fig.tight_layout()
        self.rebuild_cursors()   # re-add cursors on the fresh axes (draws too)

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
        """Interpolate every plotted signal at the cursor times."""
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

        for sig in self.plotted:
            t = np.asarray(sig.timestamps, dtype=np.float64)
            y = np.asarray(sig.samples, dtype=np.float64)
            vals = [float(np.interp(x, t, y)) for x in xs]
            row = [fmt(vals[0]),
                   fmt(vals[1]) if len(vals) > 1 else "",
                   fmt(vals[1] - vals[0]) if len(vals) > 1 else ""]
            name = sig.name if not sig.unit else "{} [{}]".format(sig.name, sig.unit)
            self.readout.insert("", "end", text=name, values=row)

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
