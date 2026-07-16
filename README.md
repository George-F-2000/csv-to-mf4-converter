# CSV → MF4 Converter

Converts AVL Drive CSVs (from `ms_avldrive_extractor.py` /
MotionSolve-HyperGraph extraction) into ASAM MDF 4.10 files via a small
tkinter GUI, packaged as a single Windows `dist\CSVtoMF4.exe` that runs on
machines with no Python installed.

> The **MF4 viewer** that used to live in this repo moved to its own
> repository: [mf4-viewer](https://github.com/George-F-2000/mf4-viewer).
> The MotionSolve **PLT → MF4** converter and the unified **SimBuilder**
> pipeline app have their own repos too.

## Project layout

| File | Role |
|---|---|
| `converter.py` | The engine: CSV → MF4 logic, channel units/scaling in `CHANNEL_CONFIG`. No GUI code. |
| `app.py` | The interface: tkinter window that calls `converter.convert()`. |
| `requirements.txt` | The packages the project needs (`pip install -r requirements.txt`). |
| `.venv\` | Virtual environment — this project's private Python + packages (shared by the sibling mf4-viewer-app). Recreate any time. |
| `build\`, `dist\`, `*.spec` | PyInstaller output. The finished program is `dist\CSVtoMF4.exe`. |

## Everyday use

Double-click `dist\CSVtoMF4.exe`, add CSV files (or a folder), press
Convert. Each `.mf4` is written next to its source CSV. You can also drag
CSV files onto the exe icon — they arrive pre-loaded in the list.

## Developing: run without building

```powershell
.\.venv\Scripts\python.exe app.py              # launch the converter GUI
.\.venv\Scripts\python.exe converter.py x.csv  # or test the engine alone
```

## Rebuilding the exe

```powershell
.\.venv\Scripts\pyinstaller.exe --onefile --windowed --name CSVtoMF4 --icon assets\csvtomf4.ico --add-data "assets\csvtomf4.ico;." app.py
```

Close a running copy of the app before rebuilding — Windows locks the exe.

## Changing channel units / scale factors

Edit `CHANNEL_CONFIG` in `converter.py` (physical = raw × scale + offset),
then rebuild. Columns not listed there pass through unchanged, with no unit.

## If the venv ever breaks

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
