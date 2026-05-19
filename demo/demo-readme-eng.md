# DICOM Header Extraction Demo

This folder contains demo notebooks that walk through three output methods for extracting DICOM header metadata using `dicom_to_parquet.py`.

## Demo Notebooks

| Language | Notebook |
|----------|----------|
| English | [dicom_to_parquet_demo.ipynb](dicom_to_parquet_demo.ipynb) |
| Korean | [dicom_to_parquet_demo-kor.ipynb](dicom_to_parquet_demo-kor.ipynb) |

## Three Extraction Methods

| # | Method | Function | Output |
|---|--------|----------|--------|
| 1 | **Parquet only** | `build_parquet()` | Hive-partitioned Parquet (`modality=.../tag=.../part-b0-0.parquet`) |
| 2 | **Parquet → CSV** | `build_parquet(..., export_csv=...)` | Parquet partitions + merged `all_tags.csv` |
| 3 | **CSV only** | `build_csv_direct()` | Single flat `all_tags.csv` (no Parquet) |

Each method produces identical rows — the choice depends on downstream tooling and dataset size.

## Sample Results

The demo notebooks were run against **5 MIMIC-IV Echo DICOM files** (modality: US).  
Pre-computed results for all three methods are available under:

```
files/mimic-iv-echo-sample_headers/
├── 01_parquet_only/          # Method 1 output — Hive-partitioned Parquet
├── 02_parquet_and_csv/       # Method 2 output — Parquet + all_tags.csv
└── 03_csv_only/              # Method 3 output — flat all_tags.csv only
```

Summary of the sample run:

| Metric | Value |
|--------|-------|
| DICOM files processed | 5 |
| Total rows extracted | 351 |
| Unique DICOM tags | 79 |
| Modality | US |
