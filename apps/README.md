# apps — DICOM Metadata Extractor (standalone Windows .exe)

A standalone Windows build of [`dicom_to_parquet.py`](../dicom_to_parquet.py)
**Mode 2**: extract DICOM header metadata into a partitioned Parquet dataset,
then merge all partitions into a single CSV. It runs on Windows machines that
have no Python installed.

**Download: [Releases](https://github.com/kyulee-jeon/dicom_to_parquet/releases/latest)
-> `dicom_to_parquet_exe.zip` (90.5 MB)**

The executable is published as a release asset rather than committed here, so
that cloning this repository stays light.

```
dicom_to_parquet_exe.zip
    DICOM_Metadata_Extractor.exe    <- the application, one file, no installation
    README.txt                      <- the same instructions in Korean and English
```

## Usage

1. Unzip anywhere and double-click `DICOM_Metadata_Extractor.exe`.
   The first launch takes 10-30 seconds while the one-file build unpacks itself
   into a temporary folder; later launches are faster. Windows SmartScreen warns
   about the unsigned executable: choose "More info" -> "Run anyway".
2. **DICOM folder** — the top-level folder holding the DICOM files. All
   subfolders are searched recursively; files must have the `.dcm` extension.
3. **Output folder** and **Output file name** — where the result is written.
4. Press **Extract Metadata**. Progress and warnings appear in the log box, and
   **Cancel** stops the run after the current file.

### Options

| Option | Effect |
|---|---|
| Skip Pixel Data (7FE0,0010) | On by default and recommended. Headers are parsed with `stop_before_pixels=True` either way. |
| Limit files (test run) | Process only the first N files, to check the output format before a full run. |

## Output

For an output folder `D:\result` and file name `dicom_metadata.csv`:

```
D:\result\dicom_metadata.csv                 <- single merged CSV (the deliverable)
D:\result\dicom_metadata_parquet\            <- partitioned Parquet dataset (zstd)
    modality=CT\tag=00080020\part-b0-0.parquet
    ...
    _build_summary.json                      <- file and row counts of the run
```

Columns: `file_path, study_uid, series_uid, instance_uid, sop_class_uid,
modality, tag, vr, vm, path, value` — one row per scalar element, with nested
sequences (SQ), multi-valued elements (VM>1) and private tags exploded, exactly
as in the command-line script.

Notes:

- The Parquet folder must be empty at the start of a run. If it already holds
  data, the app stops and asks for a different output file name, since old and
  new partitions would otherwise both be merged into the CSV.
- Unreadable files are counted as failures and skipped; the first 20 are shown
  in the log and the total is recorded in `_build_summary.json`.
- The CSV of a large archive can become very large. The Parquet dataset is the
  efficient form for analysis; the CSV is for convenience.

## Command line

The same executable also accepts arguments, so it can be scripted on a machine
without Python:

```bat
DICOM_Metadata_Extractor.exe --dicom_root D:\dcm ^
    --out_dir D:\result\dicom_metadata_parquet ^
    --export_csv D:\result\dicom_metadata.csv --skip_pixel_data
```

Being a windowed build, it prints nothing; check the output files instead.

## Rebuilding

Source: [`../dicom_to_parquet_gui.py`](../dicom_to_parquet_gui.py). It imports the
extraction functions from `dicom_to_parquet.py` rather than duplicating them, and
adds the PyQt5 GUI, per-file progress and cancellation.

```powershell
.\dcm_venv\Scripts\activate
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1   # -> dist\DICOM_Metadata_Extractor.exe
```

Requires `pyinstaller` and `pyqt5` in addition to `pydicom` and `pyarrow`.
`build_exe.ps1 -Console` builds the same app with a console window attached,
which is how startup errors in the frozen build are diagnosed.

The build script adds `libexpat.dll`, `libssl-3-x64.dll` and
`libcrypto-3-x64.dll` from `<base_prefix>\Library\bin`. The interpreter used here
is an Anaconda build whose extension modules link against DLLs in that folder,
which PyInstaller does not scan; without `libexpat.dll` the frozen app fails at
startup with `DLL load failed while importing pyexpat`, and in windowed mode it
hangs on an error dialog that is never shown.

Verified on a sample of CT, MR and US files: the Python script, the frozen
executable and the GUI worker all produce the same 521-row CSV.
