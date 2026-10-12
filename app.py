r"""
app.py
-------
Entry point. Bootstraps Qt + qasync — asyncio runs AS Qt's event loop, on
the same thread. This is the concrete payoff of picking PySide6 + qasync
over plain Tkinter here: engine.py's coroutines call
`self.model.set_fields(...)` / `self.log(...)` directly, no
`root.after(0, lambda: ...)` marshaling required, because there's no
second thread to cross.

CHROMIUM IS NOW BUNDLED INTO THE EXE — reversing an earlier decision.
-----------------------------------------------------------------------
There used to be a `--playwright-install` code path here, plus a forced
PLAYWRIGHT_BROWSERS_PATH override to a persistent %LOCALAPPDATA% folder,
built to make Chromium live in a real external cache instead of inside
the exe (kept auto-updates small). That approach kept failing in
practice: real failure logs consistently showed
    BrowserType.launch: Executable doesn't exist at
    ...\AppData\Local\Temp\_MEIxxxxxx\playwright\driver\package\
    .local-browsers\chromium-1208\chrome-win64\chrome.exe
— Playwright's frozen-app code in its own _impl/_transport.py does:
    if getattr(sys, "frozen", False):
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
which resolves to a "hermetic" install relative to wherever the driver
package landed — i.e. inside sys._MEIPASS, this onefile build's
ephemeral, freshly-created-every-launch extraction temp dir. An explicit
override of that env var should have worked (setdefault only fills in a
missing value), and did work in isolated testing — but confirming it
was ever actually live in a *rebuilt, redeployed* exe on the real
target machine turned out to be its own ongoing problem, and the
official Playwright docs for PyInstaller
(https://playwright.dev/python/docs/library#pyinstaller) only document
one supported pattern anyway: bundle the browser in via
`PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium` on the BUILD
machine, then let PyInstaller package `.local-browsers` as data. That's
what Zoom_Ai_latest.spec does now (see its docstring for the build-time
step this requires). With the browser genuinely bundled at that exact
hermetic path, Playwright's own default frozen-mode behavior above
finds it with zero help from this file — no env var to get lost,
overridden, or silently not-yet-deployed. Trade-off: the exe is now
~250-300MB bigger, and every future auto-update re-downloads that full
size (see updater.py) rather than a small delta. Reliability wins here
given how much trouble the alternative caused in practice.
"""

import sys
import os
import asyncio

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

from main_window import MainWindow
from updater import check_for_update
from paths import resource_path
from hard_exit import hard_exit
from prism_token import refresh_token
from single_instance import try_acquire_or_notify_existing, wire_activation_to_window
from splash import show_splash

try:
    import pyi_splash  # only importable inside a frozen build with Splash() configured in the .spec
except ImportError:
    pyi_splash = None  # running from source (`python app.py`) — no bootloader, no native splash


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
        if pyi_splash is not None:
            pyi_splash.close()  # don't leave the native splash stuck on screen for a duplicate launch
        return  # another instance is already running and has been notified

    # pyi_splash (PyInstaller's native splash — see Zoom_Ai_latest.spec)
    # is already showing by this point in a real frozen build; it was
    # displayed by the bootloader before this code even started running,
    # which is the whole reason it exists — a Qt-based splash can't
    # appear that early because Python/PySide6 have to already be loaded
    # to draw it. Only fall back to the Qt splash when pyi_splash isn't
    # available at all (running from source).
    qt_splash = None
    if pyi_splash is None:
        qt_splash = show_splash()
        app.processEvents()

    icon_path = resource_path("assets/icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    else:
        print(f"App icon not found at {icon_path} — using Qt's default. "
              f"Drop your icon at assets/icon.ico to fix this.")

    window = MainWindow()
    window.show()
    if pyi_splash is not None:
        pyi_splash.close()
    elif qt_splash is not None:
        qt_splash.finish(window)
    wire_activation_to_window(instance_server, window)
    check_for_update(window)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        # Fire-and-forget, like the engine start: loads the Prism token from
        # the Google Sheet and logs the outcome. It never raises, and falls
        # back to the built-in token if the Sheet can't be reached.
        loop.create_task(refresh_token(log_fn=window.log_event))
        loop.create_task(window.engine.start())
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
