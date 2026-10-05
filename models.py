"""
models.py
----------
SessionModel: same fields as the Tkinter version's SessionModel — ported
1:1, this is business logic and has nothing to do with which UI framework
is drawing it.

SessionTableModel: replaces the Tkinter version's raw Treeview + hand-
maintained `vals[n]` tuples. Columns are named fields on a real
QAbstractTableModel, and updates go through `set_fields(url, **kwargs)`,
which emits `dataChanged` for exactly the cells that changed — no more
"which index is Retry again?" bugs, and no full-grid repaint on every
heartbeat tick (the actual mechanism behind the "grid refresh under 1
second at 100+ sessions" NFR).
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Any

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QPersistentModelIndex

import theme
from theme import DOT_COLOR_ROLE, font_mono


@dataclass
class SessionModel:
    session_type: str            # "ZOOM" | "PRISM"
    join_url: str
    program_name: str = ""
    session_name: str = ""
    moderator_name: str = ""
    start_time: str = ""
    end_time: str = ""
    meeting_code: str = ""
    host_email: str = ""
    host_password: str = ""
    status: str = "Pending"
    action: str = "▶ Launch"
    retry: str = ""
    page: Any = None

    # participant data — filled in by the heartbeat
    host_name: str = ""
    cohost_names: list = field(default_factory=list)
    participant_count: int = 0
    launched_at: str = ""

    # Prism status-push
    prism_type: str = ""                    # session | workshop-session | session-group-session
    prism_session_id: str = ""
    prism_session_group_id: str = ""
    prism_status_pushed: bool = False
    prism_sync: str = ""                    # display text for the Prism Sync column

    # Poll launch (Participants → More → Polls → <poll> → Launch)
    poll_status: str = ""                   # display text for the Poll column
    poll_action: str = ""                   # "Launch Poll" when clickable, "" / "..." otherwise
    poll_launching: bool = False            # in-flight guard — prevents overlapping launches
    poll_auto_triggered: bool = False       # one-shot guard for the Auto-Pilot near-End-Time trigger
    available_polls: list = field(default_factory=list)   # Poll-type titles found at the LIVE transition; empty = scan not run / failed
    selected_poll: str = ""                 # "" = use the default poll; else the row's chosen poll title

    def is_zoom(self) -> bool:  return self.session_type == "ZOOM"
    def is_prism(self) -> bool: return self.session_type == "PRISM"

    def zoom_meeting_id(self) -> Optional[str]:
        m = re.search(r"/j/(\d+)|/s/(\d+)|/wc/(\d+)|/w/(\d+)", self.join_url)
        return next((g for g in m.groups() if g), None) if m else None

    def lock_key(self) -> str:
        return self.zoom_meeting_id() or self.join_url

    def prism_push_ids(self) -> dict:
        if self.prism_type == "session-group-session":
            return {
                "session_group_id":         self.prism_session_group_id,
                "session_group_session_id": self.prism_session_id,
            }
        return {"session_id": self.prism_session_id}

    def display_session_id(self) -> str:
        return self.prism_session_id if self.is_prism() else (self.zoom_meeting_id() or "")


# Column definitions: (key, header). `key` is either a SessionModel attr
# name or a small derived-value function looked up in _DERIVED below.
COLUMNS = [
    ("session_type",   "Type"),
    ("program_name",   "Program"),
    ("session_name",   "Topic / Name"),
    ("moderator_name", "Moderator"),
    ("start_time",     "Start Time"),
    ("end_time",       "End Time"),
    ("host_name",      "Host"),
    ("cohosts",        "Co-Host(s)"),
    ("participant_count", "Participants"),
    ("launched_at",    "Launched At"),
    ("status",         "Status"),
    ("action",         "Action"),
    ("retry",          "Retry"),
    ("poll_status",    "Poll"),
    ("poll_select",    "Poll Select"),
    ("poll_action",    "Poll Action"),
    ("prism_type",     "Prism Type"),
    ("prism_sync",     "Prism Sync"),
    ("session_id",     "Session ID"),
    ("meeting_code",   "Meeting Code"),
]
COL_INDEX = {key: i for i, (key, _) in enumerate(COLUMNS)}

_MONO_COLS = {"start_time", "end_time", "launched_at", "session_id", "meeting_code"}


def _derived(model: SessionModel, key: str):
    if key == "cohosts":
        return ", ".join(model.cohost_names)
    if key == "session_id":
        return model.display_session_id()
    if key == "poll_select":
        return model.selected_poll or "Default"
    return getattr(model, key, "")


def _dot_color_for(model: SessionModel, key: str):
    """Returns a QColor for the tally-dot delegate, or None for plain text."""
    if key == "status":
        s = model.status
        if "LIVE" in s: return theme.QC_RED
        if any(x in s for x in ("⚠️", "❌", "Failed", "Full", "Error")): return theme.QC_RED
        if "Waiting" in s: return theme.QC_AMBER
        if "Ended" in s: return theme.QC_MUTED
        return None
    if key == "prism_sync":
        if model.prism_sync.startswith("✅"): return theme.QC_GREEN
        if model.prism_sync.startswith("❌"): return theme.QC_RED
        return None
    if key == "poll_status":
        s = model.poll_status
        if "Live" in s: return theme.QC_GREEN
        if "Failed" in s: return theme.QC_RED
        if "Launching" in s: return theme.QC_AMBER
        return None
    if key == "session_type":
        return theme.QC_RED if model.session_type == "ZOOM" else theme.QC_AMBER
    return None


class SessionTableModel(QAbstractTableModel):
    """Single source of truth for every session row. This replaces the
    module-level `session_store` dict + hand-synced Treeview values from
    the Tkinter version — the model IS the store."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._urls: list[str] = []
        self._rows: dict[str, SessionModel] = {}
        self._mono_font = font_mono(9)

    # ── Qt model protocol ────────────────────────────────────────────
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._urls)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            # Uppercased here, not via QSS text-transform — that CSS
            # property's support is inconsistent enough across Qt
            # versions/platforms that it's safer to guarantee the case
            # at the data layer than to trust the stylesheet for it.
            return COLUMNS[section][1].upper()
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        model = self._rows[self._urls[index.row()]]
        key = COLUMNS[index.column()][0]

        if role == Qt.DisplayRole:
            return str(_derived(model, key))
        if role == DOT_COLOR_ROLE:
            return _dot_color_for(model, key)
        if role == Qt.FontRole and key in _MONO_COLS:
            return self._mono_font
        if role == Qt.TextAlignmentRole and key in ("participant_count", "start_time", "end_time", "launched_at"):
            return Qt.AlignCenter
        return None

    # ── Store operations ─────────────────────────────────────────────
    def get(self, url: str) -> Optional[SessionModel]:
        return self._rows.get(url)

    def all_models(self):
        return list(self._rows.values())

    def add_session(self, model: SessionModel) -> bool:
        if model.join_url in self._rows:
            return False
        row = len(self._urls)
        self.beginInsertRows(QModelIndex(), row, row)
        self._urls.append(model.join_url)
        self._rows[model.join_url] = model
        self.endInsertRows()
        return True

    def rekey(self, old_url: str, new_url: str) -> bool:
        """Swap a row's identity key. Used when a placeholder key (a
        Prism row registered before its real link exists — see
        PRISM_PENDING_PREFIX in main_window.py) is replaced by the real
        join_url once fetched. Every other piece of state on the row
        (status, retry, page, everything) carries over untouched —
        only the dict key and model.join_url change.

        Returns False (and changes nothing) if new_url is already taken
        by a different row, so a bad fetch can't silently clobber an
        existing session."""
        if old_url not in self._rows or old_url == new_url:
            return old_url == new_url
        if new_url in self._rows:
            return False
        row = self._urls.index(old_url)
        model = self._rows.pop(old_url)
        model.join_url = new_url
        self._rows[new_url] = model
        self._urls[row] = new_url
        left = self.index(row, 0)
        right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(left, right)
        return True

    def clear_all(self):
        self.beginResetModel()
        self._urls.clear()
        self._rows.clear()
        self.endResetModel()

    def set_fields(self, url: str, **kwargs):
        """Update one or more attributes on a row's SessionModel and emit
        dataChanged for exactly the affected columns — not the whole grid.

        If `status` changes and `retry` isn't explicitly given, the Retry
        button is auto-derived from the status text (same rule the old
        Tkinter update_status used) — a single source of truth, so no
        launch/failure call site can forget to show the Retry button."""
        if url not in self._rows:
            return
        if "status" in kwargs and "retry" not in kwargs:
            s = kwargs["status"]
            kwargs["retry"] = "↺ RETRY" if any(
                x in s for x in ("⚠️", "❌", "Failed", "Full", "Error")) else ""

        row = self._urls.index(url)
        model = self._rows[url]
        cols_touched = set()
        for k, v in kwargs.items():
            setattr(model, k, v)
            if k == "cohost_names":
                cols_touched.add(COL_INDEX["cohosts"])
            elif k in ("available_polls", "selected_poll"):
                cols_touched.add(COL_INDEX["poll_select"])
            elif k in COL_INDEX:
                cols_touched.add(COL_INDEX[k])
            elif k in ("prism_session_id",):
                cols_touched.add(COL_INDEX["session_id"])
        # status/prism_sync changes also affect the dot (same cell, no extra column)
        for c in sorted(cols_touched):
            idx = self.index(row, c)
            self.dataChanged.emit(idx, idx, [Qt.DisplayRole, DOT_COLOR_ROLE])

    def row_of(self, url: str) -> Optional[int]:
        try:
            return self._urls.index(url)
        except ValueError:
            return None

    def url_at_row(self, row: int) -> Optional[str]:
        if 0 <= row < len(self._urls):
            return self._urls[row]
        return None

    def summary(self):
        total = len(self._urls)
        live = sum(1 for m in self._rows.values() if "LIVE" in m.status)
        return total, live
