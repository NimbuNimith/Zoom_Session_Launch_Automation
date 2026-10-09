"""
updater.py
-----------
Ported from Zoom_Ai_V54.py's check_for_update()/_do_update() — same
version-check-against-GitHub-raw, same download-and-replace-via-.bat
mechanism. What changed since the port:

  1. tkinter.messagebox -> the app's own confirm()/notify() dialogs, so
     the update prompt looks like the rest of the app instead of a native
     popup (same reason those two functions exist at all — see
     main_window.py's _StyledDialog docstring).
  2. root.after(1000, _prompt) -> QTimer.singleShot(1000, _prompt) —
     same "wait a second so the prompt doesn't appear before the window
     does" behavior, Qt's timer API instead of Tkinter's.
  3. The ~435 MB download runs on a background thread with a progress
     dialog (percentage, size, speed, time left, Cancel) instead of
     freezing the window behind a popup. The version check at startup is
     also off the UI thread now.
  4. The final replace-and-restart script lives in update_swap.py (see
     there for the failure it fixes).

Release workflow: bump CURRENT_VERSION here and in installer.iss -> build
-> GitHub Release (asset must be named Zoom_Ai_latest.exe) -> set
version.txt on main LAST, so nobody is prompted before the file exists.
"""

import os
import sys
import threading

import requests
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from downloader import download_file, friendly_error, DownloadCancelled
from update_swap import write_swap_script, launch_swap_script

CURRENT_VERSION = "2.5.1"
VERSION_URL  = "https://raw.githubusercontent.com/NimbuNimith/Zoom_Session_Launch_Automation/main/version.txt"
DOWNLOAD_URL = "https://github.com/NimbuNimith/Zoom_Session_Launch_Automation/releases/latest/download/Zoom_Ai_latest.exe"

class _DownloadSignals(QObject):
    """Lives on the UI thread; the worker thread only ever .emit()s on it, which
    Qt delivers to the UI thread's event loop (queued connection)."""
    progress = Signal(object, object, float)   # bytes done, total bytes (0 = unknown), bytes/sec
    finished = Signal()
    failed = Signal(str)
    cancelled = Signal()


def _download_worker(url: str, part_path: str, final_path: str,
                     cancel: threading.Event, sig: _DownloadSignals):
    """Runs on a background thread. Never touches Qt widgets — it only emits."""
    try:
        download_file(url, part_path, final_path, cancel, sig.progress.emit, magic=b"MZ",
                      magic_error="The downloaded file is not a Windows program.")
    except DownloadCancelled:
        sig.cancelled.emit()
    except Exception as e:
        sig.failed.emit(friendly_error(e))
    else:
        sig.finished.emit()


class _UpdateDownload(QObject):
    """Owns one update: the progress dialog, the download thread, and the
    hand-off to the replace-and-restart script once the file is complete."""

    def __init__(self, parent, new_version: str, current_exe: str):
        super().__init__(parent)
        from main_window import UpdateProgressDialog  # local import: main_window imports this module
        self._parent = parent
        self._exe = current_exe
        self._new_path = current_exe + ".new"
        self._part_path = self._new_path + ".part"
        self._cancel = threading.Event()
        self._sig = _DownloadSignals(self)
        self._dlg = UpdateProgressDialog(parent, new_version)

        self._sig.progress.connect(self._dlg.set_progress)
        self._sig.finished.connect(self._on_finished)
        self._sig.failed.connect(self._on_failed)
        self._sig.cancelled.connect(self._on_cancelled)
        self._dlg.cancel_requested.connect(self._cancel.set)

    def start(self):
        self._dlg.open()
        # daemon: closing the app mid-download must never wait on this thread
        threading.Thread(
            target=_download_worker, daemon=True, name="update-download",
            args=(DOWNLOAD_URL, self._part_path, self._new_path, self._cancel, self._sig),
        ).start()

    @Slot()
    def _on_finished(self):
        self._dlg.finish()
        self._release()
        if self._cancel.is_set():
            # Cancel was clicked in the last instant, after the file was
            # already complete: honour it rather than restarting anyway.
            try:
                os.remove(self._new_path)
            except OSError:
                pass
            return
        # The swap script (and why it retries and clears PyInstaller's env
        # vars) lives in update_swap.py.
        bat_path = write_swap_script(self._exe, self._new_path)

        # NOT sys.exit(0) — that exits the interpreter immediately and skips
        # MainWindow.closeEvent() entirely, which is where the "sessions are
        # LIVE, are you sure?" confirmation lives, along with the only code
        # that actually calls engine.shutdown() to close the Playwright
        # browser cleanly. A bare sys.exit() here orphans the browser
        # process on update exactly the way the original zombie-process bug
        # did before closeEvent()/the 8s safety-net thread were added —
        # this reuses that same, already-tested shutdown path instead of
        # bypassing it.
        #
        # close() returns False when the user declines the "sessions are
        # live" prompt; the script is only started once the close is
        # accepted, so declining leaves nothing running (the downloaded .new
        # stays and the update is offered again next start).
        if self._parent.close():
            launch_swap_script(bat_path)
        else:
            try:
                os.remove(bat_path)
            except OSError:
                pass

    @Slot(str)
    def _on_failed(self, message: str):
        from main_window import notify
        self._dlg.finish()
        self._release()
        notify(self._parent, "Update Failed", f"Could not download the update:\n{message}")

    @Slot()
    def _on_cancelled(self):
        self._dlg.finish()
        self._release()

    def _release(self):
        global _active
        _active = None
        self.deleteLater()


_active = None   # the one in-flight _UpdateDownload, if any (also keeps it from being garbage-collected)


def _do_update(parent, new_version: str):
    global _active
    from main_window import notify  # local import avoids a circular import at module load

    current_exe = sys.executable if getattr(sys, "frozen", False) else None
    if not current_exe:
        notify(parent, "Update Available",
               f"Version {new_version} is available.\n\n"
               "Download the latest release from GitHub and replace this file.\n"
               "(Running from source right now, not a packaged .exe, so this "
               "can't self-replace — that only works in the built app.)")
        return
    if _active is not None:
        return   # an update is already downloading

    _active = _UpdateDownload(parent, new_version, current_exe)
    _active.start()


class _VersionCheck(QObject):
    """Fetches version.txt on a background thread (it used to block startup,
    on the UI thread, for up to 5 s when GitHub was slow) and hands the
    result back to the UI thread."""
    result = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self._parent = parent
        self.result.connect(self._on_result)

    def start(self):
        threading.Thread(target=self._fetch, daemon=True, name="update-check").start()

    def _fetch(self):
        try:
            resp = requests.get(VERSION_URL, timeout=5)
            resp.raise_for_status()
            self.result.emit(resp.text.strip())
        except Exception:
            pass   # a broken update check must never be the reason the app misbehaves

    @Slot(str)
    def _on_result(self, latest: str):
        try:
            from packaging import version as pkg_version
            if pkg_version.parse(latest) <= pkg_version.parse(CURRENT_VERSION):
                return
        except Exception:
            return

        def _prompt():
            from main_window import confirm
            if confirm(self._parent, "Update Available",
                       f"A new version is available: v{latest}\n"
                       f"You have: v{CURRENT_VERSION}\n\nDownload and restart now?",
                       danger=False):
                _do_update(self._parent, latest)
        QTimer.singleShot(1000, _prompt)


_check = None


def check_for_update(parent):
    """Call once, shortly after the main window is shown (see app.py).
    Silent no-op on any failure — a broken update check should never be
    the reason the app won't open."""
    global _check
    _check = _VersionCheck(parent)
    _check.start()
