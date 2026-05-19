#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DICOM header -> "Silver layer" Parquet builder (tag-exploded)

Output schema (per row):
  file_path, study_uid, series_uid, instance_uid, sop_class_uid, modality,
  tag, vr, vm, path, value

- Reads DICOM with stop_before_pixels=True (fast; avoids pixel data)
- Explodes all tags, including nested SQ, and multi-valued elements (VM>1)
- Writes partitioned parquet dataset (modality=.../tag=.../part-*.parquet) to out_dir
"""

import os
import sys
import json
import argparse
from typing import Any, Dict, Iterable, List, Optional

import pydicom
from pydicom.dataset import Dataset
from pydicom.dataelem import DataElement
from pydicom.tag import Tag

import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.dataset as ds
import pyarrow.csv as pacsv


# ----------- config / utilities -----------

DEFAULT_SKIP_TAGS = {
    # PixelData won't be present when stop_before_pixels=True, but keep for safety
    "7FE00010",  # (7FE0,0010) Pixel Data
    "54001010",  # (5400,1010) Waveform Data
}

# For safety: truncate very long string representations
MAX_VALUE_CHARS = 4096


def iter_dicom_files(root: str) -> Iterable[str]:
    """Yield .dcm files under root (recursive)."""
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".dcm"):
                yield os.path.join(dirpath, fn)


def safe_str(x: Any) -> str:
    """Convert value to a bounded string for parquet storage."""
    if x is None:
        return ""
    try:
        s = str(x)
    except Exception:
        s = repr(x)
    if len(s) > MAX_VALUE_CHARS:
        s = s[:MAX_VALUE_CHARS] + "...(truncated)"
    return s


def tag_hex(elem_tag: Tag) -> str:
    """Return tag as 8-hex uppercase, e.g. 00080016."""
    return f"{int(elem_tag):08X}"


def vm_to_str(elem: DataElement) -> str:
    """Return VM as string (pydicom may provide int or '1-n')."""
    try:
        return str(elem.VM)
    except Exception:
        return ""


def get_top_ids(ds: Dataset) -> Dict[str, str]:
    """Extract common IDs for partitioning and querying."""
    def g(name: str) -> str:
        return safe_str(getattr(ds, name, ""))

    out = {
        "study_uid": g("StudyInstanceUID"),
        "series_uid": g("SeriesInstanceUID"),
        "instance_uid": g("SOPInstanceUID"),
        "sop_class_uid": g("SOPClassUID"),
        "modality": g("Modality"),
    }
    return out


# ----------- core: explode dataset -----------

def explode_dataset(
    ds: Dataset,
    file_path: str,
    ids: Dict[str, str],
    skip_tags: set,
    include_pixel_data: bool,
) -> List[Dict[str, str]]:
    """
    Explode a pydicom Dataset into row dicts. Includes nested SQ elements.
    Produces (tag, vr, vm, path, value) per scalar item.
    """
    rows: List[Dict[str, str]] = []

    # Alternative flat-walk using iterall() — kept for reference, not called.
    # def walk(current: Dataset, base_path: str) -> None:
    #     for elem in current.iterall():
    #         # iterall() flattens nested SQ; path tracking is lost.
    #         pass

    def walk_immediate(current: Dataset, base_path: str) -> None:
        # Iterates all elements including private tags (odd group numbers).
        # pydicom with force=True reads unknown private tags as VR='UN' (bytes).
        for elem in current:
            th = tag_hex(elem.tag)

            if (not include_pixel_data) and th == "7FE00010":
                continue
            if th in skip_tags:
                continue

            vr = safe_str(elem.VR)
            vm = vm_to_str(elem)

            # Sequence (SQ): recurse into items
            if vr == "SQ":
                # Some sequences can be empty
                try:
                    seq = elem.value
                except Exception:
                    seq = []
                if seq is None:
                    seq = []

                # store an optional row for the sequence container itself (often useful)
                rows.append({
                    "file_path": file_path,
                    **ids,
                    "tag": th,
                    "vr": vr,
                    "vm": vm,
                    "path": f"{base_path}{th}/",
                    "value": f"SQ[{len(seq)}]",
                })

                for i, item in enumerate(seq):
                    if isinstance(item, Dataset):
                        walk_immediate(item, f"{base_path}{th}[{i}]/")
                    else:
                        # rare: non-dataset item in SQ
                        rows.append({
                            "file_path": file_path,
                            **ids,
                            "tag": th,
                            "vr": vr,
                            "vm": vm,
                            "path": f"{base_path}{th}[{i}]/",
                            "value": safe_str(item),
                        })
                continue

            # Non-sequence: handle multi-valued
            val = elem.value

            # bytes/bytearray -> store length + first bytes hex (bounded)
            if isinstance(val, (bytes, bytearray)):
                # waveform data won't happen if excluded; still, be safe
                preview = val[:32].hex().upper()
                rows.append({
                    "file_path": file_path,
                    **ids,
                    "tag": th,
                    "vr": vr,
                    "vm": vm,
                    "path": f"{base_path}{th}[0]/",
                    "value": f"bytes(len={len(val)};hex32={preview})",
                })
                continue

            # pydicom MultiValue behaves like list/tuple
            if isinstance(val, (list, tuple)):
                for i, v in enumerate(val):
                    rows.append({
                        "file_path": file_path,
                        **ids,
                        "tag": th,
                        "vr": vr,
                        "vm": vm,
                        "path": f"{base_path}{th}[{i}]/",
                        "value": safe_str(v),
                    })
            else:
                rows.append({
                    "file_path": file_path,
                    **ids,
                    "tag": th,
                    "vr": vr,
                    "vm": vm,
                    "path": f"{base_path}{th}[0]/",
                    "value": safe_str(val),
                })

    walk_immediate(ds, base_path="")
    return rows


# ----------- parquet writing (sharded) -----------

ARROW_SCHEMA = pa.schema([
    pa.field("file_path", pa.string()),
    pa.field("study_uid", pa.string()),
    pa.field("series_uid", pa.string()),
    pa.field("instance_uid", pa.string()),
    pa.field("sop_class_uid", pa.string()),
    pa.field("modality", pa.string()),
    pa.field("tag", pa.string()),
    pa.field("vr", pa.string()),
    pa.field("vm", pa.string()),
    pa.field("path", pa.string()),
    pa.field("value", pa.string()),
])


def write_partitioned_batch(out_dir: str, rows: list, schema: pa.Schema, batch_id: int) -> None:
    """
    Safe Hive-style partitioned write:
      (example) out_dir/modality=CT/tag=00080020/part-b{batch_id}-{i}.parquet
    - No deletes
    - No overwrites (unique filenames)
    """
    if not rows:
        return

    os.makedirs(out_dir, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema)

    parquet_format = ds.ParquetFileFormat()
    write_options = parquet_format.make_write_options(
        use_dictionary=True,
        compression="zstd",
    )

    partitioning = ds.HivePartitioning(
        schema=pa.schema([
            pa.field("modality", pa.string()),
            pa.field("tag", pa.string()),
        ])
    )

    ds.write_dataset(
        data=table,
        base_dir=out_dir,
        format=parquet_format,
        file_options=write_options,
        partitioning=partitioning,
        basename_template=f"part-b{batch_id}-{{i}}.parquet",
        existing_data_behavior="overwrite_or_ignore",
    )



# ----------- optional CSV merge -----------

def merge_to_csv(out_dir: str, csv_path: str, batch_rows: int = 500_000) -> None:
    """
    Merge all partitioned Parquet files into a single flat CSV.
    WARNING: For large datasets this can be very slow and produce an enormous file.
    Streams data in batches to avoid loading everything into memory at once.
    """
    print(f"[CSV] Scanning partitions in {out_dir} ...")
    dataset = ds.dataset(out_dir, format="parquet", partitioning="hive", exclude_invalid_files=True)
    total_rows = 0
    first = True
    with open(csv_path, "wb") as fout:
        for batch in dataset.to_batches(batch_size=batch_rows):
            table = pa.Table.from_batches([batch])
            buf = pa.BufferOutputStream()
            pacsv.write_csv(
                table,
                buf,
                write_options=pacsv.WriteOptions(include_header=first),
            )
            fout.write(buf.getvalue().to_pybytes())
            total_rows += len(batch)
            first = False
            print(f"[CSV] {total_rows:,} rows written ...", file=sys.stderr)
    print(f"[CSV] Done: {total_rows:,} rows saved to {csv_path}")


# ----------- main pipeline -----------

def build_parquet(
    dicom_root: str,
    out_dir: str,
    max_files: Optional[int],
    shard_rows: int,
    skip_pixel_data: bool,
    extra_skip_tags: List[str],
    verbose_every: int,
    export_csv: Optional[str],
) -> None:
    dicom_root = os.path.abspath(dicom_root)
    out_dir = os.path.abspath(out_dir)

    skip_tags = set(DEFAULT_SKIP_TAGS)
    for t in extra_skip_tags:
        t2 = t.replace("(", "").replace(")", "").replace(",", "").replace(" ", "")
        # allow "5400,1010" or "54001010"
        if len(t2) == 8 and all(c in "0123456789ABCDEFabcdef" for c in t2):
            skip_tags.add(t2.upper())

    include_pixel_data = not skip_pixel_data

    buffer: List[Dict[str, str]] = []
    n_files = 0
    n_bad = 0
    n_rows = 0
    n_batches_written = 0

    for fp in iter_dicom_files(dicom_root):
        n_files += 1
        if max_files is not None and n_files > max_files:
            break

        try:
            ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)
            ids = get_top_ids(ds)
            rows = explode_dataset(
                ds=ds,
                file_path=fp,
                ids=ids,
                skip_tags=skip_tags,
                include_pixel_data=include_pixel_data,
            )
            buffer.extend(rows)
            n_rows += len(rows)
        except Exception as e:
            n_bad += 1
            # keep going; record minimal info
            if (n_bad <= 20) or (n_bad % 1000 == 0):
                print(f"[WARN] failed: {fp} :: {e}", file=sys.stderr)

        if verbose_every > 0 and (n_files % verbose_every == 0):
            print(f"[INFO] files={n_files:,} bad={n_bad:,} rows_buffer={len(buffer):,} rows_total={n_rows:,}")

        if len(buffer) >= shard_rows:
            write_partitioned_batch(out_dir, buffer, ARROW_SCHEMA, batch_id=n_batches_written)
            buffer = []
            n_batches_written += 1

    # flush remaining
    if buffer:
        write_partitioned_batch(out_dir, buffer, ARROW_SCHEMA, batch_id=n_batches_written)
        n_batches_written += 1

    # write a tiny run summary
    summary = {
        "dicom_root": dicom_root,
        "out_dir": out_dir,
        "files_processed": n_files,
        "files_failed": n_bad,
        "rows_total": n_rows,
        "batches_written": n_batches_written,
        "skip_pixel_data": skip_pixel_data,
        "skip_tags": sorted(list(skip_tags)),
        "compression": "zstd",
        "schema": [f"{f.name}:{f.type}" for f in ARROW_SCHEMA],
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "_build_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("[DONE]")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if export_csv:
        merge_to_csv(out_dir, export_csv)


def build_csv_direct(
    dicom_root: str,
    csv_path: str,
    max_files: Optional[int],
    skip_pixel_data: bool,
    extra_skip_tags: List[str],
    verbose_every: int,
    buffer_rows: int = 100_000,
) -> None:
    """
    Write all DICOM headers directly to a single flat CSV without creating Parquet.
    Uses the same tag-explosion logic (sequences, multi-value, private tags) as the
    Parquet mode. Buffers rows and flushes periodically to control memory usage.
    WARNING: Can be very slow and produce a very large file for large datasets.
    """
    dicom_root = os.path.abspath(dicom_root)

    skip_tags = set(DEFAULT_SKIP_TAGS)
    for t in extra_skip_tags:
        t2 = t.replace("(", "").replace(")", "").replace(",", "").replace(" ", "")
        if len(t2) == 8 and all(c in "0123456789ABCDEFabcdef" for c in t2):
            skip_tags.add(t2.upper())

    include_pixel_data = not skip_pixel_data

    buffer: List[Dict[str, str]] = []
    n_files = 0
    n_bad = 0
    n_rows = 0
    header_written = False

    def flush(fout, write_header: bool) -> None:
        if not buffer:
            return
        table = pa.Table.from_pylist(buffer, schema=ARROW_SCHEMA)
        buf = pa.BufferOutputStream()
        pacsv.write_csv(
            table, buf,
            write_options=pacsv.WriteOptions(include_header=write_header),
        )
        fout.write(buf.getvalue().to_pybytes())
        buffer.clear()

    with open(csv_path, "wb") as fout:
        for fp in iter_dicom_files(dicom_root):
            n_files += 1
            if max_files is not None and n_files > max_files:
                break

            try:
                ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)
                ids = get_top_ids(ds)
                rows = explode_dataset(
                    ds=ds,
                    file_path=fp,
                    ids=ids,
                    skip_tags=skip_tags,
                    include_pixel_data=include_pixel_data,
                )
                buffer.extend(rows)
                n_rows += len(rows)
            except Exception as e:
                n_bad += 1
                if (n_bad <= 20) or (n_bad % 1000 == 0):
                    print(f"[WARN] failed: {fp} :: {e}", file=sys.stderr)

            if verbose_every > 0 and (n_files % verbose_every == 0):
                print(f"[INFO] files={n_files:,} bad={n_bad:,} rows={n_rows:,}")

            if len(buffer) >= buffer_rows:
                flush(fout, not header_written)
                header_written = True

        flush(fout, not header_written)  # final flush

    summary = {
        "dicom_root": dicom_root,
        "csv_path": csv_path,
        "files_processed": n_files,
        "files_failed": n_bad,
        "rows_total": n_rows,
    }
    print("[DONE]")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def parse_args():
    ap = argparse.ArgumentParser(
        description="Build DICOM header Silver Layer Parquet (and optionally CSV) from a root directory of .dcm files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Output modes:
  (1) Parquet only (default):
        --dicom_root DIR --out_dir DIR
  (2) Parquet first, then merge all partitions to a single CSV:
        --dicom_root DIR --out_dir DIR --export_csv output.csv
  (3) CSV only - write directly without creating Parquet:
        --dicom_root DIR --csv_only output.csv
""",
    )
    ap.add_argument("--dicom_root", required=True,
                    help="Top-level folder containing .dcm files (recursive).")
    ap.add_argument("--out_dir", default=None,
                    help="Output directory to write partitioned Parquet dataset. "
                         "Required for modes (1) and (2). Not used for --csv_only.")
    ap.add_argument("--max_files", type=int, default=None,
                    help="Process only N files (debug). Default: all.")
    ap.add_argument("--shard_rows", type=int, default=2_000_000,
                    help="[Parquet] Rows per file within each partition. Default: 2,000,000.")
    ap.add_argument("--skip_pixel_data", action="store_true",
                    help="Exclude (7FE0,0010) Pixel Data (recommended).")
    ap.add_argument("--extra_skip_tags", nargs="*", default=[],
                    help="Additional tags to skip (e.g., 5400100A or '5400,100A').")
    ap.add_argument("--verbose_every", type=int, default=5000,
                    help="Print progress every N files. Default: 5000.")
    ap.add_argument("--export_csv", default=None, metavar="CSV_PATH",
                    help="[Mode 2] After building Parquet, merge all partitions into a single flat CSV. "
                         "WARNING: can be very slow and produce a very large file.")
    ap.add_argument("--csv_only", default=None, metavar="CSV_PATH",
                    help="[Mode 3] Skip Parquet; write all headers directly to a single flat CSV. "
                         "WARNING: can be very slow and produce a very large file.")
    return ap.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.csv_only:
        if args.out_dir or args.export_csv:
            print("[WARN] --csv_only is set; --out_dir and --export_csv are ignored.", file=sys.stderr)
        build_csv_direct(
            dicom_root=args.dicom_root,
            csv_path=args.csv_only,
            max_files=args.max_files,
            skip_pixel_data=args.skip_pixel_data,
            extra_skip_tags=args.extra_skip_tags,
            verbose_every=args.verbose_every,
        )
    else:
        if not args.out_dir:
            print("[ERROR] --out_dir is required unless --csv_only is specified.", file=sys.stderr)
            sys.exit(1)
        build_parquet(
            dicom_root=args.dicom_root,
            out_dir=args.out_dir,
            max_files=args.max_files,
            shard_rows=args.shard_rows,
            skip_pixel_data=args.skip_pixel_data,
            extra_skip_tags=args.extra_skip_tags,
            verbose_every=args.verbose_every,
            export_csv=args.export_csv,
        )
