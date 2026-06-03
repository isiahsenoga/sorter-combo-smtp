from __future__ import annotations
import logging
import os
import re
import sys
import time
import threading
from collections import Counter

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QStringListModel, QUrl
from PySide6.QtGui import QDesktopServices, QFont
try:
    from PySide6.QtWidgets import (
        QAction, QApplication, QCheckBox, QComboBox, QCompleter, QFileDialog, QGroupBox,
        QHBoxLayout, QLabel, QLineEdit, QMainWindow,
        QMessageBox, QProgressBar, QPushButton, QSplitter,
        QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    )
except ImportError:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QCompleter, QFileDialog, QGroupBox,
        QHBoxLayout, QLabel, QLineEdit, QMainWindow,
        QMessageBox, QProgressBar, QPushButton, QSplitter,
        QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    )

from processor import (
    process_dataset, delete_scanned_files,
    extract_by_domain, extract_by_email, split_by_domains, split_folder_by_domains, master_path, reports_dir, output_dir,
)
from analytics import export_report
from scanner import DEFAULT_KEYWORDS

try:
    import qdarkstyle
    _HAS_QDARKSTYLE = True
except ImportError:
    qdarkstyle = None  # type: ignore[assignment]
    _HAS_QDARKSTYLE = False

logger = logging.getLogger(__name__)

# ── theme ─────────────────────────────────────────────────────────────────────

STYLESHEET = """
QMainWindow, QDialog { background: #1e1e2e; }
QWidget { background: #1e1e2e; color: #cdd6f4; font-family: "Segoe UI", Arial; font-size: 13px; }

QTabWidget::pane  { border: 1px solid #45475a; border-radius: 4px; margin-top: -1px; }
QTabBar::tab      { background: #313244; color: #a6adc8; padding: 7px 20px; margin-right: 2px;
                    border-radius: 4px 4px 0 0; border: 1px solid #45475a; border-bottom: none; }
QTabBar::tab:selected { background: #1e1e2e; color: #cdd6f4; font-weight: bold;
                        border-bottom: 2px solid #89b4fa; }
QTabBar::tab:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                               stop:0 #45475a, stop:1 #515175); }

QMainWindow { border: 1px solid #2A2A3C; }
QWidget#main_container { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                               stop:0 #1e1e2e, stop:1 #171720); }

QTabBar::tab:hover:!selected { background: #45475a; }

QGroupBox { border: 1px solid #45475a; border-radius: 6px; margin-top: 10px; padding: 10px 8px 8px 8px;
            font-weight: bold; color: #89b4fa; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px; }

QLineEdit { background: #313244; border: 1px solid #45475a; border-radius: 4px;
            padding: 5px 9px; color: #cdd6f4; selection-background-color: #89b4fa; }
QLineEdit:focus { border-color: #89b4fa; }
QLineEdit:disabled { color: #585b70; background: #1e1e2e; }

QPushButton { background: #313244; border: 1px solid #45475a; border-radius: 5px;
              padding: 6px 16px; color: #cdd6f4; }
QPushButton:hover   { background: #45475a; border-color: #6c7086; }
QPushButton:pressed { background: #585b70; }
QPushButton:disabled { color: #45475a; border-color: #313244; background: #1e1e2e; }

QPushButton#start_btn  { background: #89b4fa; color: #1e1e2e; font-weight: bold; border: none; }
QPushButton#start_btn:hover    { background: #b4befe; }
QPushButton#start_btn:pressed  { background: #74c7ec; }
QPushButton#start_btn:disabled { background: #313244; color: #585b70; border: 1px solid #45475a; }

QPushButton#cancel_btn { background: #f38ba8; color: #1e1e2e; font-weight: bold; border: none; }
QPushButton#cancel_btn:hover    { background: #eba0ac; }
QPushButton#cancel_btn:disabled { background: #313244; color: #585b70; border: 1px solid #45475a; }

QPushButton#delete_btn { color: #f38ba8; border-color: #f38ba8; }
QPushButton#delete_btn:hover { background: #2a1a1e; }

QProgressBar { background: #313244; border: 1px solid #45475a; border-radius: 5px;
               text-align: center; height: 22px; color: #cdd6f4; font-size: 12px; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                      stop:0 #89b4fa, stop:1 #b4befe); border-radius: 4px; }

QTextEdit { background: #181825; border: 1px solid #45475a; border-radius: 4px; }
QScrollBar:vertical { background: #1e1e2e; width: 8px; border: none; }
QScrollBar::handle:vertical { background: #45475a; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #585b70; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #6c7086; border-radius: 4px;
                       background: #1e1e2e; }
QCheckBox::indicator:checked { background: #89b4fa; border-color: #89b4fa; }
QCheckBox::indicator:checked:hover { background: #89b4fa; }
QCheckBox::indicator:unchecked:hover { border-color: #89b4fa; background: rgba(137,180,250,0.15); }

QPushButton#view_output_btn { background: #89b4fa; color: #1e1e2e; font-weight: bold; border: none; }
QPushButton#view_output_btn:hover { background: #b4befe; }
QPushButton#clear_btn { background: #272735; border-color: #5b5f76; }
QPushButton#clear_btn:hover { background: #3c3f55; }

QLabel { color: #cdd6f4; }
QLabel#hint_lbl  { color: #6c7086; font-size: 11px; }
QLabel#status_lbl { color: #89b4fa; font-weight: bold; font-size: 12px; }
QLabel#elapsed_lbl { color: #6c7086; font-size: 12px; }

QSplitter::handle:vertical { background: #45475a; height: 4px; margin: 0 4px; }

QMessageBox { background: #1e1e2e; }
QMessageBox QLabel { color: #cdd6f4; }
QMessageBox QPushButton { min-width: 70px; }
"""

# ── log colours (HTML) ────────────────────────────────────────────────────────

def _log_html(text: str) -> str:
    """Wrap text in a coloured HTML span for the log widget."""
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if t.startswith("[ERROR]"):
        return f'<span style="color:#f38ba8">{t}</span>'
    if t.startswith("[INFO]") or (t.startswith("  ") and ":" not in t[:6]):
        return f'<span style="color:#6c7086">{t}</span>'
    if "─────" in t or t.startswith("-"):
        return f'<span style="color:#f9e2af;font-weight:bold">{t}</span>'
    if t.startswith("  Lines") or t.startswith("  New") or t.startswith("  Dupes") \
            or t.startswith("  Already") or t.startswith("  Errors") \
            or t.startswith("  Unique") or t.startswith("  Format"):
        return f'<span style="color:#89dceb">{t}</span>'
    if t.startswith("  Fresh") or t.startswith("  Master") \
            or t.startswith("  Reports") or t.startswith("  Done"):
        return f'<span style="color:#a6e3a1">{t}</span>'
    if t.startswith("  Top") or t.startswith("  Auto"):
        return f'<span style="color:#f9e2af">{t}</span>'
    if t.startswith("    ") and "→" in t:
        return f'<span style="color:#a6e3a1">{t}</span>'
    if t.startswith("    ") and "@" in t:
        return f'<span style="color:#6c7086">{t}</span>'
    return f'<span style="color:#cdd6f4">{t}</span>'


# ── Worker: scanner ───────────────────────────────────────────────────────────

class ScanWorker(QThread):
    progress = Signal(int, int, str)
    status   = Signal(str)
    log_msg  = Signal(str)
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, folder: str, settings: dict) -> None:
        super().__init__()
        self._folder     = folder
        self._settings   = settings
        self._cancel     = False
        self._pause_event = threading.Event()
        self._pause_event.set()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def cancel(self) -> None:
        self._cancel = True
        self._pause_event.set()

    def run(self) -> None:
        try:
            def on_progress(done: int, total: int, filename: str) -> None:
                if self._cancel:
                    raise InterruptedError
                self._pause_event.wait()
                self.progress.emit(done, total, filename)
                self.log_msg.emit(f"  {filename}")

            stats = process_dataset(
                self._folder,
                self._settings,
                progress_cb=on_progress,
                status_cb=lambda msg: self.status.emit(msg),
                pause_check=self._pause_event.wait,
                cancel_check=lambda: self._cancel,
            )
            self.finished.emit(stats)
        except InterruptedError:
            self.log_msg.emit("[INFO] Cancelled.")
            self.finished.emit({
                "total_scanned": 0, "new_written": 0,
                "duplicates_skipped": 0, "already_in_master": 0,
                "format_skipped": 0, "domains": Counter(),
                "error_count": 0, "fresh_file": "",
                "scanned_files": [], "mode": "combo",
            })
        except Exception as exc:
            logger.exception("ScanWorker raised")
            self.error.emit(str(exc))


# ── Worker: domain extractor ──────────────────────────────────────────────────

class ExtractWorker(QThread):
    progress = Signal(int, int)
    finished = Signal(str, int)
    error    = Signal(str)

    def __init__(self, query: str, mode: str, extract_type: str = "domain", emails_only: bool = False, source_file: str | None = None) -> None:
        super().__init__()
        self._query = query
        self._mode  = mode
        self._extract_type = extract_type  # "domain" or "email"
        self._emails_only = emails_only
        self._source_file = source_file

    def run(self) -> None:
        try:
            def cb(done: int, total: int) -> None:
                self.progress.emit(done, total)

            if self._extract_type.lower() == "email":
                path, count = extract_by_email(
                    self._query,
                    mode=self._mode,
                    source_file=self._source_file,
                    emails_only=self._emails_only,
                    progress_cb=cb
                )
            else:
                path, count = extract_by_domain(
                    self._query,
                    mode=self._mode,
                    source_file=self._source_file,
                    emails_only=self._emails_only,
                    progress_cb=cb
                )
            self.finished.emit(path, count)
        except Exception as exc:
            logger.exception("ExtractWorker raised")
            self.error.emit(str(exc))


# ── Worker: multi-domain splitter ─────────────────────────────────────────────

class SplitWorker(QThread):
    finished       = Signal(dict)
    error          = Signal(str)
    stats_updated  = Signal(dict)
    log_message    = Signal(str)

    def __init__(self, queries: list[str], mode: str, source_file: str) -> None:
        super().__init__()
        self._queries     = queries
        self._mode        = mode
        self._source_file = source_file

    def run(self) -> None:
        try:
            def _emit_progress(payload: dict) -> None:
                self.stats_updated.emit(payload)

            def _emit_log(message: str) -> None:
                self.log_message.emit(message)

            results = split_by_domains(
                self._source_file,
                self._queries,
                mode=self._mode,
                progress_cb=_emit_progress,
                log_cb=_emit_log,
            )
            self.finished.emit(results)
        except Exception as exc:
            logger.exception("SplitWorker raised")
            self.error.emit(str(exc))


class SplitFolderWorker(QThread):
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, queries: list[str], mode: str, folder: str) -> None:
        super().__init__()
        self._queries = queries
        self._mode    = mode
        self._folder  = folder

    def run(self) -> None:
        try:
            results = split_folder_by_domains(self._folder, self._queries, mode=self._mode)
            self.finished.emit(results)
        except Exception as exc:
            logger.exception("SplitFolderWorker raised")
            self.error.emit(str(exc))


# ── Scanner / Merger tab ──────────────────────────────────────────────────────

_PULSE_PHRASES = (
    "Loading keys", "Building keys", "Loading master",
    "Discovering", "Checking format",
)


class ScannerTab(QWidget):

    def __init__(self, mode: str) -> None:
        super().__init__()
        self._mode             = mode
        self._worker: ScanWorker | None = None
        self._split_worker: SplitWorker | None = None
        self._last_stats: dict = {}
        self._elapsed_secs     = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 10, 12, 10)

        # Master path info
        self._master_lbl = QLabel()
        self._master_lbl.setObjectName("hint_lbl")
        root.addWidget(self._master_lbl)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._mode_selector = QComboBox()
        self._mode_selector.addItems(["combo", "smtp"])
        self._mode_selector.setCurrentText(self._mode)
        self._mode_selector.currentTextChanged.connect(lambda value: self._apply_mode())
        mode_row.addWidget(self._mode_selector)
        mode_row.addStretch()
        root.addLayout(mode_row)

        # Folder row
        root.addWidget(QLabel("Folder to scan:"))
        row = QHBoxLayout()
        self._folder = QLineEdit()
        self._folder.setPlaceholderText("Select or paste folder path…")
        browse = QPushButton("Browse…")
        browse.setFixedWidth(90)
        browse.clicked.connect(self._on_browse)
        row.addWidget(self._folder)
        row.addWidget(browse)
        root.addLayout(row)

        self._folder_hint = QLabel()
        self._folder_hint.setObjectName("hint_lbl")
        self._folder_hint.setWordWrap(True)
        root.addWidget(self._folder_hint)
        self._update_folder_hint()

        # ── Options group ─────────────────────────────────────────────────────
        grp = QGroupBox("Options")
        grp_layout = QVBoxLayout(grp)
        grp_layout.setSpacing(6)

        self._kw_cb = QCheckBox("Filter by filename keywords")
        self._kw_cb.setToolTip(
            "Only process files whose name contains one of the keywords below.\n"
            "Examples: valid, good, mailaccess, combo, fresh, checked…"
        )
        self._kw_cb.toggled.connect(self._on_kw_toggled)
        grp_layout.addWidget(self._kw_cb)

        kw_row = QHBoxLayout()
        kw_row.addWidget(QLabel("Keywords:"))
        self._kw_edit = QLineEdit(",".join(DEFAULT_KEYWORDS))
        self._kw_edit.setEnabled(False)
        kw_row.addWidget(self._kw_edit)
        grp_layout.addLayout(kw_row)

        self._fmt_cb = QCheckBox("Skip non-matching files  (format check — samples 100 lines)")
        self._fmt_cb.setChecked(True)
        grp_layout.addWidget(self._fmt_cb)

        root.addWidget(grp)

        # ── Auto-split group ──────────────────────────────────────────────────
        split_grp = QGroupBox("Auto-separate by domain  (splits fresh output after scan)")
        split_layout = QVBoxLayout(split_grp)
        split_layout.setSpacing(6)

        cb_row = QHBoxLayout()
        self._split_cbs: dict[str, QCheckBox] = {}
        for label, query in [
            ("Gmail",   "gmail.com"),
            ("Yahoo",   "yahoo.com"),
            ("Outlook", "outlook.com"),
            ("Hotmail", "hotmail.com"),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(False)
            cb.setProperty("query", query)
            self._split_cbs[query] = cb
            cb_row.addWidget(cb)
        cb_row.addStretch()
        split_layout.addLayout(cb_row)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("Custom:"))
        self._split_custom = QLineEdit()
        self._split_custom.setPlaceholderText(
            "space or comma separated — e.g.  .de   @t-online.de   web.de   .net"
        )
        # Autocomplete for common domains/TLDs
        domain_suggestions = QStringListModel([
            ".de", ".net", ".com", ".co.uk", ".ru", ".cn",
            "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
            "t-online.de", "web.de", "gmx.de", "arcor.de",
            "@gmail.com", "@yahoo.com", "@outlook.com",
        ])
        domain_completer = QCompleter(domain_suggestions, self._split_custom)
        domain_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._split_custom.setCompleter(domain_completer)
        custom_row.addWidget(self._split_custom)
        split_layout.addLayout(custom_row)

        root.addWidget(split_grp)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._start_btn  = QPushButton()
        self._start_btn.setObjectName("start_btn")
        self._pause_btn  = QPushButton("Pause")
        self._pause_btn.setObjectName("pause_btn")
        self._cancel_btn = QPushButton("Stop")
        self._cancel_btn.setObjectName("cancel_btn")
        self._delete_btn = QPushButton("Delete Scanned Files")
        self._delete_btn.setObjectName("delete_btn")
        self._pause_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._delete_btn.setToolTip(
            "Permanently delete the input files that were just scanned.\n"
            "Their contents are already saved in the master file."
        )
        self._start_btn.clicked.connect(self._on_start)
        self._pause_btn.clicked.connect(self._on_pause)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._delete_btn.clicked.connect(self._on_delete)
        self._clear_btn = QPushButton("Clear Log")
        self._clear_btn.setObjectName("clear_btn")
        self._view_output_btn = QPushButton("View Output")
        self._view_output_btn.setObjectName("view_output_btn")
        self._view_output_btn.clicked.connect(self._on_view_output)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._pause_btn)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._delete_btn)
        btn_row.addWidget(self._view_output_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Progress + status ─────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setFormat("Idle")
        root.addWidget(self._progress)

        info_row = QHBoxLayout()
        self._status_lbl = QLabel("Idle")
        self._status_lbl.setObjectName("status_lbl")
        self._elapsed_lbl = QLabel("")
        self._elapsed_lbl.setObjectName("elapsed_lbl")
        info_row.addWidget(self._status_lbl, stretch=1)
        info_row.addWidget(self._elapsed_lbl)
        root.addLayout(info_row)

        eta_row = QHBoxLayout()
        self._eta_lbl = QLabel("")
        self._eta_lbl.setObjectName("hint_lbl")
        eta_row.addWidget(self._eta_lbl)
        eta_row.addStretch()
        root.addLayout(eta_row)

        # ── Log (in a splitter so user can resize) ────────────────────────────
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Vertical)
        log_container = QWidget()
        log_vbox = QVBoxLayout(log_container)
        log_vbox.setContentsMargins(0, 0, 0, 0)
        log_vbox.addWidget(QLabel("Log:"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 11))
        log_vbox.addWidget(self._log)
        splitter.addWidget(log_container)
        root.addWidget(splitter, stretch=1)
        self._apply_mode()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _log_line(self, text: str) -> None:
        self._log.append(_log_html(text))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _apply_mode(self) -> None:
        mode = self._mode_selector.currentText().lower()
        if mode == "smtp":
            fmt_tip = (
                "Samples the first 100 lines of each file.\n"
                "Files with fewer than 10% SMTP credential lines are skipped."
            )
            start_label = "Start Merge"
            master_mode = "smtp"
        else:
            fmt_tip = (
                "Samples the first 100 lines of each file.\n"
                "Files with fewer than 10% email:pass lines are skipped entirely."
            )
            start_label = "Start Scan"
            master_mode = "combo"

        self._mode = mode
        self._fmt_cb.setToolTip(fmt_tip)
        self._start_btn.setText(start_label)
        self._master_lbl.setText(f"Master: {master_path(master_mode)}")
        self._update_folder_hint()

    def _tick(self) -> None:
        self._elapsed_secs += 1
        h, rem = divmod(self._elapsed_secs, 3600)
        m, s   = divmod(rem, 60)
        self._elapsed_lbl.setText(f"Elapsed: {h:02d}:{m:02d}:{s:02d}")

    def _on_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)

        eta_match = re.search(r"ETA\s*[~:]?\s*([0-9hms:]+)", msg)
        if eta_match:
            self._eta_lbl.setText(f"ETA: {eta_match.group(1)}")
        elif "ETA" in msg:
            self._eta_lbl.setText(msg)

        pct_val: int | None = None
        pct_match = re.search(r"\((\d+)\%\)", msg)
        if pct_match:
            try:
                pct_val = max(0, min(100, int(pct_match.group(1))))
            except ValueError:
                pct_val = None

        if pct_val is not None:
            if self._progress.maximum() == 0:
                self._progress.setRange(0, 100)
            self._progress.setValue(pct_val)
            safe_msg = msg.replace('%', '%%')
            self._progress.setFormat(safe_msg)
            return

        if any(phrase in msg for phrase in _PULSE_PHRASES):
            self._progress.setRange(0, 0)
            self._progress.setFormat(msg)
        else:
            if self._progress.maximum() == 0:
                self._progress.setRange(0, 100)
            self._progress.setFormat(msg.replace('%', '%%'))

    def _on_kw_toggled(self, checked: bool) -> None:
        self._kw_edit.setEnabled(checked)

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select folder")
        if path:
            self._folder.setText(path)

    def _default_scan_folder(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data",
            "combo" if self._mode == "combo" else "smtp",
        )

    def _update_folder_hint(self) -> None:
        default_folder = self._default_scan_folder()
        if os.path.isdir(default_folder):
            self._folder.setPlaceholderText(default_folder)
            self._folder_hint.setText(
                f"Leave this blank to scan the default folder for {self._mode.upper()} mode."
            )
        else:
            self._folder.setPlaceholderText("Select or paste folder path…")
            self._folder_hint.setText("Select a folder to scan or browse to choose one.")

    def _on_view_output(self) -> None:
        main_window = self.window()
        if main_window and hasattr(main_window, "_on_open_output_folder"):
            main_window._on_open_output_folder()

    def _get_auto_split_queries(self) -> list[str]:
        split_queries: list[str] = []
        for query, cb in self._split_cbs.items():
            if cb.isChecked():
                split_queries.append(query)

        raw_custom = self._split_custom.text().strip()
        if raw_custom:
            import re as _re
            for tok in _re.split(r"[\s,]+", raw_custom):
                tok = tok.strip()
                if tok:
                    split_queries.append(tok)

        return split_queries

    def _start_auto_split(self, fresh: str, split_queries: list[str]) -> None:
        if not split_queries:
            return
        self._log_line(f"\n  Auto-separating by: {', '.join(split_queries)}")
        self._log_line("  This may take a while for large files…")
        self._progress.setRange(0, 0)
        self._progress.setFormat("Auto-splitting…")
        self._start_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)

        self._split_worker = SplitWorker(split_queries, self._mode, fresh)
        self._split_worker.stats_updated.connect(self._on_split_stats)
        self._split_worker.log_message.connect(self._log_line)
        self._split_worker.finished.connect(self._on_auto_split_finished)
        self._split_worker.error.connect(self._on_auto_split_error)
        self._split_worker.start()

    def _on_auto_split_finished(self, results: dict) -> None:
        self._split_worker = None
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._progress.setFormat("Done")
        self._log_line("\n  Auto-split complete")
        if results:
            for q, (path, count) in results.items():
                if path:
                    self._log_line(f"    {q:<25} {count:>8,}  →  {path}")
                else:
                    self._log_line(f"    {q:<25}       0   (no matches)")
        if self._last_stats.get("scanned_files"):
            self._delete_btn.setEnabled(True)
            self._log_line('\n  Click "Delete Scanned Files" to remove the input files.')
        self._start_btn.setEnabled(True)

    def _on_auto_split_error(self, msg: str) -> None:
        self._split_worker = None
        self._progress.setRange(0, 100)
        self._progress.setFormat("Error")
        self._start_btn.setEnabled(True)
        if self._last_stats.get("scanned_files"):
            self._delete_btn.setEnabled(True)
        self._log_line(f"[ERROR] Auto-split failed: {msg}")

    # ── actions ───────────────────────────────────────────────────────────────

    def _on_start(self) -> None:
        folder = self._folder.text().strip().strip('"').strip("'")
        if not folder:
            folder = self._default_scan_folder()
            if folder and os.path.isdir(folder):
                self._log_line(f"[INFO] Using default folder: {folder}")
            else:
                self._log_line("[ERROR] No scan folder selected and default folder is unavailable.")
                return
        if not os.path.isdir(folder):
            self._log_line(f"[ERROR] Not a valid directory: {folder!r}")
            return

        kw_on  = self._kw_cb.isChecked()
        raw_kw = self._kw_edit.text()
        kws    = [k.strip() for k in raw_kw.split(",") if k.strip()] or DEFAULT_KEYWORDS

        settings = {
            "mode":           self._mode,
            "keyword_filter": kw_on,
            "keywords":       kws,
            "format_check":   self._fmt_cb.isChecked(),
        }

        self._log.clear()
        self._last_stats   = {}
        self._elapsed_secs = 0
        self._elapsed_lbl.setText("")
        self._eta_lbl.setText("ETA: calculating…")
        self._status_lbl.setText("Starting…")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("Starting…")
        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText("Pause")
        self._cancel_btn.setEnabled(True)
        self._delete_btn.setEnabled(False)
        self._timer.start()

        self._worker = ScanWorker(folder, settings)
        
        # Register worker with main window for cleanup
        main_window = self.window()
        if main_window and hasattr(main_window, 'register_worker'):
            main_window.register_worker(self._worker)
        
        self._worker.progress.connect(self._on_progress)
        self._worker.status.connect(self._on_status)
        self._worker.log_msg.connect(self._log_line)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._pause_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            self._status_lbl.setText("Cancelling…")
            self._log_line("[INFO] Cancelling…")
            
            # Use a timer to wait for thread to finish
            def wait_for_cancel():
                if self._worker.isRunning():
                    self._worker.wait(timeout=1000)
                self._finish_cancel()
            
            QTimer.singleShot(100, wait_for_cancel)
    
    def _finish_cancel(self) -> None:
        """Finish cancel operation and reset UI."""
        self._worker = None
        self._timer.stop()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("Cancelled")
        self._start_btn.setEnabled(True)
        self._status_lbl.setText("Cancelled")

    def _on_pause(self) -> None:
        if self._worker and self._worker.isRunning():
            if self._pause_btn.text() == "Pause":
                self._worker.pause()
                self._pause_btn.setText("Resume")
                self._status_lbl.setText("Paused")
                self._log_line("[INFO] Paused.")
            else:
                self._worker.resume()
                self._pause_btn.setText("Pause")
                self._status_lbl.setText("Resuming…")
                self._log_line("[INFO] Resumed.")

    def _on_delete(self) -> None:
        files = self._last_stats.get("scanned_files", [])
        if not files:
            return
        reply = QMessageBox.question(
            self, "Delete scanned files?",
            f"Permanently delete {len(files)} scanned input file(s)?\n\n"
            "Their contents are already saved in the master file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted, errs = delete_scanned_files(files)
        self._log_line(
            f"\n  Deleted {deleted} file(s)." + (f"  ({errs} errors)" if errs else "")
        )
        self._delete_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int, filename: str) -> None:
        remaining = max(total - done, 0)
        if total > 0:
            pct = max(0, min(100, int(done / total * 100)))
            if self._progress.maximum() == 0:
                self._progress.setRange(0, 100)
            self._progress.setValue(pct)
            self._progress.setFormat(
                f"Reading files  {done}/{total}  ({pct}%%)  remaining {remaining}  {filename}"
            )
        else:
            self._progress.setRange(0, 0)
            self._progress.setFormat(f"Reading files… {done}  {filename}")

    def _on_finished(self, stats: dict) -> None:
        self._timer.stop()
        if self._progress.maximum() == 0:
            self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._progress.setFormat("Done")
        self._eta_lbl.setText("Done")
        self._status_lbl.setText("Done")
        self._start_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("Pause")
        self._cancel_btn.setEnabled(False)
        self._last_stats = stats
        self._master_lbl.setText(f"Master: {master_path(self._mode)}")

        total    = stats.get("total_scanned", 0)
        written  = stats.get("new_written", 0)
        dup      = stats.get("duplicates_skipped", 0)
        existing = stats.get("already_in_master", 0)
        fmt_skip = stats.get("format_skipped", 0)
        errors   = stats.get("error_count", 0)
        domains  = Counter(stats.get("domains", {}))
        fresh    = stats.get("fresh_file", "")

        self._log_line("\n--- RESULTS ---")
        self._log_line(f"  Lines scanned        : {total:,}")
        self._log_line(f"  New entries added    : {written:,}")
        self._log_line(f"  Dupes in this scan   : {dup:,}")
        self._log_line(f"  Already in master    : {existing:,}")
        if fmt_skip:
            self._log_line(f"  Format-skipped files : {fmt_skip}")
        self._log_line(f"  Errors               : {errors}")
        self._log_line(f"  Unique domains found : {len(domains):,}")

        if domains and written:
            self._log_line(f"\n  === TOP DOMAINS ({len(domains):,} total) ===")
            for dom, cnt in domains.most_common(10):
                pct = cnt / written * 100
                bar = "#" * max(1, int(pct / 2.5))
                self._log_line(f"    {dom:<30} {cnt:>10,}  {pct:>6.2f}%  {bar}")
            if len(domains) > 10:
                self._log_line(f"    ... and {len(domains) - 10} more domains")

        self._log_line(f"\n  [RESULTS]")
        if fresh:
            fname = os.path.basename(fresh)
            self._log_line(f"  Fresh file: {fname}")
        else:
            self._log_line(f"  No new entries - fresh file not created.")
        self._log_line(f"  Mode: {'GUI (Combo)' if self._mode == 'combo' else 'GUI (SMTP)'}")

        rpt = reports_dir()
        export_report(domains, written, rpt)
        self._log_line(f"\n  Master: {master_path(self._mode)}")
        self._log_line(f"  Reports: {rpt}")

        if fresh:
            split_queries = self._get_auto_split_queries()
            if split_queries:
                self._start_auto_split(fresh, split_queries)
                return

        if stats.get("scanned_files"):
            self._delete_btn.setEnabled(True)
            self._log_line('\n  Click "Delete Scanned Files" to remove the input files.')

    def _on_error(self, msg: str) -> None:
        self._timer.stop()
        if self._progress.maximum() == 0:
            self._progress.setRange(0, 100)
        self._progress.setFormat("Error")
        self._eta_lbl.setText("")
        self._status_lbl.setText("Error")
        self._start_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("Pause")
        self._cancel_btn.setEnabled(False)
        self._log_line(f"[ERROR] {msg}")


# ── Domain extractor tab ──────────────────────────────────────────────────────

class ExtractorTab(QWidget):

    def __init__(self) -> None:
        super().__init__()
        self._worker: ExtractWorker | None = None
        self._split_worker: SplitWorker | None = None
        self._ext_start_time = 0.0
        self._eta_lbl: QLabel | None = None
        self._line_progress_lbl: QLabel | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 10, 12, 10)

        hint = QLabel(
            "Extract all entries matching a domain/TLD or email from the master file or a custom file.\n"
            "Examples:  de   .de   gmail.com   .net   yahoo   @t-online.de   user@gmail.com   john"
        )
        hint.setObjectName("hint_lbl")
        root.addWidget(hint)

        # Source mode and extraction type
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["combo", "smtp"])
        self._mode_combo.setCurrentIndex(0)
        self._mode_combo.setEditable(False)
        self._mode_combo.setFixedWidth(120)
        self._mode_combo.currentIndexChanged.connect(lambda _: self._update_master_info())
        mode_row.addWidget(self._mode_combo)
        
        mode_row.addSpacing(30)
        mode_row.addWidget(QLabel("Extract by:"))
        self._extract_type_combo = QComboBox()
        self._extract_type_combo.addItems(["Domain", "Email"])
        self._extract_type_combo.setCurrentIndex(0)
        self._extract_type_combo.setEditable(False)
        self._extract_type_combo.setFixedWidth(120)
        self._extract_type_combo.currentIndexChanged.connect(self._on_extract_type_changed)
        mode_row.addWidget(self._extract_type_combo)
        mode_row.addStretch()
        root.addLayout(mode_row)

        self._master_info = QLabel()
        self._master_info.setObjectName("hint_lbl")
        self._update_master_info()
        root.addWidget(self._master_info)

        # Custom source file (optional)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Or split custom file:"))
        self._custom_file = QLineEdit()
        self._custom_file.setPlaceholderText("Leave empty to use master file… or browse a fresh output file")
        browse_src = QPushButton("Browse…")
        browse_src.setFixedWidth(90)
        browse_src.clicked.connect(self._on_browse_source)
        source_row.addWidget(self._custom_file)
        source_row.addWidget(browse_src)
        root.addLayout(source_row)

        # Query row with autocomplete
        q_row = QHBoxLayout()
        self._query_label = QLabel("Domain / TLD:")
        q_row.addWidget(self._query_label)
        self._query = QLineEdit()
        self._query.setPlaceholderText("e.g.  de   or   gmail.com   or   .net")
        self._query.returnPressed.connect(self._on_extract)
        # Autocomplete suggestions
        query_suggestions = QStringListModel([
            "de", "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
            ".de", ".net", ".com", ".ru", ".cn", ".co.uk",
            "t-online.de", "web.de", "gmx.de", "arcor.de",
            ".org", ".edu", ".gov", ".io",
        ])
        query_completer = QCompleter(query_suggestions, self._query)
        query_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._query.setCompleter(query_completer)
        q_row.addWidget(self._query)
        self._extract_btn = QPushButton("Extract")
        self._extract_btn.setObjectName("start_btn")
        self._extract_btn.setFixedWidth(100)
        self._extract_btn.clicked.connect(self._on_extract)
        q_row.addWidget(self._extract_btn)
        root.addLayout(q_row)

        self._emails_only_cb = QCheckBox("Email-only output")
        self._emails_only_cb.setToolTip(
            "Write only the matching email addresses instead of full entries."
        )
        root.addWidget(self._emails_only_cb)

        # Split many domains at once
        split_row = QHBoxLayout()
        split_row.addWidget(QLabel("Split (multi):"))
        self._split_input = QLineEdit()
        self._split_input.setPlaceholderText("space or comma separated — e.g.  @gmail.com  .de  yahoo.com")
        self._split_input.returnPressed.connect(self._on_split)
        split_row.addWidget(self._split_input)
        self._split_btn = QPushButton("Split")
        self._split_btn.setObjectName("start_btn")
        self._split_btn.setFixedWidth(100)
        self._split_btn.clicked.connect(self._on_split)
        split_row.addWidget(self._split_btn)
        root.addLayout(split_row)

        self._progress = QProgressBar()
        self._progress.setFormat("Idle")
        root.addWidget(self._progress)

        info_panel = QHBoxLayout()
        self._line_progress_lbl = QLabel("Lines scanned: 0")
        self._line_progress_lbl.setObjectName("hint_lbl")
        info_panel.addWidget(self._line_progress_lbl, stretch=2)
        self._eta_lbl = QLabel("ETA: --")
        self._eta_lbl.setObjectName("hint_lbl")
        info_panel.addWidget(self._eta_lbl)
        root.addLayout(info_panel)

        self._result_lbl = QLabel("")
        self._result_lbl.setWordWrap(True)
        self._result_lbl.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        root.addWidget(self._result_lbl)

        result_btn_row = QHBoxLayout()
        self._view_output_btn = QPushButton("View Output Folder")
        self._view_output_btn.setObjectName("view_output_btn")
        self._view_output_btn.setFixedWidth(140)
        self._view_output_btn.clicked.connect(self._on_view_output)
        result_btn_row.addWidget(self._view_output_btn)
        result_btn_row.addStretch()
        root.addLayout(result_btn_row)

        root.addWidget(QLabel("Log:"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 11))
        root.addWidget(self._log, stretch=1)

    def _log_line(self, text: str) -> None:
        self._log.append(_log_html(text))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_view_output(self) -> None:
        mode = (self._mode_combo.currentText() or "combo").strip().lower()
        emails_only = self._emails_only_cb.isChecked()
        folder = output_dir(mode, emails_only)
        main_window = self.window()
        if main_window and hasattr(main_window, "_open_directory"):
            main_window._open_directory(folder)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    @staticmethod
    def _parse_queries_text(text: str) -> list[str]:
        raw_tokens = re.split(r"[\s,;]+", text.strip())
        cleaned: list[str] = []
        for token in raw_tokens:
            candidate = token.strip().lower()
            if not candidate:
                continue
            if candidate.endswith('.txt'):
                candidate = candidate[:-4]
            if candidate.endswith('.combo'):
                candidate = candidate[:-6]
            if candidate.endswith('.smtp'):
                candidate = candidate[:-5]
            candidate = candidate.lstrip('@.').rstrip('.').strip()
            if not candidate or any(sep in candidate for sep in ('\\', '/', ':')):
                continue
            if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,252}", candidate) is None:
                continue
            if candidate not in cleaned:
                cleaned.append(candidate)
        return cleaned

    def _get_queries_for_split(self) -> list[str]:
        raw_multi = self._split_input.text().strip()
        if raw_multi:
            return self._parse_queries_text(raw_multi)
        # Fallback: reuse Domain/TLD field if split multi is empty
        raw_domain = self._query.text().strip()
        if raw_domain:
            return self._parse_queries_text(raw_domain)
        return []

    def _update_master_info(self) -> None:
        mode = (self._mode_combo.currentText() or "combo").strip().lower()
        p    = master_path(mode)
        size = ""
        if os.path.exists(p):
            mb = os.path.getsize(p) / 1_048_576
            size = f"  ({mb:.1f} MB)"
        self._master_info.setText(f"Master: {p}{size}")

    def _on_browse_source(self) -> None:
        path = QFileDialog.getOpenFileName(self, "Select file to split", "", "Text files (*.txt);;All files (*)")
        if path and path[0]:
            self._custom_file.setText(path[0])

    def _on_extract_type_changed(self) -> None:
        extract_type = self._extract_type_combo.currentText()
        if extract_type == "Email":
            self._query_label.setText("Email:")
            self._query.setPlaceholderText("e.g.  user@gmail.com   or   john   or   @gmail.com")
        else:
            self._query_label.setText("Domain / TLD:")
            self._query.setPlaceholderText("e.g.  de   or   gmail.com   or   .net")
        self._query.clear()

    def _on_extract(self) -> None:
        query = self._query.text().strip()
        mode  = (self._mode_combo.currentText() or "combo").strip().lower()
        extract_type = self._extract_type_combo.currentText().lower()
        
        if not query:
            error_msg = "[ERROR] Enter a domain/TLD or email first."
            self._log_line(error_msg)
            return

        # Use custom file if provided, otherwise use master
        custom_src = self._custom_file.text().strip()
        if custom_src:
            if not os.path.exists(custom_src):
                self._log_line(f"[ERROR] File not found: {custom_src}")
                return
            source = custom_src
        else:
            source = master_path(mode)
            if not os.path.exists(source):
                self._log_line(f"[ERROR] Master database not found: {source}")
                self._log_line(f"[INFO] Hint: Run a scan first to create the master database.")
                return

        self._extract_btn.setEnabled(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("Extracting…")
        if self._line_progress_lbl:
            self._line_progress_lbl.setText("Lines scanned: 0")
        if self._eta_lbl:
            self._eta_lbl.setText("ETA: calculating…")
        self._ext_start_time = time.monotonic()
        self._result_lbl.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        self._result_lbl.setText("")
        emails_only = self._emails_only_cb.isChecked()
        self._log_line(
            f"Extracting '{query}' (by {extract_type}) from {os.path.basename(source)}"
            + (" as emails only…" if emails_only else "…")
        )

        self._worker = ExtractWorker(query, mode, extract_type, emails_only, source)
        
        # Register worker with main window for cleanup
        main_window = self.window()
        if main_window and hasattr(main_window, 'register_worker'):
            main_window.register_worker(self._worker)
        
        self._worker.progress.connect(self._on_ext_progress)
        self._worker.finished.connect(self._on_ext_finished)
        self._worker.error.connect(self._on_ext_error)
        self._worker.start()

    def _on_split(self) -> None:
        mode    = (self._mode_combo.currentText() or "combo").strip().lower()
        queries = self._get_queries_for_split()
        if not queries:
            self._log_line("[ERROR] Enter one or more domains/TLDs to split.")
            return

        custom_src = self._custom_file.text().strip()
        if custom_src:
            if not os.path.exists(custom_src):
                self._log_line(f"[ERROR] File not found: {custom_src}")
                return
            source = custom_src
        else:
            source = master_path(mode)
            if not os.path.exists(source):
                self._log_line(f"[ERROR] Master database not found: {source}")
                self._log_line(f"[INFO] Hint: Run a scan first to create the master database.")
                return

        self._split_btn.setEnabled(False)
        self._extract_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.setFormat("Splitting…")
        self._result_lbl.setText("")
        self._log_line(f"Splitting {os.path.basename(source)} by: {', '.join(queries)}")

        self._split_worker = SplitWorker(queries, mode, source)
        
        # Register worker with main window for cleanup
        main_window = self.window()
        if main_window and hasattr(main_window, 'register_worker'):
            main_window.register_worker(self._split_worker)
        
        self._split_worker.finished.connect(self._on_split_finished)
        self._split_worker.error.connect(self._on_split_error)
        self._split_worker.stats_updated.connect(self._on_split_stats)
        self._split_worker.log_message.connect(self._log_line)
        self._split_worker.start()

    def _on_split_finished(self, results: dict) -> None:
        self._split_worker = None
        self._render_split_results(results, "Split complete")

    def _on_split_error(self, msg: str) -> None:
        self._split_worker = None
        self._split_btn.setEnabled(True)
        self._extract_btn.setEnabled(True)
        self._progress.setRange(0, 100)
        self._progress.setFormat("Error")
        self._log_line(f"[ERROR] {msg}")

    def _render_split_results(self, results: dict, label: str) -> None:
        self._split_btn.setEnabled(True)
        self._extract_btn.setEnabled(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._progress.setFormat("Done")

        if not results:
            self._result_lbl.setStyleSheet("color: #f38ba8; font-weight: bold;")
            self._result_lbl.setText("No matching entries found.")
            self._log_line("  No matching entries found.")
            return

        total = sum(cnt for _, cnt in results.values())
        self._result_lbl.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        self._result_lbl.setText(f"✓  {label}  ({total:,} lines)")
        for q, (path, count) in results.items():
            if path:
                self._log_line(f"    {q:<25} {count:>8,}  →  {path}")
            else:
                self._log_line(f"    {q:<25}       0   (no matches)")

    def _on_split_stats(self, payload: dict) -> None:
        lines = payload.get("lines", 0)
        matches = payload.get("matches", 0)
        files_created = payload.get("files_created", 0)
        processed_bytes = payload.get("processed_bytes", 0)
        total_bytes = payload.get("total_bytes", 0)
        speed_lpm = payload.get("speed_lpm", 0.0)
        eta_seconds = payload.get("eta_seconds", 0.0)
        elapsed = payload.get("elapsed", 0.0)
        domain_counts = payload.get("domain_counts", [])

        pct = 0
        if total_bytes:
            pct = int(min(max(processed_bytes / total_bytes * 100, 0), 100))
            self._progress.setRange(0, 100)
            self._progress.setValue(pct)
            self._progress.setFormat(f"{pct}%")
        else:
            self._progress.setRange(0, 0)
            self._progress.setFormat(f"Lines: {lines:,}")

        if self._line_progress_lbl:
            self._line_progress_lbl.setText(
                f"Lines scanned: {lines:,}    Matches found: {matches:,}    Files created: {files_created:,}"
            )

        if self._eta_lbl:
            if speed_lpm:
                speed_txt = f"Speed: {speed_lpm:,.0f} lines/min"
            else:
                speed_txt = "Speed: calculating…"
            if eta_seconds:
                mins, secs = divmod(int(max(eta_seconds, 0)), 60)
                eta_txt = f"ETA: {mins:02d}:{secs:02d}"
            else:
                mins, secs = divmod(int(elapsed), 60)
                eta_txt = f"Elapsed: {mins:02d}:{secs:02d}"
            self._eta_lbl.setText(f"{speed_txt}    {eta_txt}")

        if domain_counts:
            sample = domain_counts[:5]
            lines_snippet = [f"{dom:<25} {count:>10,}" for dom, count in sample]
            if lines_snippet:
                self._result_lbl.setStyleSheet("color: #a6e3a1; font-weight: bold;")
                joined = "\n".join(lines_snippet)
                self._result_lbl.setText(f"Live matches:\n{joined}")

    def _on_ext_progress(self, done: int, total: int) -> None:
        display_total = max(total, done)
        pct = int(done / display_total * 100) if display_total else 0
        self._progress.setValue(min(max(pct, 0), 100))
        if display_total:
            self._progress.setFormat(f"{done:,} / {display_total:,}  ({pct}%)")
        else:
            self._progress.setFormat(f"{done:,} lines")

        if self._line_progress_lbl:
            if display_total:
                self._line_progress_lbl.setText(f"Lines scanned: {done:,} / {display_total:,}")
            else:
                self._line_progress_lbl.setText(f"Lines scanned: {done:,}")

        if self._eta_lbl:
            elapsed = max(time.monotonic() - self._ext_start_time, 1e-3)
            if done and display_total and done <= display_total:
                remaining = max(display_total - done, 0)
                rate = done / elapsed if elapsed else 0
                eta_secs = int(remaining / rate) if rate else 0
                mins, secs = divmod(max(eta_secs, 0), 60)
                self._eta_lbl.setText(f"ETA: {mins:02d}:{secs:02d}")
            else:
                self._eta_lbl.setText(f"Elapsed: {elapsed:.1f}s")

    def _on_ext_finished(self, path: str, count: int) -> None:
        self._worker = None
        self._extract_btn.setEnabled(True)
        self._progress.setValue(100)
        self._progress.setFormat("Done")
        if self._eta_lbl:
            elapsed = max(time.monotonic() - self._ext_start_time, 0.0)
            self._eta_lbl.setText(f"Elapsed: {elapsed:.1f}s")
        if self._line_progress_lbl:
            current = self._line_progress_lbl.text()
            if " / " in current:
                self._line_progress_lbl.setText(f"Lines scanned: {count:,} / {count:,}")
            else:
                self._line_progress_lbl.setText(f"Lines scanned: {count:,}")
        self._update_master_info()
        if path:
            self._result_lbl.setStyleSheet("color: #a6e3a1; font-weight: bold;")
            self._result_lbl.setText(f"✓  {count:,} entries extracted  →  {path}")
            self._log_line(f"  Done. {count:,} entries  →  {path}")
        else:
            self._result_lbl.setStyleSheet("color: #f38ba8; font-weight: bold;")
            self._result_lbl.setText("No matching entries found.")
            self._log_line("  No matching entries found.")

    def _on_ext_error(self, msg: str) -> None:
        self._worker = None
        self._extract_btn.setEnabled(True)
        self._progress.setFormat("Error")
        self._result_lbl.setStyleSheet("color: #f38ba8; font-weight: bold;")
        self._log_line(f"[ERROR] {msg}")


# ── Main window ───────────────────────────────────────────────────────────────

class ToolkitGUI(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Dataset Toolkit")
        self.setMinimumSize(860, 640)
        self.resize(1020, 740)
        
        # Track all worker threads for cleanup
        self._all_workers: list = []

        tabs = QTabWidget()
        tabs.addTab(ScannerTab("combo"), "  Scanner  ")
        tabs.addTab(ScannerTab("smtp"),  "  Merge    ")
        tabs.addTab(ExtractorTab(),      "  Domain Extractor  ")
        self.setCentralWidget(tabs)
        self._create_menus()
        
    def register_worker(self, worker) -> None:
        """Register a worker thread for cleanup on exit."""
        if worker not in self._all_workers:
            self._all_workers.append(worker)
    
    def closeEvent(self, event) -> None:
        """Properly cleanup all running threads before closing."""
        # Cancel and wait for all workers
        for worker in self._all_workers:
            if hasattr(worker, 'isRunning') and worker.isRunning():
                if hasattr(worker, 'cancel'):
                    worker.cancel()
                worker.wait(timeout=2000)  # Wait max 2 seconds
        
        event.accept()

    def _create_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        open_scan_action = QAction("Open &Scan Folder...", self)
        open_scan_action.triggered.connect(self._on_open_scan_folder)
        file_menu.addAction(open_scan_action)

        open_output_action = QAction("Open &Output Folder", self)
        open_output_action.triggered.connect(self._on_open_output_folder)
        file_menu.addAction(open_output_action)

        open_reports_action = QAction("Open &Reports Folder", self)
        open_reports_action.triggered.connect(self._on_open_reports_folder)
        file_menu.addAction(open_reports_action)

        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _open_directory(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _current_scanner_tab(self) -> ScannerTab | None:
        current = self.centralWidget().currentWidget()
        return current if isinstance(current, ScannerTab) else None

    def _on_open_scan_folder(self) -> None:
        scanner_tab = self._current_scanner_tab()
        if scanner_tab is None:
            return
        path = QFileDialog.getExistingDirectory(self, "Select folder to scan")
        if path:
            scanner_tab._folder.setText(path)

    def _on_open_output_folder(self) -> None:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        if os.path.exists(out_dir):
            self._open_directory(out_dir)
        else:
            QMessageBox.information(self, "Output Folder", "No output folder found. Run an extraction first.")

    def _on_open_reports_folder(self) -> None:
        reports = reports_dir()
        self._open_directory(reports)

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About Dataset Toolkit",
            "<b>Dataset Toolkit</b><br>"
            "Scan combo/smtp datasets, deduplicate against masters, and extract domains.<br><br>"
            "Improved GUI, faster workflow, and direct access to output folders."
        )


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    if _HAS_QDARKSTYLE and qdarkstyle is not None:
        try:
            app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api='pyside6'))
        except Exception:
            app.setStyleSheet(STYLESHEET)
    else:
        app.setStyleSheet(STYLESHEET)
    w = ToolkitGUI()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
