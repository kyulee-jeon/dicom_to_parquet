# DICOM Header to Parquet / CSV Builder

This script converts DICOM header metadata into a tag-exploded dataset.
All tags are extracted including **private tags** (odd group numbers), nested sequences (SQ),
and multi-valued elements (VM>1).

## Installation

```bash
pip install pydicom pyarrow
```

## Output Modes

Three modes are available depending on your needs:

| Mode | Command flags | Output |
|------|--------------|--------|
| (1) Parquet only | `--out_dir DIR` | Partitioned Parquet dataset |
| (2) Parquet + CSV | `--out_dir DIR --export_csv FILE` | Parquet dataset **and** a merged CSV |
| (3) CSV only | `--csv_only FILE` | Single flat CSV, no Parquet created |

## Usage

### Mode 1 — Parquet only (recommended for large datasets)

```bash
python dicom_to_parquet.py \
  --dicom_root /project_name/dataset_name/dcm/files \
  --out_dir /project_name/dataset_name/dcm_metadata/silver \
  --skip_pixel_data \
  --shard_rows 2000000
```

### Mode 2 — Parquet, then merge all partitions to a single CSV

```bash
python dicom_to_parquet.py \
  --dicom_root /project_name/dataset_name/dcm/files \
  --out_dir /project_name/dataset_name/dcm_metadata/silver \
  --skip_pixel_data \
  --export_csv /project_name/dataset_name/dcm_metadata/all_tags.csv
```

### Mode 3 — CSV only (no Parquet)

```bash
python dicom_to_parquet.py \
  --dicom_root /project_name/dataset_name/dcm/files \
  --skip_pixel_data \
  --csv_only /project_name/dataset_name/dcm_metadata/all_tags.csv
```

## Output Structure

### Parquet (modes 1 and 2)

The script creates a **partitioned Parquet dataset** organized by `modality` and `tag`:

```
out_dir/
├── modality=MR/
│   ├── tag=00080020/
│   │   ├── part-b0-0.parquet
│   │   ├── part-b1-0.parquet
│   │   └── ...
│   ├── tag=00080030/
│   │   └── ...
│   └── ...
├── modality=CT/
│   └── ...
└── _build_summary.json
```

### CSV (modes 2 and 3)

A single flat file with all rows:

```
all_tags.csv
```

> **Note on size and time**: A merged CSV can be **very large** (tens to hundreds of GB for
> large datasets) and slow to produce. Consider Parquet-only (mode 1) and querying with
> pyarrow or DuckDB when working with large collections.

### Schema (both formats)

Each row represents one scalar value from one DICOM file:

| Column | Description |
|--------|-------------|
| `file_path` | Path to the source DICOM file |
| `study_uid` | Study Instance UID |
| `series_uid` | Series Instance UID |
| `instance_uid` | SOP Instance UID |
| `sop_class_uid` | SOP Class UID |
| `modality` | Modality (e.g., MR, CT) |
| `tag` | DICOM tag in 8-hex uppercase (e.g., `00080020`) |
| `vr` | Value Representation (e.g., `DA`, `LO`, `UN`) |
| `vm` | Value Multiplicity |
| `path` | Hierarchical path for nested sequences (e.g., `00400275[0]/00400009[0]/`) |
| `value` | Tag value as string |

**Private tags** (odd group numbers, e.g., `00091001`) are included automatically.
Unknown private tags are read as VR=`UN` and stored as `bytes(len=N;hex32=...)`.

## Command-Line Arguments

### Required

- `--dicom_root`: Top-level folder containing `.dcm` files (recursive search)

### Output target (at least one required)

- `--out_dir`: Output directory for partitioned Parquet dataset (modes 1, 2)
- `--csv_only CSV_PATH`: Write directly to a single CSV file, no Parquet (mode 3)
- `--export_csv CSV_PATH`: After writing Parquet, also merge to a single CSV (mode 2)

### Optional

- `--shard_rows`: Rows per Parquet file within each partition (default: 2,000,000)
- `--skip_pixel_data`: Exclude Pixel Data tag `(7FE0,0010)` — recommended for header-only extraction
- `--extra_skip_tags`: Additional tags to skip (space-separated, e.g., `5400100A` or `5400,100A`)
- `--max_files`: Process only N files (useful for testing)
- `--verbose_every`: Print progress every N files (default: 5000)

## Features

- **Private tags included**: All tags including private (odd-group) tags are extracted
- **Sequence expansion**: Nested SQ elements are recursed with hierarchical path tracking
- **Multi-value handling**: Multi-valued elements (VM>1) are split into separate rows
- **Partitioned by modality and tag**: Enables efficient column-pruned queries (Parquet modes)
- **Memory efficient**: Processes files in batches; flushes periodically to disk
- **Resume-friendly**: Parquet mode appends new batch files; existing files are not deleted

## Example Queries (Parquet)

After building the Parquet dataset, you can query efficiently with pyarrow:

```python
import pyarrow.dataset as ds

dataset = ds.dataset("/project_name/dataset_name/dcm_metadata/silver", format="parquet")

# All CT tags
table = dataset.to_table(filter=ds.field("modality") == "CT")
df = table.to_pandas()

# Specific tag across all modalities
table = dataset.to_table(filter=ds.field("tag") == "00080020")
df = table.to_pandas()

# Private tags only (odd group number: last 4 hex digits, first 2 hex = group)
table = dataset.to_table()
df = table.to_pandas()
private = df[df["tag"].apply(lambda t: int(t[:4], 16) % 2 == 1)]
```

## Demo

| | English | Korean |
|---|---|---|
| Notebook (`.ipynb`) | [dicom_to_parquet_demo-eng.ipynb](demo/dicom_to_parquet_demo-eng.ipynb) | [dicom_to_parquet_demo-kor.ipynb](demo/dicom_to_parquet_demo-kor.ipynb) |
| Guide (`.md`) | [demo-readme-eng.md](demo/demo-readme-eng.md) | [demo-readme-kor.md](demo/demo-readme-kor.md) |
