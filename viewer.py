"""
viewer.py
================================================================================
Slim MF4 viewer - sanity-check MDF files without AVL Drive.

Opens an .mf4/.mdf file with asammdf (the same library the converter uses to
write them), lists every channel with its unit and sample count, and plots
selected channels against time. If this app can open the file and the traces
look right, the file structure is valid MDF.

How the pieces fit:
  - asammdf reads the file. `mdf.channels_db` maps each channel name to its
    (group, index) location inside the file; `mdf.get()` pulls one channel's
    samples + timestamps + unit on demand, so we never load the whole file.
  - matplotlib draws the plots. Its Figure is embedded straight into the
    tkinter window via FigureCanvasTkAgg, and NavigationToolbar2Tk gives
    zoom-to-rectangle, pan, back/forward, home, and save-as-PNG for free.
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

from asammdf import MDF


class ViewerApp:
    def __init__(self, root):
        self.root = root
        root.title("MF4 Viewer")
        root.geometry("1150x680")
        root.minsize(800, 500)

        self.mdf = None
        self.entries = {}   # treeview item id -> (channel name, group, index)

        # --- top bar: open button + file summary ------------------------------
        top = tk.Frame(root)
        top.pack(fill="x", padx=8, pady=6)

        tk.Button(top, text="Open MF4…", command=self.open_file).pack(side="left")
        self.info_var = tk.StringVar(value="No file loaded.")
        tk.Label(top, textvariable=self.info_var, anchor="w").pack(
            side="left", padx=10)

        # --- resizable split: channel list | plot area -------------------------
        paned = ttk.PanedWindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # left pane: channel tree + plot controls
        left = tk.Frame(paned)
        paned.add(left, weight=1)

        # pack order matters: widgets claim space in the order they are
        # packed, so the bottom button row is packed first to reserve its
        # strip - then the tree expands into everything that is left.
        controls = tk.Frame(left)
        controls.pack(side="bottom", fill="x", pady=(4, 0))

        tk.Button(controls, text="Plot selected",
                  command=self.plot_selected).pack(side="left")
        self.stacked_var = tk.BooleanVar(value=True)
        tk.Checkbutton(controls, text="Stacked axes",
                       variable=self.stacked_var).pack(side="left", padx=6)

        self.tree = ttk.Treeview(left, columns=("unit", "samples"),
                                 show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="Channel")
        self.tree.heading("unit", text="Unit")
        self.tree.heading("samples", text="Samples")
        self.tree.column("#0", width=180, stretch=True)
        self.tree.column("unit", width=55, anchor="center", stretch=False)
        self.tree.column("samples", width=65, anchor="e", stretch=False)

        tree_scroll = ttk.Scrollbar(left, orient="vertical",
                                    command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="left", fill="y")
        # double-click a channel to plot it on its own
        self.tree.bind("<Double-1>", lambda e: self.plot_selected())

        # right pane: matplotlib figure + its navigation toolbar
        right = tk.Frame(paned)
        paned.add(right, weight=3)

        self.fig = Figure(dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        toolbar = NavigationToolbar2Tk(self.canvas, right)  # zoom/pan/save live here
        toolbar.update()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        ax = self.fig.add_subplot(111)
        ax.set_title("Open an MF4 file, select channels, press 'Plot selected'")
        ax.set_axis_off()

        # a file dragged onto the exe arrives as a command-line argument
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self.load(sys.argv[1])

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
                item = self.tree.insert("", "end", text=name,
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

    # --- plotting ---------------------------------------------------------------

    def plot_selected(self):
        if self.mdf is None:
            messagebox.showinfo("No file", "Open an MF4 file first.")
            return
        selected = [self.entries[i] for i in self.tree.selection()
                    if i in self.entries]
        if not selected:
            messagebox.showinfo("No channels", "Select one or more channels "
                                "in the list first (Ctrl+click for several).")
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
        if self.stacked_var.get():
            # one subplot per channel, sharing the x axis: zooming time in
            # one plot zooms all of them - ideal for comparing mixed units.
            axes = self.fig.subplots(len(signals), 1, sharex=True)
            if len(signals) == 1:
                axes = [axes]
            for ax, sig in zip(axes, signals):
                ax.plot(sig.timestamps, sig.samples, linewidth=0.9)
                label = sig.name if not sig.unit else "{}\n[{}]".format(sig.name, sig.unit)
                ax.set_ylabel(label, fontsize=8)
                ax.grid(alpha=0.3)
            axes[-1].set_xlabel("time [s]")
        else:
            # everything on one axis with a legend - good for same-unit signals
            ax = self.fig.add_subplot(111)
            for sig in signals:
                label = sig.name if not sig.unit else "{} [{}]".format(sig.name, sig.unit)
                ax.plot(sig.timestamps, sig.samples, linewidth=0.9, label=label)
            ax.legend(fontsize=8)
            ax.grid(alpha=0.3)
            ax.set_xlabel("time [s]")

        self.fig.tight_layout()
        self.canvas.draw()


def main():
    root = tk.Tk()
    ViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
