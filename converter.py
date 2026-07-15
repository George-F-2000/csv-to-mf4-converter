"""
converter.py
================================================================================
CSV -> MF4 conversion logic (no GUI code in this file).

This is the engine: app.py (the tkinter window) imports convert() from here.
Keeping the logic separate from the interface means you can still test it
from a command line:

    python converter.py <input.csv>

and the GUI is just a thin wrapper around the same function.

physical_value = csv_value * scale + offset  (per CHANNEL_CONFIG below)
================================================================================
"""

import os

import numpy as np
import pandas as pd
from asammdf import MDF, Signal


TIME_COLUMN = "time"     # CSV column used as the MDF master channel
MDF_VERSION = "4.10"     # ASAM MDF version; 4.10 is the safest for AVL tools

# ----------------------------------------------------------------------------
#  CHANNEL CONFIG  -  unit + optional conversion per CSV column
# ----------------------------------------------------------------------------
#  IMPORTANT: verify scale factors against your MotionSolve model's units.
#  The defaults assume the model solves in mm/kg/s (MotionSolve default),
#  so accelerations arrive in mm/s^2 and torques in N*mm, and converts them
#  to the SI units AVL Drive expects. Pedals and SOC arrive as 0..1
#  fractions and are converted to %.
# ----------------------------------------------------------------------------

CHANNEL_CONFIG = {
    # csv column            unit       scale      offset   comment
    "AccelPdlPos":         ("%",       100.0,     0.0,     "Accelerator pedal position"),
    "BrakePdlPos":         ("%",       100.0,     0.0,     "Brake pedal position"),
    "BrakeOn":             ("-",       1.0,       0.0,     "Brake switch (0/1)"),

    "LongAccel":           ("m/s^2",   0.001,     0.0,     "Longitudinal acceleration (mm/s^2 -> m/s^2)"),
    "LatAccel":            ("m/s^2",   0.001,     0.0,     "Lateral acceleration (mm/s^2 -> m/s^2)"),

    "F_MotSpd":            ("rad/s",   1.0,       0.0,     "Front motor speed"),
    "F_MotTrq":            ("Nm",      0.001,     0.0,     "Front motor torque (N*mm -> N*m)"),
    "R_MotSpd":            ("rad/s",   1.0,       0.0,     "Rear motor speed"),
    "R_MotTrq":            ("Nm",      0.001,     0.0,     "Rear motor torque (N*mm -> N*m)"),

    "DriverRangeSelected": ("-",       1.0,       0.0,     "Selected gear/range"),

    "WheelSpeed_FL":       ("rad/s",   1.0,       0.0,     "Wheel speed front-left"),
    "WheelSpeed_FR":       ("rad/s",   1.0,       0.0,     "Wheel speed front-right"),
    "WheelSpeed_RL":       ("rad/s",   1.0,       0.0,     "Wheel speed rear-left"),
    "WheelSpeed_RR":       ("rad/s",   1.0,       0.0,     "Wheel speed rear-right"),

    "RESS_SOC":            ("%",       100.0,     0.0,     "Battery state of charge"),
}

# Optional: rename CSV columns to different MDF channel names.
RENAME = {
    # "csv_name": "mdf_channel_name",
}


def convert(csv_path, mf4_path=None, log=print):
    """Convert one CSV file to MF4. Returns the output path.

    `log` is any function that accepts a string - print() for the command
    line, or the GUI's log-window writer. This is how the same code serves
    both interfaces.
    """
    df = pd.read_csv(csv_path)

    if TIME_COLUMN not in df.columns:
        raise ValueError(
            "CSV has no '{}' column. Columns found: {}".format(
                TIME_COLUMN, ", ".join(df.columns)))

    t = df[TIME_COLUMN].to_numpy(dtype=np.float64)
    if len(t) < 2:
        raise ValueError("CSV has fewer than 2 rows of data.")
    if np.any(np.diff(t) <= 0):
        raise ValueError("Time column is not strictly increasing - "
                         "check the CSV for duplicated or shuffled rows.")

    signals = []
    skipped = []
    for col in df.columns:
        if col == TIME_COLUMN:
            continue

        samples = df[col].to_numpy(dtype=np.float64)
        n_nan = int(np.isnan(samples).sum())
        if n_nan == len(samples):
            skipped.append(col)
            continue  # all-NaN column (unmapped signal) - leave it out
        if n_nan:
            log("  note: '{}' has {} NaN samples (kept as NaN)".format(col, n_nan))

        unit, scale, offset, comment = CHANNEL_CONFIG.get(col, ("", 1.0, 0.0, ""))
        if scale != 1.0 or offset != 0.0:
            samples = samples * scale + offset

        signals.append(Signal(
            samples=samples,
            timestamps=t,
            name=RENAME.get(col, col),
            unit=unit,
            comment=comment,
        ))

    if not signals:
        raise ValueError("No data channels found in the CSV.")

    if mf4_path is None:
        mf4_path = os.path.splitext(csv_path)[0] + ".mf4"

    mdf = MDF(version=MDF_VERSION)
    mdf.append(signals, comment="Converted from " + os.path.basename(csv_path),
               common_timebase=True)
    mdf.save(mf4_path, overwrite=True)
    mdf.close()

    dt = float(np.median(np.diff(t)))
    log("MF4 written: {}".format(mf4_path))
    log("  {} channels, {} samples, {:.4g} s duration, ~{:.6g} s sample step".format(
        len(signals), len(t), t[-1] - t[0], dt))
    if skipped:
        log("  skipped all-NaN columns: " + ", ".join(skipped))
    return mf4_path


if __name__ == "__main__":
    # Command-line fallback so the engine stays testable without the GUI.
    import sys
    if len(sys.argv) < 2:
        print("Usage: python converter.py <input.csv> [output.mf4]")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
