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
  - Reading a channel is fast, so unlike the converter there is no worker
    thread here - everything runs on the GUI thread.

Run from source:   .venv\\Scripts\\python.exe viewer.py [file.mf4]
================================================================================
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")   # select the tkinter drawing backend before pyplot-ish imports
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)
from matplotlib.figure import Figure

from asammdf import MDF


UNCHECKED = "☐"   # empty checkbox glyph
CHECKED = "☑"     # ticked checkbox glyph

ACCENT = "#0b5ed7"     # button/plot accent blue
ACCENT_DARK = "#0a53be"


class ViewerApp:
    def __init__(self, root):
        self.root = root
        root.title("MF4 Viewer")
        root.geometry("1150x680")
        root.minsize(820, 500)

        # ttk theming: 'vista' is the native-looking theme on Windows.
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview.Heading", padding=4)

        self.mdf = None
        self.entries = {}   # treeview item id -> (channel name, group, index)
        self.checked = set()   # item ids whose checkbox is ticked

        # --- top toolbar: everything the user acts on, in one row --------------
        top = tk.Frame(root)
        top.pack(fill="x", padx=10, pady=8)

        tk.Button(top, text="Open MF4…",
                  command=self.open_file).pack(side="left")

        # accent-colored primary action, right where the eye starts
        tk.Button(top, text="▶  Plot checked",
                  command=self.plot_selected,
                  bg=ACCENT, fg="white", activebackground=ACCENT_DARK,
                  activeforeground="white", relief="flat",
                  padx=12).pack(side="left", padx=(8, 0))

        self.stacked_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Stacked axes",
                        variable=self.stacked_var).pack(side="left", padx=10)

        tk.Button(top, text="Check all",
                  command=lambda: self.set_all(True)).pack(side="left", padx=(6, 0))
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
        self.tree.heading("#0", text="Channel")
        self.tree.heading("unit", text="Unit")
        self.tree.heading("samples", text="Samples")
        self.tree.column("#0", width=200, stretch=True)
        self.tree.column("unit", width=60, anchor="center", stretch=False)
        self.tree.column("samples", width=70, anchor="e", stretch=False)

        tree_scroll = ttk.Scrollbar(left, orient="vertical",
                                    command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="left", fill="y")

        # one click anywhere on a row toggles its checkbox
        self.tree.bind("<Button-1>", self.on_tree_click)

        # right pane: matplotlib figure + its navigation toolbar
        right = tk.Frame(paned)
        paned.add(right, weight=3)

        self.fig = Figure(dpi=100, facecolor="white")
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        toolbar = NavigationToolbar2Tk(self.canvas, right)  # zoom/pan/save live here
        toolbar.update()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.show_placeholder("Open an MF4 file, tick some channels,\n"
                              "then press 'Plot checked'")

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
        self.show_placeholder("Tick some channels, then press 'Plot checked'")

    # --- plotting ---------------------------------------------------------------

    def show_placeholder(self, message):
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
            messagebox.showinfo("No channels",
                                "Tick one or more channels in the list first.")
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
            for i, (ax, sig) in enumerate(zip(axes, signals)):
                ax.plot(sig.timestamps, sig.samples, linewidth=0.9,
                        color="C{}".format(i % 10))
                label = sig.name if not sig.unit else "{}\n[{}]".format(sig.name, sig.unit)
                ax.set_ylabel(label, fontsize=8)
                self.tidy_axes(ax)
            axes[-1].set_xlabel("time [s]")
        else:
            # everything on one axis with a legend - good for same-unit signals
            ax = self.fig.add_subplot(111)
            for sig in signals:
                label = sig.name if not sig.unit else "{} [{}]".format(sig.name, sig.unit)
                ax.plot(sig.timestamps, sig.samples, linewidth=0.9, label=label)
            ax.legend(fontsize=8)
            self.tidy_axes(ax)
            ax.set_xlabel("time [s]")

        self.fig.tight_layout()
        self.canvas.draw()


def main():
    root = tk.Tk()
    ViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
