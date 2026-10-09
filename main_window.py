"""
main_window.py
----------------
The UI shell. Ported behaviors from Zoom_Ai_V54.py's ZoomControlApp:
CSV loader (same encoding fallback chain, same Prism-column validation),
Add Session dialog (same fields, same Zoom/Prism tabs), Auto Pilot's
time-parsing (same ISO + 12h fallback chain, same UTC-offset handling),
Retry/Clear All — all business logic, unchanged.

What's actually different from the Tkinter version, deliberately:
  - Table is QTableView + SessionTableModel (models.py), not a Treeview
    with hand-indexed value tuples. Clicking Action/Retry is done by
    column IDENTITY (COL_INDEX["action"]), not "column #12".
  - Log lines use the same tally-dot signature as the grid, via a tiny
    HTML span instead of Tkinter tag-based coloring.
  - Auto Pilot's scan loop is a QTimer instead of a recursive
    `root.after` callback chain — same 10s cadence, clearer control flow.
"""

import sys
import os
import re
import asyncio
import threading
from datetime import datetime, timedelta
import time as _time

import pandas as pd

from PySide6.QtCore import Qt, QTimer, QModelIndex, QUrl, QDate, QMetaObject, Q_ARG, Slot, Signal, QObject, QAbstractTableModel, QThread
from PySide6.QtGui import QFont, QColor, QIcon, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableView, QHeaderView, QFileDialog, QDialog, QLineEdit,
    QFormLayout, QTabWidget, QRadioButton, QButtonGroup, QPlainTextEdit,
    QAbstractItemView, QFrame, QDialogButtonBox, QGraphicsOpacityEffect,
    QApplication, QComboBox, QDateEdit, QProgressBar, QStackedLayout,
)

import theme
from models import SessionModel, SessionTableModel, COL_INDEX
from engine import Engine, DEFAULT_POLL_NAME, AUTO_POLL_END_MINUTES
from prism_data_pull import fetch_sessions_async
from paths import resource_path
from hard_exit import hard_exit
from applog import log_to_file, LOG_DIR
from app_settings import get_settings
from gsheets_api_client import GSheetsAPIClient, GSheetsError
from datetime import date

VALID_PRISM_TYPES = ("session", "workshop-session", "session-group-session")
MAX_LOG_LINES = 500
PRISM_PENDING_PREFIX = "prism-pending:"

# Worker class for thread-safe communication with signals
class ModeratorWorker(QObject):
    moderators_loaded = Signal(list)
    error = Signal(str)
    
    def __init__(self, client):
        super().__init__()
        self.client = client
    
    def run(self):
        try:
            mods = self.client.fetch_moderators(date.today())
            self.moderators_loaded.emit(mods)
        except Exception as e:
            self.error.emit(str(e))

class SessionFetchWorker(QObject):
    sessions_fetched = Signal(list)
    error = Signal(str, str)
    
    def __init__(self, client, moderator, target_date):
        super().__init__()
        self.client = client
        self.moderator = moderator
        self.target_date = target_date
    
    def run(self):
        try:
            sessions = self.client.fetch_sessions(self.moderator, self.target_date)
            self.sessions_fetched.emit(sessions)
        except GSheetsError as e:
            self.error.emit(e.code, e.message)
        except Exception as e:
            self.error.emit("EXCEPTION", str(e))


def _log_colors():
    """Read live — NOT a module-level dict — so a theme toggle is
    reflected in every Event Log line logged afterward, not just ones
    logged before the switch."""
    return {"info": theme.MUTED, "ok": theme.GREEN, "warn": theme.AMBER, "err": theme.RED}


def _app_icon() -> QIcon:
    """Shared by MainWindow and both dialog classes so every window in
    the app — not just the main one — carries the same icon. Falls back
    to an empty QIcon (Qt's default) if the file isn't there yet rather
    than crashing the app over a missing icon."""
    path = resource_path("assets/icon.ico")
    return QIcon(path) if os.path.exists(path) else QIcon()


# ─────────────────────────────────────────────────────────────────────────
# Styled confirm/notify dialogs — replace QMessageBox entirely
# ─────────────────────────────────────────────────────────────────────────
# Native QMessageBox pulls its "?" / warning icon and part of its content
# chrome from the OS theme — QSS can't fully reach it, which is exactly
# what produced the mismatched-shade popup. These two dialogs are built
# entirely from our own styled widgets instead, so a popup is guaranteed
# to look like the rest of the app. (The outer OS window titlebar/border
# is unavoidably native — that's the window manager's chrome, not Qt's —
# but everything inside it is ours.)
class _StyledDialog(QDialog):
    def __init__(self, parent, title, message, buttons, accent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(_app_icon())
        self.setMinimumWidth(380)
        self.setStyleSheet(theme.STYLESHEET)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(14)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {theme.INK};")
        v.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"font-size: 11px; color: {theme.MUTED};")
        v.addWidget(msg_lbl)

        row = QHBoxLayout()
        row.addStretch(1)
        self._result = False
        for label, is_primary, value in buttons:
            b = QPushButton(label)
            b.setObjectName("ToolBtn")
            b.setCursor(Qt.PointingHandCursor)
            if is_primary:
                color = accent or theme.RED
                b.setStyleSheet(
                    f"QPushButton#ToolBtn {{ background: {color}; border: none; color: white; "
                    f"padding: 6px 18px; font-weight: 700; }}"
                    f"QPushButton#ToolBtn:hover {{ background: {color}; }}"
                )
            b.clicked.connect(lambda _=False, v=value: self._choose(v))
            row.addWidget(b)
        v.addLayout(row)

    def _choose(self, value):
        self._result = value
        self.accept()


def confirm(parent, title: str, message: str, danger: bool = True) -> bool:
    """Yes/No confirmation. `danger=True` colors the primary action red
    (destructive: Clear All, End Session); False uses the brand color."""
    dlg = _StyledDialog(parent, title, message,
                         buttons=[("No", False, False), ("Yes", True, True)],
                         accent=theme.RED if danger else theme.GREEN)
    dlg.exec()
    return dlg._result


def notify(parent, title: str, message: str):
    """Single-button info/warning popup."""
    dlg = _StyledDialog(parent, title, message, buttons=[("OK", True, True)])
    dlg.exec()


def _fmt_mb(n: float) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


def _fmt_eta(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"about {max(seconds, 1)} s left"
    minutes, secs = divmod(seconds, 60)
    return f"about {minutes} min {secs:02d} s left"


class UpdateProgressDialog(QDialog):
    """Shown while an app update downloads (see updater.py). Same look as
    _StyledDialog, but it has to stay alive and be updated while the
    download runs, which _StyledDialog's build-once layout can't do.

    Closing it any way other than finish() — the Cancel button, the window's
    X, Esc — counts as cancelling the download."""

    cancel_requested = Signal()

    def __init__(self, parent, new_version: str):
        super().__init__(parent)
        self._finished = False
        self.setWindowTitle("Downloading Update")
        self.setWindowIcon(_app_icon())
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setStyleSheet(theme.STYLESHEET)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)

        title_lbl = QLabel(f"Downloading v{new_version}")
        title_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {theme.INK};")
        v.addWidget(title_lbl)

        msg_lbl = QLabel("The app will restart automatically when the download finishes.")
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"font-size: 11px; color: {theme.MUTED};")
        v.addWidget(msg_lbl)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        v.addWidget(self.bar)

        self.detail = QLabel("Starting download...")
        self.detail.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: 11px; color: {theme.MUTED};")
        v.addWidget(self.detail)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("ToolBtn")
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        v.addLayout(row)

    @Slot(object, object, float)
    def set_progress(self, done, total, speed):
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(min(1000, int(done * 1000 / total)))
            text = f"{_fmt_mb(done)} of {_fmt_mb(total)}  ·  {int(done * 100 / total)}%"
            if speed > 0:
                text += f"  ·  {speed / (1024 * 1024):.1f} MB/s"
                if done < total:
                    text += f"  ·  {_fmt_eta((total - done) / speed)}"
        else:
            self.bar.setRange(0, 0)   # size unknown: indeterminate
            text = f"{_fmt_mb(done)} downloaded"
            if speed > 0:
                text += f"  ·  {speed / (1024 * 1024):.1f} MB/s"
        self.detail.setText(text)

    def finish(self):
        """Close without it counting as a cancel."""
        self._finished = True
        self.accept()

    def reject(self):
        if not self._finished:
            self._finished = True
            self.detail.setText("Cancelling...")
            self.cancel_requested.emit()
        super().reject()


# ─────────────────────────────────────────────────────────────────────────
# Add Session dialog
# ─────────────────────────────────────────────────────────────────────────
class AddSessionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Session")
        self.setWindowIcon(_app_icon())
        self.setMinimumWidth(420)
        self.result_model: SessionModel | None = None

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # ── Zoom tab ──
        zoom_tab = QWidget()
        zf = QFormLayout(zoom_tab)
        self.z_program  = QLineEdit("Quick Add")
        self.z_topic    = QLineEdit("New Session")
        self.z_mod      = QLineEdit("Monitor")
        self.z_start    = QLineEdit("02:00 PM")
        self.z_end      = QLineEdit()
        self.z_code     = QLineEdit()
        self.z_link     = QLineEdit()
        self.z_email    = QLineEdit()
        self.z_password = QLineEdit()
        self.z_password.setEchoMode(QLineEdit.Password)
        for lbl, w in [("Program Name", self.z_program), ("Session Topic", self.z_topic),
                       ("Moderator Name", self.z_mod), ("Start Time", self.z_start),
                       ("End Time", self.z_end), ("Meeting Code", self.z_code),
                       ("Meeting Link", self.z_link), ("Host Email", self.z_email),
                       ("Host Password", self.z_password)]:
            zf.addRow(lbl, w)
        self.tabs.addTab(zoom_tab, "ZOOM")

        # ── Prism tab ──
        prism_tab = QWidget()
        pf = QFormLayout(prism_tab)
        self.p_program = QLineEdit("Quick Add")
        self.p_topic   = QLineEdit("New Session")
        self.p_mod     = QLineEdit("Monitor")
        self.p_start   = QLineEdit("02:00 PM")
        self.p_end     = QLineEdit()
        self.p_code    = QLineEdit()
        self.p_link    = QLineEdit()
        for lbl, w in [("Program Name", self.p_program), ("Session Topic", self.p_topic),
                       ("Moderator Name", self.p_mod), ("Start Time", self.p_start),
                       ("End Time", self.p_end), ("Meeting Code", self.p_code),
                       ("Meeting Link", self.p_link)]:
            pf.addRow(lbl, w)

        self.p_type_group = QButtonGroup(prism_tab)
        type_row = QHBoxLayout()
        for i, val in enumerate(VALID_PRISM_TYPES):
            rb = QRadioButton(val)
            if i == 0: rb.setChecked(True)
            self.p_type_group.addButton(rb)
            self.p_type_group.setId(rb, i)
            type_row.addWidget(rb)
        pf.addRow("Prism Type", type_row)

        self.p_session_id = QLineEdit()
        self.p_group_id   = QLineEdit()
        pf.addRow("Session ID", self.p_session_id)
        pf.addRow("Session Group ID", self.p_group_id)
        self.tabs.addTab(prism_tab, "PRISM")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self):
        is_prism = self.tabs.currentIndex() == 1
        if is_prism:
            link = self.p_link.text().strip()
        else:
            link = self.z_link.text().strip()
        if len(link) < 5:
            notify(self, "Missing Link", "Meeting Link is required.")
            return

        if is_prism:
            checked_id = self.p_type_group.checkedId()
            ptype = VALID_PRISM_TYPES[checked_id] if checked_id >= 0 else ""
            self.result_model = SessionModel(
                session_type="PRISM", join_url=link,
                program_name=self.p_program.text(), session_name=self.p_topic.text(),
                moderator_name=self.p_mod.text(), start_time=self.p_start.text(),
                end_time=self.p_end.text(), meeting_code=self.p_code.text(),
                prism_type=ptype,
                prism_session_id=self.p_session_id.text().strip(),
                prism_session_group_id=self.p_group_id.text().strip(),
            )
        else:
            self.result_model = SessionModel(
                session_type="ZOOM", join_url=link,
                program_name=self.z_program.text(), session_name=self.z_topic.text(),
                moderator_name=self.z_mod.text(), start_time=self.z_start.text(),
                end_time=self.z_end.text(), meeting_code=self.z_code.text(),
                host_email=self.z_email.text(), host_password=self.z_password.text(),
            )
        self.accept()


# ─────────────────────────────────────────────────────────────────────────
# Google Sheets Sync Dialog
# ─────────────────────────────────────────────────────────────────────────

class GSheetsPreviewModel(QAbstractTableModel):
    """Table model for preview dialog - editable, highlights missing required fields."""
    
    COLUMNS = [
        ('Type', 'Type'),
        ('Meeting Link', 'Meeting Link'),
        ('Program Name', 'Program Name'),
        ('Session Topic / Name', 'Topic'),
        ('Moderator Name', 'Moderator'),
        ('Session Start Time', 'Start Time'),
        ('Session End Time', 'End Time'),
        ('Meeting Code', 'Meeting Code'),
        ('Host Email', 'Host Email'),
        ('Host Password', 'Host Password'),
        ('Prism Type', 'Prism Type'),
        ('Prism Session ID', 'Prism Session ID'),
        ('Prism Session Group ID', 'Prism Group ID'),
    ]
    
    REQUIRED_KEYS = {'Type', 'Meeting Link'}
    ZOOM_REQUIRED = {'Host Email', 'Host Password'}
    PRISM_REQUIRED = {'Prism Type', 'Prism Session ID'}
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: list[dict] = []
    
    def set_sessions(self, sessions: list[dict]):
        self.beginResetModel()
        self._sessions = sessions
        self.endResetModel()
    
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._sessions)
    
    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)
    
    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section][1].upper()
        return None
    
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._sessions[index.row()]
        key = self.COLUMNS[index.column()][0]
        
        if role == Qt.DisplayRole or role == Qt.EditRole:
            return row.get(key, '')
        
        # Highlight missing required fields
        if role == Qt.BackgroundRole:
            val = row.get(key, '')
            if key in self.REQUIRED_KEYS and not val:
                return QColor(theme.RED + "30")
            if row.get('Type') == 'ZOOM' and key in self.ZOOM_REQUIRED and not val:
                return QColor(theme.AMBER + "30")
            if row.get('Type') == 'PRISM' and key in self.PRISM_REQUIRED and not val:
                return QColor(theme.AMBER + "30")
        
        return None
    
    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
    
    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or role != Qt.EditRole:
            return False
        row = self._sessions[index.row()]
        key = self.COLUMNS[index.column()][0]
        row[key] = str(value)
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.BackgroundRole])
        return True


class GSheetsSyncDialog(QDialog):
    """Dialog to fetch, preview, and load sessions from Google Sheets."""
    
    def __init__(self, parent, apps_script_url: str):
        super().__init__(parent)
        self.parent_window = parent
        self.client = GSheetsAPIClient(apps_script_url)
        self.fetched_sessions: list[dict] = []
        
        self.setWindowTitle("Sync from Google Sheets")
        self.setWindowIcon(_app_icon())
        self.setMinimumSize(900, 600)
        self.setStyleSheet(theme.STYLESHEET)
        
        self._build_ui()
        self._load_moderators()
    
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Header
        header = QLabel("Sync Sessions from Google Sheets")
        header.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {theme.INK};")
        layout.addWidget(header)
        
        # Moderator + Date row
        controls = QHBoxLayout()
        controls.setSpacing(12)
        
        controls.addWidget(QLabel("Moderator:"))
        self.moderator_combo = QComboBox()
        self.moderator_combo.setEditable(True)
        self.moderator_combo.setMinimumWidth(250)
        controls.addWidget(self.moderator_combo)
        
        controls.addWidget(QLabel("Date:"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setMinimumWidth(140)
        controls.addWidget(self.date_edit)
        
        self.fetch_btn = QPushButton("Fetch Sessions")
        self.fetch_btn.setObjectName("ToolBtn")
        self.fetch_btn.setCursor(Qt.PointingHandCursor)
        self.fetch_btn.clicked.connect(self._on_fetch)
        controls.addWidget(self.fetch_btn)
        
        controls.addStretch()
        layout.addLayout(controls)
        
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # Indeterminate
        self.progress.setVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        
        # Preview table
        self.preview_table = QTableView()
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_model = GSheetsPreviewModel()
        self.preview_table.setModel(self.preview_model)
        layout.addWidget(self.preview_table, stretch=1)
        
        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        layout.addWidget(self.status_label)
        
        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("ToolBtn")
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)
        
        self.load_btn = QPushButton("Load Sessions")
        self.load_btn.setObjectName("ToolBtn")
        self.load_btn.setCursor(Qt.PointingHandCursor)
        self.load_btn.setEnabled(False)
        self.load_btn.clicked.connect(self._on_load)
        btn_row.addWidget(self.load_btn)
        
        layout.addLayout(btn_row)
    
    def _load_moderators(self):
        """Fetch moderator list for today asynchronously."""
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Loading moderators...")
        self.status_label.setText("Fetching moderator list...")
        
        # Clean up any existing worker
        if hasattr(self, '_moderator_worker') and self._moderator_worker is not None:
            self._moderator_worker.deleteLater()
        if hasattr(self, '_moderator_thread') and self._moderator_thread is not None:
            self._moderator_thread.quit()
            self._moderator_thread.wait()
            
        # Create worker and thread
        self._moderator_worker = ModeratorWorker(self.client)
        self._moderator_thread = QThread()
        self._moderator_worker.moveToThread(self._moderator_thread)
        
        # Connect signals
        self._moderator_worker.moderators_loaded.connect(self._on_moderators_loaded)
        self._moderator_worker.error.connect(self._on_moderators_error)
        self._moderator_thread.started.connect(self._moderator_worker.run)
        self._moderator_worker.moderators_loaded.connect(self._moderator_thread.quit)
        self._moderator_worker.error.connect(self._moderator_thread.quit)
        self._moderator_worker.moderators_loaded.connect(self._moderator_worker.deleteLater)
        self._moderator_worker.error.connect(self._moderator_worker.deleteLater)
        self._moderator_thread.finished.connect(self._moderator_thread.deleteLater)
        
        # Start the thread
        self._moderator_thread.start()
    
    @Slot(list)
    def _on_moderators_loaded(self, mods: list):
        self.moderator_combo.clear()
        self.moderator_combo.addItems(mods)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch Sessions")
        self.status_label.setText(f"Found {len(mods)} moderators for today")
    
    @Slot(str)
    def _on_moderators_error(self, error: str):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch Sessions")
        self.status_label.setText(f"Error loading moderators: {error}")
        # Allow manual entry anyway
    
    def _on_fetch(self):
        moderator = self.moderator_combo.currentText().strip()
        if not moderator:
            notify(self, "Missing Moderator", "Please select or enter a moderator name.")
            return
        
        target_date = self.date_edit.date().toPython()
        
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching...")
        self.progress.setVisible(True)
        self.status_label.setText(f"Fetching sessions for {moderator} on {target_date}...")
        self.load_btn.setEnabled(False)
        
        # Clean up any existing worker
        if hasattr(self, '_fetch_worker') and self._fetch_worker is not None:
            self._fetch_worker.deleteLater()
        if hasattr(self, '_fetch_thread') and self._fetch_thread is not None:
            self._fetch_thread.quit()
            self._fetch_thread.wait()
            
        # Create worker and thread
        self._fetch_worker = SessionFetchWorker(self.client, moderator, target_date)
        self._fetch_thread = QThread()
        self._fetch_worker.moveToThread(self._fetch_thread)
        
        # Connect signals
        self._fetch_worker.sessions_fetched.connect(self._on_fetch_complete)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.sessions_fetched.connect(self._fetch_thread.quit)
        self._fetch_worker.error.connect(self._fetch_thread.quit)
        self._fetch_worker.sessions_fetched.connect(self._fetch_worker.deleteLater)
        self._fetch_worker.error.connect(self._fetch_worker.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        
        # Start the thread
        self._fetch_thread.start()
    
    @Slot(list)
    def _on_fetch_complete(self, sessions: list):
        self.fetched_sessions = sessions
        self.preview_model.set_sessions(sessions)
        self.progress.setVisible(False)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch Sessions")
        self.load_btn.setEnabled(len(sessions) > 0)
        self.status_label.setText(f"Loaded {len(sessions)} sessions — review and click Load Sessions")
    
    @Slot(str, str)
    def _on_fetch_error(self, code: str, message: str):
        self.progress.setVisible(False)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Fetch Sessions")
        self.load_btn.setEnabled(False)
        
        if code == "NO_SESSIONS":
            self.status_label.setText(message)
            notify(self, "No Sessions", message)
        else:
            self.status_label.setText(f"Error: {message}")
            notify(self, "Fetch Failed", f"[{code}] {message}")
    
    def _on_load(self):
        """Load sessions into parent's SessionTableModel."""
        if not self.fetched_sessions:
            return
        
        model = self.parent_window.table_model
        
        added = 0
        updated = 0
        
        for session in self.fetched_sessions:
            url = session.get('Meeting Link', '').strip()
            is_prism = session.get('Type') == 'PRISM'
            
            # For PRISM, use placeholder key if no link
            if is_prism and not url:
                psid = session.get('Prism Session ID', '').strip()
                url = f"prism-pending:{psid}" if psid else f"prism-pending:{session.get('Session Topic / Name', '').strip()}"
            
            if not url:
                continue
            
            existing = model.get(url)
            
            kw = {
                'session_type': session.get('Type', ''),
                'join_url': url,
                'program_name': session.get('Program Name', ''),
                'session_name': session.get('Session Topic / Name', ''),
                'moderator_name': session.get('Moderator Name', ''),
                'start_time': session.get('Session Start Time', ''),
                'end_time': session.get('Session End Time', ''),
                'meeting_code': session.get('Meeting Code', ''),
                'host_email': session.get('Host Email', ''),
                'host_password': session.get('Host Password', ''),
            }
            
            if is_prism:
                kw['prism_type'] = session.get('Prism Type', '')
                kw['prism_session_id'] = session.get('Prism Session ID', '')
                kw['prism_session_group_id'] = session.get('Prism Session Group ID', '')
            
            if existing:
                model.set_fields(url, **kw)
                updated += 1
            else:
                from models import SessionModel
                new_model = SessionModel(**kw)
                if model.add_session(new_model):
                    added += 1
        
        self.parent_window.log_event(
            f"Google Sheets sync: {added} added, {updated} updated", "ok"
        )
        self.accept()


# ─────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from updater import CURRENT_VERSION
        self.setWindowTitle(f"Command Center — Zoom + Prism  (v{CURRENT_VERSION})")
        self._icon_buttons = []  # (button, icon_name, color_fn, size) — rebuilt on theme toggle, see _apply_theme()
        self._init_theme()
        self.setWindowIcon(_app_icon())
        self._restore_window_geometry()

        self.table_model = SessionTableModel()
        self.engine = Engine(self.table_model, self.log_event)
        self.auto_pilot_running = False
        self._auto_pilot_timer = QTimer(self)
        self._auto_pilot_timer.setInterval(10_000)
        self._auto_pilot_timer.timeout.connect(self._run_auto_pilot_check)

        self._build_ui()

        self.table_model.dataChanged.connect(lambda *a: self._refresh_counts())
        self.table_model.dataChanged.connect(self._on_poll_select_changed)
        self.table_model.rowsInserted.connect(lambda *a: self._refresh_counts())
        self.table_model.modelReset.connect(self._refresh_counts)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()
        self._refresh_counts()
# Check if Google Sheets URL is configured
        settings = get_settings()
        url = settings.value('gsheets/apps_script_url', '')
        if not url:
            from prism_config import GSHEETS_APPS_SCRIPT_URL
            url = GSHEETS_APPS_SCRIPT_URL
        self.btn_gsheets.setEnabled(bool(url))

    # ── Theme (light/dark) ─────────────────────────────────────────
    def _init_theme(self):
        """Explicit user choice (if they've ever toggled) always wins.
        Otherwise, detect the OS preference — this only happens once:
        the very first launch, before any explicit choice exists. Every
        launch after that uses whatever was last decided, regardless of
        what the OS is doing, per the actual request this was built
        for ('remember what I decided, don't keep re-detecting')."""
        saved = get_settings().value("theme/mode", None)
        mode = saved if saved in ("light", "dark") else theme.detect_system_theme()
        theme.set_theme(mode)

    def _apply_theme(self):
        """Re-applies the current palette to everything that doesn't
        update itself automatically from theme.py's live module
        globals: the QSS stylesheet (Qt only re-parses it when
        setStyleSheet() is called again, it doesn't poll), every baked
        toolbar/log-panel icon (QIcon pixmaps are rendered once at
        creation time, not re-rendered on palette change), and the
        table's tally dots (repainted on-demand, not automatically)."""
        self.setStyleSheet(theme.STYLESHEET)
        for button, icon_name, color_fn, size in self._icon_buttons:
            button.setIcon(theme.icon(icon_name, color_fn(), size))
        self.table.viewport().update()
        self._update_theme_toggle_button()

    def _on_toggle_theme(self):
        new_mode = "light" if theme.CURRENT_THEME == "dark" else "dark"
        theme.set_theme(new_mode)
        get_settings().setValue("theme/mode", new_mode)
        self._apply_theme()
        self.log_event(f"Switched to {new_mode} theme.", "info")

    def _update_theme_toggle_button(self):
        if theme.CURRENT_THEME == "dark":
            self.btn_theme.setIcon(theme.icon("sun", theme.INK, 16))
            self.btn_theme.setToolTip("Switch to light theme")
        else:
            self.btn_theme.setIcon(theme.icon("moon", theme.INK, 16))
            self.btn_theme.setToolTip("Switch to dark theme")

    # ── Window geometry persistence ─────────────────────────────────
    def _restore_window_geometry(self):
        """saveGeometry()/restoreGeometry() is Qt's own recommended
        pattern for exactly this — one QByteArray blob captures size,
        position, AND maximized/fullscreen state together, so restoring
        it puts the window back exactly as it was left, maximized or
        not. Falls back to the previous fixed default on first-ever run
        (no saved value yet)."""
        geometry = get_settings().value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1920, 980)

    def _save_window_geometry(self):
        get_settings().setValue("window/geometry", self.saveGeometry())

    # ── Shutdown ─────────────────────────────────────────────────────
    def closeEvent(self, event):
        """Without this override, closing the window just quits Qt —
        engine.shutdown() (which cancels the 3 background heartbeat
        loops and, critically, closes the Playwright browser process)
        was written but never actually called from anywhere. The browser
        subprocess doesn't belong to Qt, so Qt quitting doesn't touch it
        — it's left running, orphaned, which is what shows up as still
        active. This is the fix: explicitly confirm (if sessions are
        live), then guarantee shutdown happens before the process exits."""
        self._save_window_geometry()
        total, live = self.table_model.summary()
        if live:
            if not confirm(
                self, "Sessions Are Live",
                f"{live} session(s) are currently LIVE. Closing Command Center "
                f"will end them.\n\nClose anyway?", danger=True,
            ):
                event.ignore()
                return

        event.accept()  # window disappears immediately — cleanup below doesn't block that
        self._auto_pilot_timer.stop()
        # Ordering matters here, and it's the actual root cause of the
        # taskbar-zombie-process bug: this safety-net thread must be
        # STARTED FIRST, before any async shutdown work begins.
        #
        # It's a plain threading.Thread, not a QTimer — that distinction
        # also matters. A QTimer.singleShot only fires while Qt's event
        # loop is actively pumping events to dispatch it. If
        # engine.shutdown() and QApplication.quit() both complete
        # quickly (a common case, not just the hung-CDP edge case), the
        # event loop stops well before an 8s QTimer would ever elapse —
        # so it never gets a chance to fire. If the Python interpreter
        # then hangs during its OWN teardown afterward (e.g. a
        # non-daemon thread owned by Playwright's driver refusing to
        # join), there is no event loop left at all to dispatch that
        # timer, so it silently never rescues that hang — which is
        # exactly the "still running in Task Manager" symptom, even
        # with the ordering fix in place. A daemon thread sleeping on
        # real wall-clock time has no such dependency: it fires
        # regardless of whether Qt's loop is still running, whether
        # quit() succeeded, or what the rest of the process is doing.
        #
        # 8s budget = the 5s graceful-shutdown timeout below + buffer
        # for quit() itself and normal interpreter teardown.
        #
        # hard_exit(), not os._exit(): os._exit crashed pyside6.abi3.dll
        # when it fired while the main thread was still closing the event
        # loop (see hard_exit.py).
        threading.Thread(
            target=lambda: (_time.sleep(8), hard_exit(0)), daemon=True
        ).start()
        asyncio.ensure_future(self._shutdown_and_quit())

    async def _shutdown_and_quit(self):
        try:
            # engine.shutdown() awaits browser.close(), which has no
            # timeout of its own — if the browser's CDP connection is
            # ever unresponsive (a stuck/crashed page), that await could
            # hang forever and the process would never reach quit()
            # below. This is exactly the "still running in Task Manager"
            # symptom, so bound it rather than trust it completes.
            await asyncio.wait_for(self.engine.shutdown(), timeout=5.0)
        except asyncio.TimeoutError:
            print("Engine shutdown timed out after 5s — exiting anyway.")
        except Exception as e:
            print(f"Error during shutdown: {e}")

        try:
            QApplication.instance().quit()
        except Exception as e:
            # If quit() itself throws (unlikely, but possible), don't
            # wait around for the safety-net thread to bail us out — we
            # already know the clean path failed.
            print(f"quit() failed: {e}")
            hard_exit(0)

    # ── UI construction ──────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_toolbar())

        self.table = QTableView()
        self.table.setModel(self.table_model)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        # Qt's default scroll mode for QTableView is ScrollPerItem — each
        # wheel notch jumps a full COLUMN width horizontally (or a full
        # row vertically), which on a table this wide feels like the
        # scroll is "too fast"/uncontrollable rather than a smooth pan.
        # ScrollPerPixel moves by actual pixel amounts instead.
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setSortingEnabled(False)
        self.table.setItemDelegate(theme.TallyDotDelegate(self.table))
        self.table.clicked.connect(self._on_table_clicked)
        self.table.verticalHeader().setDefaultSectionSize(30)
        for key, width in [("session_type", 70), ("program_name", 170),
                            ("session_name", 210), ("moderator_name", 120),
                            ("start_time", 130), ("end_time", 130), ("host_name", 150),
                            ("cohosts", 180), ("participant_count", 100),
                            ("launched_at", 100), ("status", 190),
                            ("action", 130), ("retry", 90),
                            ("poll_status", 110), ("poll_select", 190), ("poll_action", 130),
                            ("prism_type", 150), ("prism_sync", 160),
                            ("session_id", 160), ("meeting_code", 110)]:
            self.table.setColumnWidth(COL_INDEX[key], width)

        # Empty state: an un-styled blank void under the headers gives a
        # first-time (or post-Clear-All) user zero indication of what to
        # do next. QStackedLayout swaps the table for a guidance label
        # when there are 0 rows — no manual geometry/resize tracking
        # needed, unlike a raw overlay widget. Toggled from
        # _refresh_counts(), the existing single hook this app already
        # calls after every add/remove/load/clear.
        self.empty_state_label = QLabel(
            "No sessions loaded\n\nUpload Sessions CSV or sync from Google Sheets to get started")
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")

        table_stack_holder = QWidget()
        self.table_stack = QStackedLayout(table_stack_holder)
        self.table_stack.addWidget(self.table)
        self.table_stack.addWidget(self.empty_state_label)
        root.addWidget(table_stack_holder, stretch=1)

        root.addWidget(self._build_log_panel())
        self.setCentralWidget(central)
        self.setStyleSheet(theme.STYLESHEET)

    def _build_header(self):
        bar = QFrame()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(56)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(10)

        dot = QFrame()
        dot.setObjectName("WordmarkDot")
        dot.setFixedSize(8, 8)
        lay.addWidget(dot)

        wordmark = QLabel("COMMAND CENTER")
        wordmark.setObjectName("Wordmark")
        lay.addWidget(wordmark)

        sub = QLabel("ZOOM + PRISM")
        sub.setObjectName("WordmarkSub")
        lay.addWidget(sub)

        pill = QLabel("LIVE CONTROL")
        pill.setObjectName("Pill")
        lay.addWidget(pill)

        lay.addStretch(1)

        self.scan_label = QLabel("Auto-Pilot idle")
        self.scan_label.setObjectName("ScanLabel")
        lay.addWidget(self.scan_label)

        self.clock_label = QLabel("")
        self.clock_label.setObjectName("ClockLabel")
        lay.addWidget(self.clock_label)

        self.btn_theme = QPushButton()
        self.btn_theme.setObjectName("ThemeToggleBtn")
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        self.btn_theme.setFixedSize(30, 30)
        self.btn_theme.clicked.connect(self._on_toggle_theme)
        self._update_theme_toggle_button()
        lay.addWidget(self.btn_theme)

        self.btn_auto = QPushButton("▶  START AUTO-PILOT")
        self.btn_auto.setObjectName("AutoPilotBtn")
        self.btn_auto.setProperty("running", "false")
        self.btn_auto.setCursor(Qt.PointingHandCursor)
        self.btn_auto.clicked.connect(self._on_toggle_auto_pilot)
        self.btn_auto.setEnabled(False)
        lay.addWidget(self.btn_auto)

        return bar

    def _build_toolbar(self):
        bar = QFrame()
        bar.setObjectName("ToolBar")
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(10)

        def tool_btn(icon_name, text, slot, tooltip=None):
            # Short labels so the toolbar fits without hard-clipping text
            # (confirmed live: "Sync from Gooc", "Launch Poll (Se" —
            # QPushButton doesn't auto-elide, it just cuts off raw when
            # squeezed). The full description still lives in the
            # tooltip, so nothing is lost, just not crammed into the
            # visible label.
            b = QPushButton(theme.icon(icon_name, theme.INK), " " + text)
            b.setObjectName("ToolBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tooltip or text)
            b.clicked.connect(slot)
            lay.addWidget(b)
            self._icon_buttons.append((b, icon_name, lambda: theme.INK, 18))
            return b

        tool_btn("upload", "Upload CSV", self._on_upload_csv, tooltip="Upload Sessions CSV")
        tool_btn("add", "Add Session", self._on_add_session)
        self.btn_gsheets = tool_btn("refresh", "Google Sheets", self._on_sync_gsheets,
                                     tooltip="Sync from Google Sheets")
        self.btn_refresh = tool_btn("refresh", "Refresh", self._on_refresh_participants,
                                     tooltip="Refresh Participants")
        self.btn_scraping_toggle = tool_btn(
            "clock", "Pause Scraping", self._on_toggle_participant_scraping,
            tooltip="Pause Live Scraping")
        tool_btn("chevron", "Select Live", self._on_select_all_live, tooltip="Select All Live")
        self.btn_poll_selected = tool_btn(
            "play", "Launch Poll", self._on_launch_poll_selected,
            tooltip="Launch Poll (Selected)")
        self.btn_clear = tool_btn("trash", "Clear All", self._on_clear_all)

        lay.addStretch(1)

        self.count_badge = QLabel("0 sessions")
        self.count_badge.setObjectName("CountBadge")
        lay.addWidget(self.count_badge)

        self.live_badge = QLabel("● 0 live")
        self.live_badge.setObjectName("LiveBadge")
        self.live_badge.setProperty("live", "false")
        lay.addWidget(self.live_badge)

        return bar

    def _build_log_panel(self):
        panel = QFrame()
        panel.setObjectName("LogPanel")
        panel.setFixedHeight(220)
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        header = QFrame()
        header.setObjectName("LogHeader")
        header.setFixedHeight(30)
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 0, 10, 0)
        lbl = QLabel("EVENT LOG")
        lbl.setObjectName("LogHeaderLabel")
        h.addWidget(lbl)
        h.addStretch(1)

        btn_open_folder = QPushButton(theme.icon("download", theme.MUTED, 14), "")
        btn_open_folder.setObjectName("IconBtn")
        btn_open_folder.setToolTip(f"Open logs folder ({LOG_DIR})")
        btn_open_folder.clicked.connect(self._on_open_logs_folder)
        h.addWidget(btn_open_folder)
        self._icon_buttons.append((btn_open_folder, "download", lambda: theme.MUTED, 14))

        btn_clear_log = QPushButton(theme.icon("close", theme.MUTED, 14), "")
        btn_clear_log.setObjectName("IconBtn")
        btn_clear_log.setToolTip("Clear this panel — the 30-day file log on disk is unaffected")
        btn_clear_log.clicked.connect(lambda: self.log_box.clear())
        h.addWidget(btn_clear_log)
        self._icon_buttons.append((btn_clear_log, "close", lambda: theme.MUTED, 14))
        v.addWidget(header)

        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("LogText")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(MAX_LOG_LINES)
        v.addWidget(self.log_box, stretch=1)
        return panel

    # ── Clock / counters ─────────────────────────────────────────────
    def _tick_clock(self):
        now = datetime.now()
        self.clock_label.setText(now.strftime("%a %d %b  %H:%M:%S").upper())

    def _refresh_counts(self):
        total, live = self.table_model.summary()
        self.count_badge.setText(f"{total} sessions")
        self.live_badge.setText(f"● {live} live")
        self.live_badge.setProperty("live", "true" if live else "false")
        self.live_badge.style().unpolish(self.live_badge)
        self.live_badge.style().polish(self.live_badge)
        self.btn_auto.setEnabled(total > 0)
        self.table_stack.setCurrentWidget(self.table if total > 0 else self.empty_state_label)

    # ── Event log ────────────────────────────────────────────────────
    def log_event(self, msg: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        color = _log_colors().get(level, theme.MUTED)
        self.log_box.appendHtml(
            f'<span style="color:{theme.MUTED}">[{ts}]</span> '
            f'<span style="color:{color}">●</span> '
            f'<span style="color:{theme.INK}">{_escape(msg)}</span>'
        )
        log_to_file(msg, level)  # persisted separately — see applog.py

    def _on_open_logs_folder(self):
        os.makedirs(LOG_DIR, exist_ok=True)  # in case nothing's been logged yet this run
        QDesktopServices.openUrl(QUrl.fromLocalFile(LOG_DIR))

    # ── CSV loader ───────────────────────────────────────────────────
    def _on_upload_csv(self):
        last_dir = get_settings().value("csv/last_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Upload Sessions CSV", last_dir, "CSV Files (*.csv)")
        if not path:
            return
        get_settings().setValue("csv/last_dir", os.path.dirname(path))
        asyncio.create_task(self._load_csv_async(path))

    async def _load_csv_async(self, path: str):
        try:
            df = None
            for enc in ("utf-8", "cp1252", "latin-1"):
                try:
                    # No encoding_errors="replace" here — that would make
                    # pandas silently substitute bad bytes instead of
                    # raising UnicodeDecodeError, which means the very
                    # first (utf-8) attempt always "succeeds" and cp1252/
                    # latin-1 never get a real chance to run. A CSV
                    # actually saved as cp1252 (e.g. exported from Excel
                    # with accented moderator names) would then get its
                    # special characters silently mangled into ï¿½
                    # instead of properly decoded by the cp1252 pass.
                    df = pd.read_csv(path, encoding=enc, dtype=str)
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                # Last-resort fallback — latin-1 above never actually
                # raises (every byte maps to a codepoint), so in
                # practice this only fires if something more unusual
                # went wrong reading the file at all.
                try:
                    df = pd.read_csv(path, encoding="utf-8", dtype=str, encoding_errors="replace")
                except Exception:
                    pass
            if df is None:
                notify(self, "Error", "Could not decode CSV.")
                return
            df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
            df.fillna("", inplace=True)

            if "Type" not in df.columns:
                notify(self, "Missing Column",
                       "CSV must have a 'Type' column with values ZOOM or PRISM.")
                return

            zoom_count = prism_count = 0
            prism_meta_warnings = 0

            for _, row in df.iterrows():
                stype = str(row.get("Type", "")).strip().upper()
                if stype not in ("ZOOM", "PRISM"):
                    continue
                link = str(row.get("Meeting Link", row.get("Meeting link", ""))).strip()

                kw = dict(
                    session_type=stype, join_url=link,
                    program_name=str(row.get("Program Name", "")),
                    session_name=str(row.get("Session Topic / Name", row.get("Session Topic", ""))),
                    moderator_name=str(row.get("Moderator Name", "")),
                    start_time=str(row.get("Session Start Time", "")),
                    end_time=str(row.get("Session End Time", row.get("Ends At", ""))),
                    meeting_code=str(row.get("Meeting Code", "")),
                    host_email=str(row.get("Host Email", "")),
                    host_password=str(row.get("Host Password", "")),
                )

                if stype == "PRISM":
                    ptype = str(row.get("Prism Type", row.get("Session Type", ""))).strip()
                    psid = str(row.get("Prism Session ID", row.get("Session ID", ""))).strip()
                    pgid = str(row.get("Prism Session Group ID", row.get("Session Group ID", ""))).strip()
                    kw["prism_type"] = ptype
                    kw["prism_session_id"] = psid
                    kw["prism_session_group_id"] = pgid

                    if not link and psid:
                        # No Meeting Link yet. Deliberately NOT fetched now
                        # — Prism only generates the join/start URL ~2
                        # hours before the session starts, so fetching at
                        # upload time (which could be hours or days early)
                        # would just come back empty. Register the row now
                        # under a placeholder key so it's visible in the
                        # grid immediately, and fetch the real link
                        # just-in-time instead — right when Auto Pilot's
                        # launch window opens, or when someone clicks
                        # Launch manually. See _fetch_prism_link().
                        kw["join_url"] = PRISM_PENDING_PREFIX + psid
                        kw["status"] = "⏳ Waiting — No Prism Link Yet"
                    elif not link:
                        continue  # no link AND no Session ID — nothing to do with this row
                    self._validate_prism_meta(kw, ptype, psid, pgid)
                    if ptype not in VALID_PRISM_TYPES or not psid or \
                       (ptype == "session-group-session" and not pgid):
                        prism_meta_warnings += 1
                else:
                    if not link:
                        continue

                model = SessionModel(**kw)
                if self.table_model.add_session(model):
                    if stype == "ZOOM": zoom_count += 1
                    else: prism_count += 1

            parts = []
            if zoom_count: parts.append(f"{zoom_count} Zoom")
            if prism_count: parts.append(f"{prism_count} Prism")
            summary = "Loaded " + ", ".join(parts) if parts else "No sessions loaded."
            if prism_meta_warnings:
                summary += f" — {prism_meta_warnings} Prism row(s) with incomplete metadata"
            self.log_event(f"CSV loaded — {summary}", "warn" if prism_meta_warnings else "ok")

        except Exception as e:
            notify(self, "Error", f"Failed to load CSV:\n{e}")
            self.log_event(f"CSV load failed: {e}", "err")

    # ── Google Sheets Sync ───────────────────────────────────────────────
    def _on_sync_gsheets(self):
        settings = get_settings()
        url = settings.value('gsheets/apps_script_url', '')
        if not url:
            from prism_config import GSHEETS_APPS_SCRIPT_URL
            url = GSHEETS_APPS_SCRIPT_URL
        if not url:
            notify(self, "Missing URL", "Google Apps Script URL not configured. Please set it in settings or check prism_config.py.")
            return
        self.btn_gsheets.setEnabled(True)
        dlg = GSheetsSyncDialog(self, url)
        dlg.exec()

    def _validate_prism_meta(self, kw: dict, ptype: str, psid: str, pgid: str):
        """Metadata completeness warnings — these are about whether we
        have enough to eventually push a status update to Prism, which
        is independent of whether we have the join link yet."""
        name = kw.get("session_name", "")
        if ptype not in VALID_PRISM_TYPES:
            self.log_event(
                f"Prism row '{name}' has missing/unknown Prism Type ({ptype!r}) — launch "
                f"will still work, but the live status-push to Prism will be skipped.", "warn")
        elif not psid:
            self.log_event(
                f"Prism row '{name}' has no Session ID — launch will still work, but the "
                f"live status-push will be skipped.", "warn")
        elif ptype == "session-group-session" and not pgid:
            self.log_event(
                f"Prism row '{name}' is session-group-session but has no Session Group ID — "
                f"status-push will fail until that's filled in.", "warn")

    # ── Just-in-time Prism link fetch ────────────────────────────────
    def _has_real_link(self, m: SessionModel) -> bool:
        return bool(m.join_url) and not m.join_url.startswith(PRISM_PENDING_PREFIX)

    async def _fetch_prism_link(self, url: str) -> bool:
        """Fetches the real join/start URL for a Session-ID-only Prism
        row, right when it's actually needed (see _load_csv_async for
        why this doesn't happen at upload time). Returns True and
        re-keys the row to its real URL on success; on any failure the
        row stays under its placeholder key with an explanatory status,
        ready to be tried again — by the next Auto Pilot tick, or by
        clicking Launch again."""
        m = self.table_model.get(url)
        if not m or not m.prism_session_id:
            return False

        self.table_model.set_fields(url, status="🟣 Fetching Prism link...")
        try:
            fetched = await fetch_sessions_async([m.prism_session_id])
        except Exception as e:
            self.log_event(f"Prism link fetch failed for '{m.session_name}': {e}", "err")
            self.table_model.set_fields(url, status="⏳ Waiting — No Prism Link Yet")
            return False

        data = fetched.get(m.prism_session_id)
        if not data:
            self.log_event(
                f"Prism Session ID {m.prism_session_id} not found for '{m.session_name}'.", "err")
            self.table_model.set_fields(url, status="❌ Prism ID Not Found")
            return False

        real_url = data.get("Start URL") or data.get("Join URL")
        if not real_url:
            # This is the expected case well before the ~2h window —
            # Prism hasn't generated a link yet. Not an error, just early.
            self.log_event(
                f"Prism link not generated yet for '{m.session_name}' "
                f"(usually appears ~2h before start) — will try again.", "warn")
            self.table_model.set_fields(url, status="⏳ Waiting — No Prism Link Yet")
            return False

        # Prism fills gaps only — anything already on the row wins.
        m.session_name = m.session_name or data.get("Session Name", "")
        m.start_time    = m.start_time or data.get("Starts At", "")
        m.end_time      = m.end_time or data.get("Ends At", "")
        m.meeting_code  = m.meeting_code or data.get("Meeting Code", "")
        m.prism_type    = m.prism_type or data.get("Session Type", "")

        if not self.table_model.rekey(url, real_url):
            self.log_event(
                f"Fetched link for '{m.session_name}' matches an existing session — skipped.", "err")
            # Every other early-return in this function resets status
            # back to something retryable — this branch didn't, so the
            # row was left stuck on "Fetching Prism link..." forever.
            # That status isn't in Auto-Pilot's LAUNCHABLE_STATUSES, so
            # nothing would ever pick this row up again without a
            # manual Retry click.
            self.table_model.set_fields(url, status="⏳ Waiting — No Prism Link Yet")
            return False

        self.table_model.set_fields(real_url, status="Pending", action="▶ Launch")
        self.log_event(f"Prism link ready: {m.session_name}", "ok")
        return True

    async def _launch_with_prism_fetch_if_needed(self, url: str):
        """Single dispatch point for every launch — Auto Pilot and the
        manual Action-column click both go through this, so the
        just-in-time fetch can't accidentally be skipped from one path
        or the other."""
        m = self.table_model.get(url)
        if not m:
            return
        if m.is_prism() and not self._has_real_link(m):
            if not await self._fetch_prism_link(url):
                return  # stays Pending-equivalent; next attempt retries the fetch
            url = m.join_url  # rekey() changed it — model object is the same, field is updated
        if m.is_prism():
            self.engine.launch_prism(url)
        else:
            self.engine.launch_zoom(url)

    # ── Add session ──────────────────────────────────────────────────
    def _on_add_session(self):
        dlg = AddSessionDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.result_model:
            if self.table_model.add_session(dlg.result_model):
                self.log_event(f"Session added: {dlg.result_model.session_name}", "ok")
            else:
                notify(self, "Duplicate", "A session with that Meeting Link already exists.")

    # ── Toolbar actions ──────────────────────────────────────────────
    def _on_refresh_participants(self):
        self.engine.refresh_all_participants()

    def _on_toggle_participant_scraping(self):
        """Perf toggle: pauses the Host/Co-Host/Participant Count live
        refresh (the per-tick DOM scrape in Engine._scan_one_participant)
        without touching Prism status-push retries, which keep running
        either way — see Engine.set_participant_scraping()'s docstring.
        Manual 'Refresh Participants' still works while paused, since
        that's a separate, on-demand call the toggle doesn't gate."""
        enabled = not self.engine.participant_scraping_enabled
        self.engine.set_participant_scraping(enabled)
        self.btn_scraping_toggle.setText(" Pause Scraping" if enabled else " Resume Scraping")
        self.btn_scraping_toggle.setToolTip("Pause Live Scraping" if enabled else "Resume Live Scraping")
        if enabled:
            self.log_event("Live participant scraping resumed — Host / Co-Host / "
                            "Participant Count will refresh again.", "ok")
        else:
            self.log_event("Live participant scraping paused — Host / Co-Host / Participant Count "
                            "columns will stop refreshing (Prism status-push retries keep running; "
                            "use 'Refresh Participants' for an on-demand update).", "warn")

    def _on_select_all_live(self):
        """Selects every row currently LIVE, so 'Launch Poll (Selected)'
        can bulk-launch across all of them at once — mirrors the
        colleague's 'Select All Live' + 'Launch Poll (Selected)' pair."""
        sel_model = self.table.selectionModel()
        sel_model.clearSelection()
        live_rows = [row for row in range(self.table_model.rowCount())
                     if "LIVE" in (self.table_model.get(
                         self.table_model.url_at_row(row)).status or "")]
        if not live_rows:
            self.log_event("Select All Live: no LIVE sessions right now.", "warn")
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        selection = QItemSelection()
        last_col = self.table_model.columnCount() - 1
        for row in live_rows:
            selection.select(self.table_model.index(row, 0), self.table_model.index(row, last_col))
        sel_model.select(selection, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.log_event(f"Selected {len(live_rows)} LIVE session(s).", "info")

    def _on_launch_poll_selected(self):
        """Toolbar 'Launch Poll (Selected)' — bulk-launches the default
        poll across every currently-selected row (concurrently; each
        session has its own browser tab). Selection isn't required to
        be LIVE-only here — launch_poll_for_many() filters and reports
        skips for anything that isn't eligible, same as a single click
        on a non-LIVE row's Poll Action cell would."""
        rows = {idx.row() for idx in self.table.selectionModel().selectedRows()}
        if not rows:
            self.log_event("Launch Poll (Selected): no rows selected — use 'Select All Live' "
                            "or click/drag to select rows first.", "warn")
            return
        urls = [self.table_model.url_at_row(r) for r in rows]
        urls = [u for u in urls if u]
        asyncio.create_task(self.engine.launch_poll_for_many(urls))

    def _on_clear_all(self):
        if self.auto_pilot_running:
            if not confirm(self, "Stop Auto-Pilot?",
                           "Auto-Pilot is currently running. Clear All requires stopping it "
                           "first.\n\nStop Auto-Pilot and clear all sessions?",
                           danger=True):
                return
            self._stop_auto_pilot()
        elif not confirm(self, "Clear All",
                         "This will close all sessions and reset the dashboard.\n\nProceed?",
                         danger=True):
            return
        # Dispose the per-row poll dropdowns explicitly before the model
        # reset rather than relying on Qt to clean embedded index widgets up.
        for row in range(self.table_model.rowCount()):
            self.table.setIndexWidget(
                self.table_model.index(row, COL_INDEX["poll_select"]), None)
        self.engine.clear_all()
        self.scan_label.setText("Cleared — ready for new sessions.")

    # ── Poll dropdown (per-row "Poll Select" column) ─────────────────
    _DEFAULT_POLL_LABEL = f"Default ({DEFAULT_POLL_NAME})"

    def _on_poll_select_changed(self, top_left, bottom_right, roles=None):
        col = COL_INDEX["poll_select"]
        if top_left.column() > col or bottom_right.column() < col:
            return
        for row in range(top_left.row(), bottom_right.row() + 1):
            self._sync_poll_select_widget(row)

    def _sync_poll_select_widget(self, row: int):
        url = self.table_model.url_at_row(row)
        m = self.table_model.get(url) if url else None
        index = self.table_model.index(row, COL_INDEX["poll_select"])
        if not m or not m.available_polls:
            if self.table.indexWidget(index) is not None:
                self.table.setIndexWidget(index, None)
            return

        combo = self.table.indexWidget(index)
        if not isinstance(combo, QComboBox):
            combo = QComboBox()
            self.table.setIndexWidget(index, combo)
            # `activated` fires only for a real user choice — never for the
            # programmatic repopulate below — so a refresh can't be mistaken
            # for the user picking something.
            combo.activated.connect(
                lambda i, u=url, c=combo: self._on_poll_selected(u, c.itemText(i)))

        wanted = [self._DEFAULT_POLL_LABEL] + list(m.available_polls)
        combo.blockSignals(True)
        if [combo.itemText(i) for i in range(combo.count())] != wanted:
            combo.clear()
            combo.addItems(wanted)
        combo.setCurrentIndex(max(0, combo.findText(m.selected_poll)) if m.selected_poll else 0)
        combo.blockSignals(False)

    def _on_poll_selected(self, url: str, text: str):
        """Only records the choice — never launches anything."""
        selected = "" if text == self._DEFAULT_POLL_LABEL else text
        m = self.table_model.get(url)
        if not m or m.selected_poll == selected:
            return
        self.table_model.set_fields(url, selected_poll=selected)
        self.log_event(
            f"Poll for '{m.session_name}' set to '{selected or DEFAULT_POLL_NAME}' — "
            f"used when a poll launches for this session.", "info")

    # ── Auto Pilot ───────────────────────────────────────────────────
    def _on_toggle_auto_pilot(self):
        if not self.auto_pilot_running:
            self.auto_pilot_running = True
            self.btn_auto.setText("■  STOP AUTO-PILOT")
            self.btn_auto.setProperty("running", "true")
            self.scan_label.setText("Auto-Pilot starting in 3s...")
            self.log_event(
                f"Auto-Pilot ON — sessions launch within the configured window, "
                f"poll(s) auto-launch {AUTO_POLL_END_MINUTES}m before End Time.", "ok")
            QTimer.singleShot(3000, self._start_auto_pilot_loop)
            self.btn_auto.style().unpolish(self.btn_auto)
            self.btn_auto.style().polish(self.btn_auto)
        else:
            self._stop_auto_pilot()

    def _stop_auto_pilot(self):
        """Shared by the toolbar toggle AND Clear All (FR8) — one place
        that actually stops Auto Pilot, so both call sites can't drift."""
        self.auto_pilot_running = False
        self._auto_pilot_timer.stop()
        self.btn_auto.setText("▶  START AUTO-PILOT")
        self.btn_auto.setProperty("running", "false")
        self.scan_label.setText("Auto-Pilot idle")
        self.log_event("Auto-Pilot stopped.", "warn")
        self.btn_auto.style().unpolish(self.btn_auto)
        self.btn_auto.style().polish(self.btn_auto)

    def _start_auto_pilot_loop(self):
        if not self.auto_pilot_running:
            return
        self._run_auto_pilot_check()
        self._auto_pilot_timer.start()

    @staticmethod
    def _parse_time_robust(time_str: str):
        time_str = str(time_str).strip()
        iso_clean = re.sub(r'\.\d+', '', time_str)
        iso_clean = re.sub(r'(Z|[+-]\d{2}:\d{2})$', '', iso_clean)
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
            try: return datetime.strptime(iso_clean, fmt)
            except Exception: continue
        t = time_str.lower()
        if "am" in t and " am" not in t: t = t.replace("am", " am")
        if "pm" in t and " pm" not in t: t = t.replace("pm", " pm")
        for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M:%S", "%H:%M"):
            try: return datetime.strptime(t, fmt)
            except Exception: continue
        try:
            if len(t.split(":")[0]) == 1:
                return datetime.strptime("0" + t, "%I:%M %p")
        except Exception:
            pass
        return None

    def _run_auto_pilot_check(self):
        if not self.auto_pilot_running:
            return
        now = datetime.now()
        self.scan_label.setText(f"Scanning...  {now.strftime('%H:%M:%S')}")
        LAUNCHABLE_STATUSES = ("Pending", "⏳ Waiting — No Prism Link Yet")
        for m in self.table_model.all_models():
            if m.status not in LAUNCHABLE_STATUSES:
                continue
            try:
                raw_time = str(m.start_time).strip()
                is_utc = raw_time.endswith("Z") or bool(re.search(r'[+-]\d{2}:\d{2}$', raw_time))
                start_obj = self._parse_time_robust(raw_time)
                if not start_obj:
                    continue
                if is_utc:
                    utc_offset = -(_time.timezone if not _time.daylight else _time.altzone)
                    start_time = start_obj + timedelta(seconds=utc_offset)
                else:
                    start_time = start_obj.replace(year=now.year, month=now.month, day=now.day)
                diff = (start_time - now).total_seconds() / 60
                if -30 <= diff <= 15:
                    self.log_event(
                        f"Auto-Pilot: launching '{m.session_name}' ({diff:.0f}m to start)", "info")
                    asyncio.create_task(self._launch_with_prism_fetch_if_needed(m.join_url))
            except Exception:
                pass

        # ── Auto-launch the default poll N minutes before End Time ──
        # Only ever considers sessions that are currently LIVE, and
        # fires exactly once per session (poll_auto_triggered guards
        # this — a failed auto-launch doesn't get silently retried every
        # 10s; the Poll Action cell stays clickable for a manual retry).
        for m in self.table_model.all_models():
            if "LIVE" not in m.status:
                continue
            if m.poll_auto_triggered or m.poll_launching:
                continue
            try:
                end_obj = self._parse_time_robust(str(m.end_time).strip())
                if not end_obj:
                    continue
                end_time = end_obj.replace(year=now.year, month=now.month, day=now.day)
                diff = (end_time - now).total_seconds() / 60
                if 0 <= diff <= AUTO_POLL_END_MINUTES:
                    m.poll_auto_triggered = True
                    poll_name = m.selected_poll or DEFAULT_POLL_NAME
                    self.log_event(
                        f"Auto-Pilot: launching '{poll_name}' for '{m.session_name}' "
                        f"({diff:.0f}m to end)", "info")
                    self.engine.launch_poll(m.join_url, poll_name)
            except Exception:
                pass

    # ── Table clicks (Action / Retry columns) ───────────────────────
    def _on_table_clicked(self, index: QModelIndex):
        url = self.table_model.url_at_row(index.row())
        if not url:
            return
        col = index.column()
        m = self.table_model.get(url)
        if not m:
            return

        if col == COL_INDEX["retry"]:
            if "RETRY" in (m.retry or ""):
                self.engine.force_retry(url)
            return

        if col == COL_INDEX["action"]:
            action = m.action or ""
            if "Launch" in action:
                asyncio.create_task(self._launch_with_prism_fetch_if_needed(url))
            elif "End" in action:
                if confirm(self, "Confirm End", "Are you sure you want to END this meeting?", danger=True):
                    self.engine.end_session(url)
            elif "JOIN" in action:
                if m.is_zoom(): self.engine.join_zoom_guest(url)
            return

        if col == COL_INDEX["poll_action"]:
            if "Launch Poll" in (m.poll_action or ""):
                self.engine.launch_poll(url, m.selected_poll or None)
            return


def _escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
