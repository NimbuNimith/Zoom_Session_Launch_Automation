r"""
app.py
-------
Entry point. Bootstraps Qt + qasync — asyncio runs AS Qt's event loop, on
the same thread. This is the concrete payoff of picking PySide6 + qasync
over plain Tkinter here: engine.py's coroutines call
`self.model.set_fields(...)` / `self.log(...)` directly, no
`root.after(0, lambda: ...)` marshaling required, because there's no
second thread to cross.

START-UP ORDER (why main() looks the way it does)
--------------------------------------------------
Importing main_window pulls in the engine, Playwright, requests and more —
several seconds on a machine whose antivirus scans every file it touches.
So main() shows the start-up window (startup_window.py) FIRST, using only
light imports, and then loads everything else behind it while the bar moves:

    start up -> load the interface -> build the window -> start the browser
    engine -> load the Prism access -> hand over to the main window

The window is an ordinary one (taskbar button, minimizable, never on top).
It replaces the PyInstaller bootloader splash, which listed unpacked file
names and could not be minimized.

BROWSER: Chromium is NOT inside the exe. The installer puts it in
%LOCALAPPDATA%\Command Center\chromium (or the app downloads it once) — see
browser_setup.py and the Engine.start() comments.
"""

import sys
import os
import asyncio
import threading
import time

# qasync picks its Qt binding at import time and prefers PyQt5/PyQt6 if they are installed
# (a dev machine with PyQt5 then silently gets a loop that never runs). We are PySide6.
os.environ.setdefault("QT_API", "pyside6")

import qasync
import qasync_patch
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

# Works around a known, unresolved upstream qasync bug where a burst of
# near-simultaneous asyncio.create_task() calls (Auto-Pilot, the
# heartbeat loops, a CSV load, ... all doing this around the same
# moment) can crash with `AssertionError: assert timerid not in
# self.__callbacks` inside qasync's own timer bookkeeping — silently
# destroying whatever task triggered it. See qasync_patch.py for the
# full writeup. Must run before qasync.QEventLoop(app) is constructed.
qasync_patch.patch()

# Only light modules here: main_window / engine / playwright / requests are
# imported AFTER the start-up window is on screen (see main()).
from paths import resource_path
from single_instance import try_acquire_or_notify_existing, wire_activation_to_window

BROWSER_WAIT_SECONDS = 30   # how long the start-up window waits for the browser engine before handing over anyway
TOKEN_WAIT_SECONDS = 4

# Modules with no Qt objects in them, imported on a background thread while the
# start-up window animates (importing them on the UI thread would freeze it).
_PRELOAD = ("playwright.async_api", "requests", "psutil", "openpyxl", "packaging.version")


def _preload_heavy_modules():
    import importlib
    for name in _PRELOAD:
        try:
            importlib.import_module(name)
        except Exception:
            pass   # the real import later reports a genuine problem


def _warm_up_pandas():
    """pandas is only needed for the CSV upload; importing it takes seconds on a
    machine that scans every file, so do it quietly in the background after start-up."""
    try:
        import pandas  # noqa: F401
    except Exception:
        pass


class _ActiveWindow:
    """What a second launch attempt should raise: the start-up window while it is
    showing, the main window afterwards (wire_activation_to_window takes one window)."""

    def __init__(self, window):
        self.window = window

    def isMinimized(self):
        return self.window.isMinimized()

    def showNormal(self):
        self.window.showNormal()

    def show(self):
        self.window.show()

    def raise_(self):
        self.window.raise_()

    def activateWindow(self):
        self.window.activateWindow()


def _pump(app, seconds=0.0, until=None):
    """Keep the start-up window painting while something blocking happens."""
    end = time.monotonic() + seconds
    while True:
        app.processEvents()
        if until is not None:
            if until():
                return
        elif time.monotonic() >= end:
            return
        time.sleep(0.015)


async def _finish_startup(startup, window, active):
    """Start the browser engine and the Prism token fetch, advance the bar as they
    complete, then replace the start-up window with the real one."""
    from prism_token import refresh_token
    from updater import check_for_update, on_startup_complete

    loop = asyncio.get_running_loop()
    engine_task = loop.create_task(window.engine.start())
    token_task = loop.create_task(refresh_token(log_fn=window.log_event))

    startup.step("Starting the browser engine…", 65, 90)
    deadline = time.monotonic() + BROWSER_WAIT_SECONDS
    # Hand over early when the browser has to be downloaded (the main window shows
    # that download's progress) or when starting it failed (the Event Log says why).
    while (not window.engine.browser_ready and not engine_task.done()
           and not window.engine.downloading_browser and time.monotonic() < deadline):
        await asyncio.sleep(0.1)

    startup.step("Loading Prism access…", 90, 97)
    try:
        await asyncio.wait_for(asyncio.shield(token_task), timeout=TOKEN_WAIT_SECONDS)
    except Exception:
        pass   # refresh_token never raises; a timeout just means it finishes in the background

    startup.step("Ready", 100, 100)
    startup.finish_instantly()
    await asyncio.sleep(0.2)

    if startup.isMinimized():
        # They minimized the start-up window to get on with something else — don't pull focus.
        window.showMinimized()
    else:
        window.show()
        window.raise_()
        window.activateWindow()
    active.window = window
    startup.close_for_real()
    on_startup_complete(window)     # reports a healthy start to the update script, cleans leftovers
    check_for_update(window)
    threading.Thread(target=_warm_up_pandas, name="pandas-warmup", daemon=True).start()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Zoom + Prism Command Center")

    # Must happen right after QApplication exists, but BEFORE building
    # MainWindow — a second launch attempt should exit here, having only
    # ever created a (cheap) QApplication, never a MainWindow or Engine
    # (which is what actually spins up a second Playwright browser).
    # See single_instance.py for why this was needed at all.
    instance_server = try_acquire_or_notify_existing()
    if instance_server is None:
        return  # another instance is already running and has been notified

    icon_path = resource_path("assets/icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    else:
        print(f"App icon not found at {icon_path} — using Qt's default. "
              f"Drop your icon at assets/icon.ico to fix this.")

    # The start-up window follows the saved theme, same as the main window.
    import theme
    from app_settings import get_settings
    saved = get_settings().value("theme/mode", None)
    theme.set_theme(saved if saved in ("light", "dark") else theme.detect_system_theme())

    from startup_window import StartupWindow
    startup = StartupWindow()
    startup.step("Starting up…", 5, 15)
    startup.show()
    active = _ActiveWindow(startup)
    wire_activation_to_window(instance_server, active)
    _pump(app)

    startup.step("Loading the interface…", 15, 45)
    preloader = threading.Thread(target=_preload_heavy_modules, name="preload", daemon=True)
    preloader.start()
    _pump(app, until=lambda: not preloader.is_alive())
    from main_window import MainWindow
    from hard_exit import hard_exit

    startup.step("Building the window…", 45, 65)
    _pump(app)
    window = MainWindow()

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.create_task(_finish_startup(startup, window, active))
        loop.run_forever()
        # run_forever() returns once the window's closeEvent has shut the engine
        # down and called quit(). End the process here instead of letting
        # `with loop:` close the event loop: closing it waits on outstanding
        # I/O (the Playwright driver's pipes) for as long as it takes, which
        # is what the 8 s safety-net thread in MainWindow.closeEvent was
        # killing the process in the middle of — crashing pyside6.abi3.dll
        # on every auto-update. See hard_exit.py.
        hard_exit(0, sync_settings=True)


if __name__ == "__main__":
    main()
