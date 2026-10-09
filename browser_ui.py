"""
browser_ui.py
--------------
The progress dialog around the one-time browser download (browser_setup.py).
Engine.start() awaits provide_browser() when the browser isn't already in its
fixed folder — the installer normally puts it there, so most people never
see this.
"""

import asyncio
import threading

from PySide6.QtCore import QObject, Signal, Slot

import browser_setup
from downloader import DownloadCancelled, friendly_error


class _Signals(QObject):
    progress = Signal(object, object, float)
    status = Signal(str)
    finished = Signal(str)     # path to chrome.exe
    failed = Signal(str)
    cancelled = Signal()


class _Job(QObject):
    """Receives the worker's signals on the UI thread and resolves the future."""

    def __init__(self, fut, dlg):
        super().__init__()
        self._fut = fut
        self._dlg = dlg

    def _resolve(self, result):
        self._dlg.finish()
        if not self._fut.done():
            self._fut.set_result(result)

    @Slot(str)
    def on_finished(self, exe):
        self._resolve(("ok", exe))

    @Slot(str)
    def on_failed(self, message):
        self._resolve(("failed", message))

    @Slot()
    def on_cancelled(self):
        self._resolve(("cancelled", ""))


def _worker(rev, cancel, sig):
    try:
        exe = browser_setup.install_browser(rev, cancel, sig.progress.emit, sig.status.emit)
    except DownloadCancelled:
        sig.cancelled.emit()
    except Exception as e:
        sig.failed.emit(friendly_error(e))
    else:
        sig.finished.emit(exe)


async def provide_browser(parent, rev: str) -> str:
    """Download + unpack the browser with a progress dialog. Returns the path
    to chrome.exe, or raises RuntimeError (cancelled / failed and the user
    chose not to retry)."""
    from main_window import UpdateProgressDialog, confirm   # local import: main_window imports this lazily too

    loop = asyncio.get_running_loop()
    while True:
        fut = loop.create_future()
        cancel = threading.Event()
        sig = _Signals()
        dlg = UpdateProgressDialog(
            parent, heading="Downloading the browser (one time)",
            message="Command Center needs its browser the first time it runs on this "
                    "computer. This only happens once.",
            window_title="Setting Up")
        job = _Job(fut, dlg)
        sig.progress.connect(dlg.set_progress)
        sig.status.connect(dlg.set_status)
        sig.finished.connect(job.on_finished)
        sig.failed.connect(job.on_failed)
        sig.cancelled.connect(job.on_cancelled)
        dlg.cancel_requested.connect(cancel.set)
        dlg.open()
        threading.Thread(target=_worker, args=(rev, cancel, sig), daemon=True,
                         name="browser-download").start()

        kind, value = await fut
        if kind == "ok":
            return value
        if kind == "cancelled":
            raise RuntimeError("The browser download was cancelled.")
        if not confirm(parent, "Browser Download Failed", f"{value}\n\nTry again?", danger=False):
            raise RuntimeError(value)
