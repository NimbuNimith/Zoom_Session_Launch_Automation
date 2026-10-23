"""
notifications.py
-----------------
Desktop notifications (Windows toasts via the tray icon) for the moments an operator
looking at another window needs to know about: a session went LIVE, failed, its window
was closed; a poll went live or failed.

Rules, so it stays useful rather than noisy:
  * only TRANSITIONS notify (a status that was already LIVE does not notify again);
  * nothing while this window is the active one - the operator is looking at it;
  * events arriving within ~2 s of each other are shown as one toast;
  * a bell button in the header turns it all off (remembered in settings).

detect_events() is a pure function over the session list so the transitions are testable
without Qt.
"""

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon

COALESCE_MS = 2000
SETTINGS_KEY = "notifications/enabled"


def _classify_status(status: str):
    s = status or ""
    if "LIVE" in s:
        return "live"
    if "Window Closed" in s:
        return "closed"
    if "Failed" in s or "Force Closed" in s:
        return "failed"
    return None


def _classify_poll(poll_status: str):
    s = poll_status or ""
    if "Poll Live" in s or "Quiz Live" in s:
        return "poll_live"
    if "Failed" in s:
        return "poll_failed"
    return None


def detect_events_ex(prev: dict, sessions) -> list:
    """Like detect_events, but each event is (title, message, session) so a listener (the Google
    Chat feed) can say WHICH session - host account, start time - and not only its name."""
    events = []
    for m in sessions:
        sk, pk = _classify_status(m.status), _classify_poll(m.poll_status)
        old = prev.get(m.join_url)
        prev[m.join_url] = (sk, pk)
        if old is None:
            continue   # first sighting (e.g. just loaded from a CSV): nothing to compare with
        name = m.session_name or "Session"
        if sk != old[0]:
            if sk == "live":
                events.append(("Session is live", name, m))
            elif sk == "failed":
                events.append(("Session failed", f"{name} - see the Event Log", m))
            elif sk == "closed":
                events.append(("Session window closed", f"{name} - use Relaunch to reopen it", m))
        if pk != old[1]:
            if pk == "poll_live":
                events.append(("Poll is live", name, m))
            elif pk == "poll_failed":
                events.append(("Poll failed", f"{name}: {(m.poll_status or '').replace('Failed:', '').strip()}", m))
    for gone in set(prev) - {m.join_url for m in sessions}:
        del prev[gone]
    return events


def detect_events(prev: dict, sessions) -> list:
    """prev: {join_url: (status_kind, poll_kind)} from the last call (updated in place).
    sessions: objects with join_url, session_name, status, poll_status.
    Returns [(title, message)] for transitions INTO live / failed / closed / poll live / poll failed."""
    return [(t, msg) for t, msg, _ in detect_events_ex(prev, sessions)]


class Notifier(QObject):
    def __init__(self, window, settings, icon: QIcon, tray=None):
        super().__init__(window)
        self._window = window
        self._settings = settings
        self._prev = {}
        self._pending = []
        self.enabled = str(settings.value(SETTINGS_KEY, "true")).lower() not in ("false", "0")
        self.shown = []    # what was actually displayed (for tests)
        self.listeners = []   # callables(events_with_sessions): see on_model_changed
        self._tray = tray
        if self._tray is None and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(icon, window)
            self._tray.setToolTip("Command Center")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COALESCE_MS)
        self._timer.timeout.connect(self._flush)

    def set_enabled(self, on: bool):
        self.enabled = bool(on)
        self._settings.setValue(SETTINGS_KEY, "true" if on else "false")
        if not on:
            self._pending.clear()

    def on_model_changed(self, sessions):
        """Call whenever session data changed (the table model's dataChanged)."""
        full = detect_events_ex(self._prev, sessions)
        if not full:
            return
        for cb in self.listeners:       # e.g. the Google Chat feed: always, whatever the bell / window focus
            try:
                cb(full)
            except Exception:
                pass
        events = [(t, msg) for t, msg, _ in full]
        if not self.enabled or self._window_in_use():
            return
        self._pending.extend(events)
        self._timer.start()

    def _window_in_use(self) -> bool:
        w = self._window
        return bool(w.isActiveWindow() and not w.isMinimized())

    def _flush(self):
        events, self._pending = self._pending, []
        if not events or not self.enabled or self._window_in_use():
            return
        if len(events) == 1:
            title, msg = events[0]
        else:
            title = f"{len(events)} updates"
            msg = "\n".join(f"{t}: {m}" for t, m in events[:4])
            if len(events) > 4:
                msg += f"\n+{len(events) - 4} more"
        self.shown.append((title, msg))
        if self._tray is not None:
            self._tray.show()
            self._tray.showMessage(title, msg, QSystemTrayIcon.Information, 6000)

    def hide_tray(self):
        if self._tray is not None:
            self._tray.hide()
