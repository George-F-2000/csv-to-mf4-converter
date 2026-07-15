# CSV → MF4 Converter (GUI)

Converts AVL Drive CSVs (from `ms_avldrive_extractor.py`) into ASAM MDF 4.10
files, via a small window instead of the command line. Packaged as a single
Windows `.exe` so it runs on machines with no Python installed.

## Project layout

| File | Role |
|---|---|
| `converter.py` | The engine: CSV → MF4 logic, channel units/scaling in `CHANNEL_CONFIG`. No GUI code. |
| `app.py` | The interface: tkinter window that calls `converter.convert()`. |
| `requirements.txt` | The packages the project needs (`pip install -r requirements.txt`). |
| `.venv\` | Virtual environment — this project's private Python + packages. Not shared, can always be deleted and recreated. |
| `build\`, `dist\`, `*.spec` | PyInstaller output. The finished program is `dist\CSVtoMF4.exe`; everything else is scaffolding. |

## Everyday use

Double-click `dist\CSVtoMF4.exe`, add CSV files (or a folder), press Convert.
Each `.mf4` is written next to its source CSV. You can also drag CSV files
onto the exe icon — they arrive pre-loaded in the list.

## Developing: run without building

Building the exe takes minutes; running the script takes seconds. While
editing, test like this:

```powershell
cd "csv-to-mf4-app"
.\.venv\Scripts\python.exe app.py            # launch the GUI
.\.venv\Scripts\python.exe converter.py x.csv  # or test the engine alone
```

Only rebuild the exe when you're happy with the behavior.

## Rebuilding the exe

```powershell
.\.venv\Scripts\pyinstaller.exe --onefile --windowed --name CSVtoMF4 app.py
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
