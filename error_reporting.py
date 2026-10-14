"""
error_reporting.py
-------------------
What happens to an exception nobody caught.

Before this, an uncaught exception in the windowed exe went nowhere: no console, so the
traceback vanished, and the symptom was "a button did nothing" or "that row froze". Now:

  * the FULL traceback always goes to the file log (Open logs folder) and a one-line
    summary to the Event Log, whichever thread / task it came from;
  * a friendly "Something went wrong" dialog appears ONLY for an exception on the UI
    thread (that is where a click that did nothing comes from), at most once per distinct
    error and not more than once a minute, so a repeating error can't bury the user in
    popups. Background threads and asyncio tasks only log.

install(window) hooks sys.excepthook, threading.excepthook and (via install_loop_handler)
the asyncio loop's exception handler.
"""

import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from applog import log_to_file

MIN_DIALOG_GAP = 60.0      # seconds between "Something went wrong" dialogs
_IGNORED = (KeyboardInterrupt, SystemExit, GeneratorExit)


def signature(exc_type, exc, tb) -> str:
    """Same bug -> same signature: exception type + message + innermost frame."""
    where = ""
    if tb is not None:
        last = traceback.extract_tb(tb)[-1]
        where = f"{last.filename}:{last.lineno}"
    return f"{getattr(exc_type, '__name__', exc_type)}|{str(exc)[:200]}|{where}"


class Reporter(QObject):
    """Receives reports from any thread (the signal hops to the UI thread) and decides
    what to show."""
    _report = Signal(str, str, bool)   # one-line summary, signature, may-show-dialog

    def __init__(self, window, dialog_fn=None, clock=time.monotonic):
        super().__init__(window)
        self._window = window
        self._dialog_fn = dialog_fn or self._default_dialog
        self._clock = clock
        self._shown = set()
        self._last_dialog = None
        self._showing = False
        self.dialogs_shown = 0
        self._report.connect(self._on_report)

    # called from any thread
    def handle(self, exc_type, exc, tb, ui_thread: bool):
        if exc_type is None or issubclass(exc_type, _IGNORED):
            return
        try:
            text = "".join(traceback.format_exception(exc_type, exc, tb))
            log_to_file(f"Unhandled exception:\n{text}", "err")
            summary = f"Unexpected error: {exc_type.__name__}: {str(exc)[:160]}"
            self._report.emit(summary, signature(exc_type, exc, tb), bool(ui_thread))
        except Exception:
            pass   # the error handler must never raise

    @Slot(str, str, bool)
    def _on_report(self, summary: str, sig: str, may_dialog: bool):
        try:
            self._window.log_event(f"{summary} (details saved to the log file)", "err")
        except Exception:
            pass
        if not may_dialog or self._showing or sig in self._shown:
            return
        now = self._clock()
        if self._last_dialog is not None and now - self._last_dialog < MIN_DIALOG_GAP:
            return
        self._shown.add(sig)
        self._last_dialog = now
        self._showing = True
        try:
            self.dialogs_shown += 1
            self._dialog_fn()
        finally:
            self._showing = False

    def _default_dialog(self):
        from main_window import _StyledDialog
        dlg = _StyledDialog(
            self._window, "Something went wrong",
            "The app hit an unexpected problem. It is still running, but the last action may not "
            "have completed.\n\nThe details were saved to the log; if it keeps happening, send the "
            "log folder to whoever supports this app.",
            buttons=[("Close", False, False), ("Open logs folder", True, True)])
        dlg.exec()
        if dlg._result:
            self._window._on_open_logs_folder()


_reporter = None


def install(window) -> Reporter:
    global _reporter
    _reporter = Reporter(window)
    main_thread = threading.main_thread()

    def _sys_hook(exc_type, exc, tb):
        _reporter.handle(exc_type, exc, tb, ui_thread=threading.current_thread() is main_thread)
        try:
            sys.__excepthook__(exc_type, exc, tb)   # still print when there is a console (dev runs)
        except Exception:
            pass

    def _thread_hook(args):
        _reporter.handle(args.exc_type, args.exc_value, args.exc_traceback, ui_thread=False)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    return _reporter


def install_loop_handler(loop):
    """asyncio: exceptions in tasks nobody awaits, and callbacks. Logged, never a dialog."""
    def handler(_loop, context):
        exc = context.get("exception")
        if exc is not None and _reporter is not None:
            _reporter.handle(type(exc), exc, exc.__traceback__, ui_thread=False)
        else:
            log_to_file(f"asyncio: {context.get('message', 'unknown error')}", "err")
    loop.set_exception_handler(handler)
