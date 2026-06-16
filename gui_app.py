"""
Desktop GUI for the Carnaval/Recife Instagram epidemiological scraper.

Three things the user asked for:
  1) enter credentials      -> Setup tab (Instagram login + Ollama settings)
  2) create a token         -> "Create Token" runs the login and saves the reusable
                               session (ig_state.json) that every scrape loads
  3) Excel output           -> every run writes an .xlsx workbook

Built with PySide6. The scraping runs in a background QThread so the window never
freezes; log output is streamed live into the panel at the bottom.

Run:  python gui_app.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import traceback
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, QThread, Signal, QSettings, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QSpinBox, QPlainTextEdit, QProgressBar, QFileDialog, QMessageBox, QRadioButton,
    QButtonGroup, QTableView, QSplitter,
)

import config
import ig_scraper

MODELS = ["qwen2.5:14b", "qwen2.5:7b", "gemma3:12b", "gemma3:27b", "llama3.1:8b"]


# ---------------------------------------------------------------------------
# Background worker: runs an asyncio coroutine and streams stdout as log lines.
# ---------------------------------------------------------------------------
class _SignalStream:
    """File-like object that emits each completed line through a Qt signal."""

    def __init__(self, emit):
        self._emit = emit
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(line)

    def flush(self):
        if self._buf:
            self._emit(self._buf)
            self._buf = ""


class Worker(QThread):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, coro_factory, parent=None):
        super().__init__(parent)
        self._factory = coro_factory  # zero-arg callable returning a coroutine

    def run(self):
        stream = _SignalStream(self.log.emit)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = stream
        try:
            # On Unix, let subprocesses (Playwright's driver) be reaped from this thread.
            if sys.platform != "win32":
                try:
                    asyncio.set_child_watcher(asyncio.ThreadedChildWatcher())
                except Exception:  # noqa: BLE001  (deprecated/removed on some 3.12+ builds)
                    pass
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._factory())
            finally:
                loop.close()
            self.done.emit(result)
        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())
        finally:
            stream.flush()
            sys.stdout, sys.stderr = old_out, old_err


# ---------------------------------------------------------------------------
# Read-only table model to preview a result DataFrame.
# ---------------------------------------------------------------------------
class DataFrameModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame | None = None):
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._df.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        val = self._df.iloc[index.row(), index.column()]
        return "" if pd.isna(val) else str(val)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return str(self._df.columns[section])
        return str(section + 1)

    def set_dataframe(self, df: pd.DataFrame):
        self.beginResetModel()
        self._df = df
        self.endResetModel()


def _split_list(text: str) -> list[str]:
    """Parse a comma/newline separated field into a clean list (no @, no #)."""
    parts = [p.strip().lstrip("@#").strip() for chunk in text.splitlines() for p in chunk.split(",")]
    return [p for p in parts if p]


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Carnaval/Recife — Instagram Epidemiological Scraper")
        self.resize(960, 760)
        self.settings = QSettings("CIE", "CarnavalScraper")
        self._worker: Worker | None = None

        tabs = QTabWidget()
        tabs.addTab(self._build_setup_tab(), "1 · Setup & Token")
        tabs.addTab(self._build_scrape_tab(), "2 · Scrape & Export")
        tabs.addTab(self._build_results_tab(), "3 · Results")
        self._tabs = tabs

        # Log + progress live at the bottom, visible from every tab.
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setFont(QFont("Consolas" if sys.platform == "win32" else "monospace", 9))
        self.log_view.setMaximumBlockCount(5000)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)  # idle
        self.status = QLabel("Ready.")

        bottom = QGroupBox("Log")
        bl = QVBoxLayout(bottom)
        bl.addWidget(self.log_view)
        bl.addWidget(self.progress)
        bl.addWidget(self.status)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(tabs)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.addWidget(splitter)
        self.setCentralWidget(central)

        self._load_settings()
        self._refresh_token_status()

    # ----- Setup tab ------------------------------------------------------
    def _build_setup_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)

        cred = QGroupBox("Instagram credentials")
        f = QFormLayout(cred)
        self.in_user = QLineEdit()
        self.in_pass = QLineEdit(echoMode=QLineEdit.Password)
        self.show_browser = QCheckBox("Show browser window during login (needed for 2FA / challenges)")
        self.show_browser.setChecked(True)
        f.addRow("Username:", self.in_user)
        f.addRow("Password:", self.in_pass)
        f.addRow("", self.show_browser)
        note = QLabel("The password is not saved to disk. It is used only to create the session token.")
        note.setStyleSheet("color: gray;")
        f.addRow("", note)
        outer.addWidget(cred)

        tok = QGroupBox("Session token  (ig_state.json — the reusable login)")
        tf = QFormLayout(tok)
        row = QHBoxLayout()
        self.in_state = QLineEdit(config.STATE_PATH)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._pick_save_file(self.in_state, "JSON (*.json)"))
        row.addWidget(self.in_state)
        row.addWidget(browse)
        rw = QWidget(); rw.setLayout(row)
        tf.addRow("Token file:", rw)
        self.token_status = QLabel()
        tf.addRow("Status:", self.token_status)
        self.btn_token = QPushButton("Create Token  (log in & save session)")
        self.btn_token.clicked.connect(self._on_create_token)
        tf.addRow("", self.btn_token)
        outer.addWidget(tok)

        oll = QGroupBox("Local LLM (Ollama)")
        of = QFormLayout(oll)
        self.in_ollama = QLineEdit(config.OLLAMA_BASE_URL)
        self.cmb_extract = QComboBox(editable=True)
        self.cmb_extract.addItems(MODELS)
        self.cmb_extract.setCurrentText(config.EXTRACTION_MODEL)
        self.cmb_classify = QComboBox(editable=True)
        self.cmb_classify.addItems(MODELS)
        self.cmb_classify.setCurrentText(config.CLASSIFIER_MODEL)
        of.addRow("Ollama URL:", self.in_ollama)
        of.addRow("Extraction model:", self.cmb_extract)
        of.addRow("Classifier model:", self.cmb_classify)
        of.addRow("", QLabel("On a 20 GB RTX 4000 Ada, qwen2.5:14b is the recommended default."))
        outer.addWidget(oll)

        save = QPushButton("Save settings")
        save.clicked.connect(self._save_settings)
        outer.addWidget(save, alignment=Qt.AlignRight)
        outer.addStretch(1)
        return w

    # ----- Scrape tab -----------------------------------------------------
    def _build_scrape_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)

        src = QGroupBox("What to scrape")
        sl = QVBoxLayout(src)
        self.rb_hashtags = QRadioButton("Posts from hashtags")
        self.rb_pages = QRadioButton("Posts from select pages (feed)")
        self.rb_stories = QRadioButton("Stories from select pages")
        self.rb_hashtags.setChecked(True)
        self.src_group = QButtonGroup(self)
        for rb in (self.rb_hashtags, self.rb_pages, self.rb_stories):
            self.src_group.addButton(rb)
            sl.addWidget(rb)
        outer.addWidget(src)

        inp = QGroupBox("Inputs  (comma or newline separated)")
        fi = QFormLayout(inp)
        self.in_hashtags = QLineEdit(", ".join(config.HASHTAGS))
        self.in_feed = QLineEdit(", ".join(config.FEED_PAGES))
        self.in_stories = QLineEdit(", ".join(config.STORY_PAGES))
        fi.addRow("Hashtags:", self.in_hashtags)
        fi.addRow("Feed pages:", self.in_feed)
        fi.addRow("Story pages:", self.in_stories)
        outer.addWidget(inp)

        opt = QGroupBox("Options")
        fo = QFormLayout(opt)
        self.sp_max = QSpinBox(); self.sp_max.setRange(1, 2000); self.sp_max.setValue(200)
        self.sp_conc = QSpinBox(); self.sp_conc.setRange(1, 8); self.sp_conc.setValue(2)
        self.cb_classify = QCheckBox("Classify public-health risk after scraping")
        self.cb_classify.setChecked(True)
        fo.addRow("Max links:", self.sp_max)
        fo.addRow("Concurrency:", self.sp_conc)
        fo.addRow("", self.cb_classify)
        outer.addWidget(opt)

        out = QGroupBox("Excel output")
        forw = QHBoxLayout(out)
        self.in_xlsx = QLineEdit(str(Path(config.OUTPUT_XLSX).resolve()))
        b = QPushButton("Browse…")
        b.clicked.connect(lambda: self._pick_save_file(self.in_xlsx, "Excel (*.xlsx)"))
        forw.addWidget(QLabel("File:"))
        forw.addWidget(self.in_xlsx)
        forw.addWidget(b)
        outer.addWidget(out)

        self.btn_run = QPushButton("▶  Run scrape and export to Excel")
        self.btn_run.clicked.connect(self._on_run)
        outer.addWidget(self.btn_run)
        outer.addStretch(1)
        return w

    # ----- Results tab ----------------------------------------------------
    def _build_results_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        self.table = QTableView()
        self.table_model = DataFrameModel()
        self.table.setModel(self.table_model)
        outer.addWidget(self.table)
        row = QHBoxLayout()
        self.btn_open_file = QPushButton("Open Excel file")
        self.btn_open_file.clicked.connect(self._open_output_file)
        self.btn_open_dir = QPushButton("Open output folder")
        self.btn_open_dir.clicked.connect(self._open_output_dir)
        row.addStretch(1)
        row.addWidget(self.btn_open_file)
        row.addWidget(self.btn_open_dir)
        outer.addLayout(row)
        return w

    # ----- helpers --------------------------------------------------------
    def _pick_save_file(self, line: QLineEdit, filt: str):
        path, _ = QFileDialog.getSaveFileName(self, "Choose file", line.text(), filt)
        if path:
            line.setText(path)

    def _append_log(self, line: str):
        self.log_view.appendPlainText(line)

    def _refresh_token_status(self):
        p = Path(self.in_state.text())
        if p.exists() and p.stat().st_size > 0:
            self.token_status.setText(f"✓ token present  ({p.resolve()})")
            self.token_status.setStyleSheet("color: green;")
        else:
            self.token_status.setText("✗ no token yet — click Create Token")
            self.token_status.setStyleSheet("color: #b00;")

    def _apply_config_from_ui(self):
        """Push the current UI settings into the config module the scraper reads."""
        config.OLLAMA_BASE_URL = self.in_ollama.text().strip() or config.OLLAMA_BASE_URL
        config.EXTRACTION_MODEL = self.cmb_extract.currentText().strip() or config.EXTRACTION_MODEL
        config.CLASSIFIER_MODEL = self.cmb_classify.currentText().strip() or config.CLASSIFIER_MODEL
        config.STATE_PATH = self.in_state.text().strip() or config.STATE_PATH

    def _set_busy(self, busy: bool, msg: str = ""):
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.reset()
        for b in (self.btn_run, self.btn_token):
            b.setEnabled(not busy)
        if msg:
            self.status.setText(msg)

    def _start_worker(self, factory, on_done):
        if self._worker and self._worker.isRunning():
            QMessageBox.warning(self, "Busy", "A task is already running.")
            return
        self._apply_config_from_ui()
        self._set_busy(True, "Working…")
        self._worker = Worker(factory)
        self._worker.log.connect(self._append_log)
        self._worker.done.connect(on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_failed(self, tb: str):
        self._set_busy(False, "Failed.")
        self._append_log(tb)
        QMessageBox.critical(self, "Error", tb.strip().splitlines()[-1] if tb.strip() else "Unknown error")

    # ----- actions --------------------------------------------------------
    def _on_create_token(self):
        user = self.in_user.text().strip()
        pwd = self.in_pass.text()
        if not user or not pwd:
            QMessageBox.warning(self, "Missing credentials", "Enter your Instagram username and password.")
            return
        state_path = self.in_state.text().strip() or config.STATE_PATH
        headless = not self.show_browser.isChecked()
        self._append_log(f"Creating session token for @{user} …")

        def factory():
            return ig_scraper.login_with_credentials(user, pwd, state_path=state_path, headless=headless)

        def on_done(ok):
            self._set_busy(False, "Token created." if ok else "Token saved (verify login).")
            self._refresh_token_status()
            QMessageBox.information(
                self, "Token",
                "Session token created successfully." if ok
                else "Saved a session, but login may be incomplete. Check the browser / try again.",
            )

        self._start_worker(factory, on_done)

    def _on_run(self):
        if not Path(self.in_state.text()).exists():
            QMessageBox.warning(self, "No token", "Create the session token first (Setup tab).")
            return
        if self.rb_hashtags.isChecked():
            source = "hashtags"
        elif self.rb_pages.isChecked():
            source = "pages"
        else:
            source = "stories"

        kwargs = dict(
            source=source,
            hashtags=_split_list(self.in_hashtags.text()),
            feed_pages=_split_list(self.in_feed.text()),
            story_pages=_split_list(self.in_stories.text()),
            max_links=self.sp_max.value(),
            concurrency=self.sp_conc.value(),
            classify=self.cb_classify.isChecked(),
            state_path=self.in_state.text().strip() or config.STATE_PATH,
            excel_path=self.in_xlsx.text().strip() or config.OUTPUT_XLSX,
        )
        self._append_log(f"Starting run: source={source}, excel={kwargs['excel_path']}")

        def factory():
            return ig_scraper.run_pipeline(**kwargs)

        def on_done(sheets: dict):
            self._set_busy(False, "Done.")
            primary = next(iter(sheets.values())) if sheets else pd.DataFrame()
            self.table_model.set_dataframe(primary)
            self.table.resizeColumnsToContents()
            self._tabs.setCurrentIndex(2)
            QMessageBox.information(
                self, "Done",
                f"Exported {sum(len(d) for d in sheets.values())} rows to:\n{kwargs['excel_path']}",
            )

        self._start_worker(factory, on_done)

    def _open_output_file(self):
        self._open_path(self.in_xlsx.text().strip())

    def _open_output_dir(self):
        self._open_path(str(Path(self.in_xlsx.text().strip() or ".").resolve().parent))

    def _open_path(self, path: str):
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Not found", f"Path does not exist:\n{path}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Could not open", str(e))

    # ----- settings persistence ------------------------------------------
    def _save_settings(self):
        s = self.settings
        s.setValue("user", self.in_user.text())
        s.setValue("state", self.in_state.text())
        s.setValue("ollama", self.in_ollama.text())
        s.setValue("extract", self.cmb_extract.currentText())
        s.setValue("classify", self.cmb_classify.currentText())
        s.setValue("hashtags", self.in_hashtags.text())
        s.setValue("feed", self.in_feed.text())
        s.setValue("stories", self.in_stories.text())
        s.setValue("xlsx", self.in_xlsx.text())
        self.status.setText("Settings saved.")

    def _load_settings(self):
        s = self.settings
        self.in_user.setText(s.value("user", "", str))
        self.in_state.setText(s.value("state", config.STATE_PATH, str))
        self.in_ollama.setText(s.value("ollama", config.OLLAMA_BASE_URL, str))
        self.cmb_extract.setCurrentText(s.value("extract", config.EXTRACTION_MODEL, str))
        self.cmb_classify.setCurrentText(s.value("classify", config.CLASSIFIER_MODEL, str))
        self.in_hashtags.setText(s.value("hashtags", ", ".join(config.HASHTAGS), str))
        self.in_feed.setText(s.value("feed", ", ".join(config.FEED_PAGES), str))
        self.in_stories.setText(s.value("stories", ", ".join(config.STORY_PAGES), str))
        self.in_xlsx.setText(s.value("xlsx", str(Path(config.OUTPUT_XLSX).resolve()), str))
        self.in_state.textChanged.connect(self._refresh_token_status)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
