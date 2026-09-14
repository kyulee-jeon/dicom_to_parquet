#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DICOM -> Parquet -> single CSV, GUI front-end (Mode 2).

Packaged with PyInstaller into a standalone Windows .exe so that it runs on
machines without Python. The extraction logic is imported from
``dicom_to_parquet.py`` (not duplicated); this file only adds the GUI,
progress reporting and a small CLI passthrough used for smoke-testing the
frozen build.

Workflow exposed to the user:
  1. pick the top-level folder that contains the DICOM files (searched recursively)
  2. pick an output folder and a CSV file name
  3. press Run -> partitioned Parquet is written next to the CSV, then all
     partitions are merged into that single CSV
"""

import os
import sys
import json
import time
import argparse
import traceback
from typing import Dict, List, Optional

import pydicom

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QCheckBox,
    QSpinBox, QProgressBar, QPlainTextEdit, QFileDialog, QMessageBox,
    QGridLayout, QVBoxLayout, QHBoxLayout, QGroupBox,
)

import dicom_to_parquet as core


APP_TITLE = "DICOM Metadata Extractor (Parquet -> CSV)"


# ----------------------------------------------------------------------
# worker
# ----------------------------------------------------------------------

class _StreamToSignal:
    """Redirect stdout/stderr of the imported core functions into the log box."""

    # merge_to_csv prints one line per record batch; show those at most every
    # THROTTLE_SEC seconds so the log widget stays usable on large datasets.
    THROTTLE_SEC = 2.0
    THROTTLED_PREFIX = "[CSV] "

    def __init__(self, emit_fn):
        self._emit = emit_fn
        self._buf = ""
        self._last_throttled = 0.0

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            if not line.strip():
                continue
            if line.startswith(self.THROTTLED_PREFIX) and line.endswith("rows written ..."):
                now = time.monotonic()
                if now - self._last_throttled < self.THROTTLE_SEC:
                    continue
                self._last_throttled = now
            self._emit(line)

    def flush(self):
        if self._buf.strip():
            self._emit(self._buf.rstrip())
        self._buf = ""


class ExtractWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)          # done, total
    finished_ok = pyqtSignal(str)            # summary text
    failed = pyqtSignal(str)

    def __init__(self, dicom_root, out_dir, csv_path,
                 skip_pixel_data=True, max_files=None, shard_rows=2_000_000):
        super().__init__()
        self.dicom_root = dicom_root
        self.out_dir = out_dir
        self.csv_path = csv_path
        self.skip_pixel_data = skip_pixel_data
        self.max_files = max_files
        self.shard_rows = shard_rows
        self._cancel = False

    def cancel(self):
        self._cancel = True

    # -- main ----------------------------------------------------------
    def run(self):
        old_out, old_err = sys.stdout, sys.stderr
        stream = _StreamToSignal(self.log.emit)
        sys.stdout = sys.stderr = stream
        try:
            summary = self._extract()
            stream.flush()
            self.finished_ok.emit(json.dumps(summary, ensure_ascii=False, indent=2))
        except Exception:
            stream.flush()
            self.failed.emit(traceback.format_exc())
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    def _extract(self) -> Dict:
        dicom_root = os.path.abspath(self.dicom_root)
        out_dir = os.path.abspath(self.out_dir)
        csv_path = os.path.abspath(self.csv_path)
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)

        self.log.emit("[1/3] Scanning for .dcm files under: " + dicom_root)
        files = list(core.iter_dicom_files(dicom_root))
        if self.max_files is not None:
            files = files[: self.max_files]
        total = len(files)
        self.log.emit("      found {:,} file(s)".format(total))
        if total == 0:
            raise RuntimeError("No .dcm files were found under the selected folder.")

        skip_tags = set(core.DEFAULT_SKIP_TAGS)
        include_pixel_data = not self.skip_pixel_data

        buffer: List[Dict[str, str]] = []
        n_bad = 0
        n_rows = 0
        n_batches = 0
        n_done = 0

        self.log.emit("[2/3] Extracting headers -> Parquet: " + out_dir)
        self.progress.emit(0, total)

        for i, fp in enumerate(files, 1):
            if self._cancel:
                self.log.emit("[CANCELLED] stopping after current file")
                break
            n_done = i
            try:
                ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)
                ids = core.get_top_ids(ds)
                rows = core.explode_dataset(
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
                if n_bad <= 20:
                    self.log.emit("[WARN] failed: {} :: {}".format(fp, e))

            if len(buffer) >= self.shard_rows:
                core.write_partitioned_batch(out_dir, buffer, core.ARROW_SCHEMA, batch_id=n_batches)
                buffer = []
                n_batches += 1

            if i % 200 == 0 or i == total:
                self.progress.emit(i, total)

        if buffer:
            core.write_partitioned_batch(out_dir, buffer, core.ARROW_SCHEMA, batch_id=n_batches)
            n_batches += 1

        summary = {
            "dicom_root": dicom_root,
            "out_dir": out_dir,
            "csv_path": csv_path,
            "files_processed": n_done,
            "files_failed": n_bad,
            "rows_total": n_rows,
            "batches_written": n_batches,
            "skip_pixel_data": self.skip_pixel_data,
            "skip_tags": sorted(skip_tags),
            "compression": "zstd",
            "schema": ["{}:{}".format(f.name, f.type) for f in core.ARROW_SCHEMA],
        }
        with open(os.path.join(out_dir, "_build_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        self.log.emit("[3/3] Merging Parquet partitions -> " + csv_path)
        core.merge_to_csv(out_dir, csv_path)
        return summary


# ----------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker: Optional[ExtractWorker] = None
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle(APP_TITLE)
        self.resize(780, 580)

        # --- inputs ---
        io_box = QGroupBox("Input / Output")
        grid = QGridLayout()

        self.ed_root = QLineEdit()
        self.ed_root.setPlaceholderText("Top-level folder containing DICOM files (searched recursively)")
        btn_root = QPushButton("Browse...")
        btn_root.clicked.connect(self._pick_root)
        grid.addWidget(QLabel("DICOM folder"), 0, 0)
        grid.addWidget(self.ed_root, 0, 1)
        grid.addWidget(btn_root, 0, 2)

        self.ed_outdir = QLineEdit()
        self.ed_outdir.setPlaceholderText("Folder where the results will be saved")
        self.ed_outdir.textChanged.connect(self._update_preview)
        btn_outdir = QPushButton("Browse...")
        btn_outdir.clicked.connect(self._pick_outdir)
        grid.addWidget(QLabel("Output folder"), 1, 0)
        grid.addWidget(self.ed_outdir, 1, 1)
        grid.addWidget(btn_outdir, 1, 2)

        self.ed_name = QLineEdit("dicom_metadata.csv")
        self.ed_name.textChanged.connect(self._update_preview)
        grid.addWidget(QLabel("Output file name"), 2, 0)
        grid.addWidget(self.ed_name, 2, 1, 1, 2)

        self.lb_preview = QLabel("")
        self.lb_preview.setWordWrap(True)
        self.lb_preview.setStyleSheet("color: #555;")
        grid.addWidget(self.lb_preview, 3, 0, 1, 3)

        io_box.setLayout(grid)

        # --- options ---
        opt_box = QGroupBox("Options")
        opt = QHBoxLayout()
        self.cb_skip_pixel = QCheckBox("Skip Pixel Data (7FE0,0010)")
        self.cb_skip_pixel.setChecked(True)
        opt.addWidget(self.cb_skip_pixel)

        self.cb_limit = QCheckBox("Limit files (test run)")
        self.cb_limit.stateChanged.connect(self._toggle_limit)
        opt.addWidget(self.cb_limit)

        self.sp_limit = QSpinBox()
        self.sp_limit.setRange(1, 100000000)
        self.sp_limit.setValue(100)
        self.sp_limit.setEnabled(False)
        opt.addWidget(self.sp_limit)
        opt.addStretch(1)
        opt_box.setLayout(opt)

        # --- run / progress ---
        run_row = QHBoxLayout()
        self.btn_run = QPushButton("Extract Metadata")
        self.btn_run.clicked.connect(self._start)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_open = QPushButton("Open Output Folder")
        self.btn_open.clicked.connect(self._open_output)
        run_row.addWidget(self.btn_run)
        run_row.addWidget(self.btn_cancel)
        run_row.addWidget(self.btn_open)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.lb_status = QLabel("Ready.")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        layout = QVBoxLayout()
        layout.addWidget(io_box)
        layout.addWidget(opt_box)
        layout.addLayout(run_row)
        layout.addWidget(self.lb_status)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_view, 1)
        self.setLayout(layout)

        self._update_preview()

    # -- helpers -------------------------------------------------------
    def _toggle_limit(self):
        self.sp_limit.setEnabled(self.cb_limit.isChecked())

    def _csv_path(self) -> str:
        name = self.ed_name.text().strip() or "dicom_metadata.csv"
        if not name.lower().endswith(".csv"):
            name += ".csv"
        return os.path.join(self.ed_outdir.text().strip(), name)

    def _parquet_dir(self) -> str:
        stem = os.path.splitext(os.path.basename(self._csv_path()))[0]
        return os.path.join(self.ed_outdir.text().strip(), stem + "_parquet")

    def _update_preview(self):
        if not self.ed_outdir.text().strip():
            self.lb_preview.setText("CSV and Parquet paths appear here once an output folder is selected.")
            return
        self.lb_preview.setText(
            "CSV     : {}\nParquet : {}  (modality=.../tag=...)".format(self._csv_path(), self._parquet_dir())
        )

    def _pick_root(self):
        d = QFileDialog.getExistingDirectory(self, "Select the top-level DICOM folder", self.ed_root.text() or "")
        if d:
            self.ed_root.setText(os.path.normpath(d))

    def _pick_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "Select the output folder", self.ed_outdir.text() or "")
        if d:
            self.ed_outdir.setText(os.path.normpath(d))

    def _open_output(self):
        d = self.ed_outdir.text().strip()
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            QMessageBox.information(self, APP_TITLE, "Select an existing output folder first.")

    def _append_log(self, text: str):
        self.log_view.appendPlainText(text)

    def _on_progress(self, done: int, total: int):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(done)
        self.lb_status.setText("Extracting headers: {:,} / {:,} files".format(done, total))

    # -- run -----------------------------------------------------------
    def _start(self):
        root = self.ed_root.text().strip()
        outdir = self.ed_outdir.text().strip()
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, APP_TITLE, "Select a valid DICOM folder.")
            return
        if not outdir:
            QMessageBox.warning(self, APP_TITLE, "Select an output folder.")
            return

        csv_path = self._csv_path()
        if os.path.exists(csv_path):
            ans = QMessageBox.question(
                self, APP_TITLE,
                csv_path + "\nalready exists. Overwrite it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return

        pq_dir = self._parquet_dir()
        if os.path.isdir(pq_dir) and os.listdir(pq_dir):
            QMessageBox.warning(
                self, APP_TITLE,
                "The Parquet folder already contains data:\n" + pq_dir + "\n\n"
                "Use a different output file name, or empty that folder. "
                "Otherwise old and new partitions would be mixed into the CSV.",
            )
            return

        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.lb_status.setText("Starting...")
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self.worker = ExtractWorker(
            dicom_root=root,
            out_dir=pq_dir,
            csv_path=csv_path,
            skip_pixel_data=self.cb_skip_pixel.isChecked(),
            max_files=self.sp_limit.value() if self.cb_limit.isChecked() else None,
        )
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
            self.lb_status.setText("Cancelling after the current file...")

    def _reset_buttons(self):
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def _on_done(self, summary: str):
        self._reset_buttons()
        self.lb_status.setText("Completed.")
        self._append_log("[DONE]")
        self._append_log(summary)
        QMessageBox.information(
            self, APP_TITLE,
            "Extraction completed.\n\nCSV: {}\nParquet: {}".format(self._csv_path(), self._parquet_dir()),
        )

    def _on_failed(self, tb: str):
        self._reset_buttons()
        self.lb_status.setText("Failed.")
        self._append_log(tb)
        last = tb.strip().splitlines()[-1] if tb.strip() else "unknown error"
        QMessageBox.critical(self, APP_TITLE, "An error occurred:\n\n" + last)


# ----------------------------------------------------------------------
# CLI passthrough (same Mode 2 pipeline; used to smoke-test the frozen exe)
# ----------------------------------------------------------------------

def _run_cli(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="DICOM -> Parquet -> single CSV (Mode 2)")
    ap.add_argument("--dicom_root", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--export_csv", required=True)
    ap.add_argument("--skip_pixel_data", action="store_true")
    ap.add_argument("--max_files", type=int, default=None)
    args = ap.parse_args(argv)

    core.build_parquet(
        dicom_root=args.dicom_root,
        out_dir=args.out_dir,
        max_files=args.max_files,
        shard_rows=2_000_000,
        skip_pixel_data=args.skip_pixel_data,
        extra_skip_tags=[],
        verbose_every=1000,
        export_csv=args.export_csv,
    )
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        return _run_cli(sys.argv[1:])
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
