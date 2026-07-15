# CSV → MF4 Converter + MF4 Viewer (GUI)

Two small Windows tools for the MotionSolve → AVL Drive workflow, each
packaged as a single `.exe` that runs on machines with no Python installed:

1. **Converter** — turns AVL Drive CSVs (from `ms_avldrive_extractor.py`)
   into ASAM MDF 4.10 files.
2. **Viewer** — opens any `.mf4`/`.mdf` file, lists its channels with units
   and sample counts, and plots signals vs. time with zoom/pan. Confirms a
   file is structurally valid MDF without needing AVL Drive.

## Project layout

| File | Role |
|---|---|
| `converter.py` | The engine: CSV → MF4 logic, channel units/scaling in `CHANNEL_CONFIG`. No GUI code. |
| `app.py` | The converter interface: tkinter window that calls `converter.convert()`. |
| `viewer.py` | The MF4 viewer: asammdf reads the file, matplotlib (embedded in tkinter) plots it. |
| `requirements.txt` | The packages the project needs (`pip install -r requirements.txt`). |
| `.venv\` | Virtual environment — this project's private Python + packages. Not shared, can always be deleted and recreated. |
| `build\`, `dist\`, `*.spec` | PyInstaller output. The finished programs are `dist\CSVtoMF4.exe` and `dist\MF4Viewer.exe`; everything else is scaffolding. |

## Everyday use

**Converter:** double-click `dist\CSVtoMF4.exe`, add CSV files (or a
folder), press Convert. Each `.mf4` is written next to its source CSV. You
can also drag CSV files onto the exe icon — they arrive pre-loaded in the
list.

**Viewer:** double-click `dist\MF4Viewer.exe` (or drag an `.mf4` onto it),
then select channels — Ctrl+click for several — and press *Plot selected*
(double-click plots one directly). *Stacked axes* gives each channel its
own subplot sharing the time axis (best for mixed units); unchecked
overlays them with a legend. The toolbar under the plot has zoom-rectangle,
pan, back/forward, reset, and save-as-PNG. If the viewer can open the file
at all, its MDF structure is valid.

## Developing: run without building

Building the exe takes minutes; running the script takes seconds. While
editing, test like this:

```powershell
cd "csv-to-mf4-app"
.\.venv\Scripts\python.exe app.py              # launch the converter GUI
.\.venv\Scripts\python.exe viewer.py           # launch the MF4 viewer
.\.venv\Scripts\python.exe converter.py x.csv  # or test the engine alone
```

Only rebuild the exes when you're happy with the behavior.

## Rebuilding the exes

```powershell
.\.venv\Scripts\pyinstaller.exe --onefile --windowed --name CSVtoMF4 app.py
.\.venv\Scripts\pyinstaller.exe --onefile --windowed --name MF4Viewer viewer.py
```

- `--onefile` — pack everything into a single self-extracting exe. Big
  (numpy/pandas are heavy) and a few seconds slow to launch, but trivially
  shareable. Drop the flag to get a faster-starting folder instead.
- `--windowed` — don't open a black console window behind the GUI.
- The exe lands in `dist\`. The `build\` folder and `CSVtoMF4.spec` file are
  intermediate artifacts; safe to delete, regenerated on every build.

## Changing channel units / scale factors

Edit `CHANNEL_CONFIG` in `converter.py` (physical = raw × scale + offset),
then rebuild. Columns not listed there pass through unchanged, with no unit.

## If the venv ever breaks

Delete `.venv\` and recreate it:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
