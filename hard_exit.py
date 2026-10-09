"""
hard_exit.py
-------------
How the app ends its own process when a clean exit isn't happening (or
isn't worth waiting for).

Why not os._exit(): it runs ExitProcess, which tells every loaded DLL
"the process is detaching" while other threads are still running. The
main thread is usually still inside qasync's loop.close() at that point
(it waits on outstanding I/O, which can take as long as the process lets
it), and pyside6.abi3.dll then access-violates during the detach. Windows
reports that as an app crash (event 1000, fault offset 0x17a24 in
pyside6.abi3.dll) — seen on every auto-update — and the PyInstaller
bootloader, finding its child died abnormally with helpers still holding
files, shows "Failed to remove temporary directory: ...\\_MEIxxxxxx".

TerminateProcess ends the process without running any DLL detach code, so
none of that happens. Before it, the Playwright driver (node) and any
Chromium still alive are stopped, because they hold files inside the
temporary folder the bootloader is about to delete. Everything else is left
alone on purpose — in particular the update script (a cmd.exe child),
which has to outlive this process to swap the new exe in.
"""

import os
import sys

_HELPER_PREFIXES = ("node", "chrome")   # Playwright's driver and the browsers it launched


def _stop_helpers(wait_seconds: float = 1.0):
    try:
        import psutil
        helpers = []
        for p in psutil.Process(os.getpid()).children(recursive=True):
            try:
                if (p.name() or "").lower().startswith(_HELPER_PREFIXES):
                    helpers.append(p)
            except Exception:
                pass
        for p in helpers:
            try:
                p.terminate()
            except Exception:
                pass
        if helpers:
            _, alive = psutil.wait_procs(helpers, timeout=wait_seconds)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
    except Exception:
        pass


def hard_exit(code: int = 0, sync_settings: bool = False):
    """End this process now. Pass sync_settings=True only from the main
    thread (QSettings isn't safe to touch from another one)."""
    if sync_settings:
        try:
            from app_settings import get_settings
            get_settings().sync()
        except Exception:
            pass
    _stop_helpers()
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
        except Exception:
            pass
    os._exit(code)   # only reached if TerminateProcess failed (or not on Windows)
