"""
updater.py
-----------
Ported from Zoom_Ai_V54.py's check_for_update()/_do_update() — same
version-check-against-GitHub-raw, same download-and-replace-via-.bat
mechanism. Only two things actually changed:

  1. tkinter.messagebox -> the app's own confirm()/notify() dialogs, so
     the update prompt looks like the rest of the app instead of a native
     popup (same reason those two functions exist at all — see
     main_window.py's _StyledDialog docstring).
  2. root.after(1000, _prompt) -> QTimer.singleShot(1000, _prompt) —
     same "wait a second so the prompt doesn't appear before the window
     does" behavior, Qt's timer API instead of Tkinter's.

CURRENT_VERSION is bumped to 2.0.0 here since this is the PySide6 rewrite
— a real break from the 1.x Tkinter line, worth reflecting in the version
number. Update version.txt in the repo to match whenever you cut this
release (same release workflow as before: bump here -> build -> GitHub
Release -> update version.txt).
"""

import os
import sys
import subprocess

import requests
from PySide6.QtCore import QTimer

CURRENT_VERSION = "2.3.0"
VERSION_URL  = "https://raw.githubusercontent.com/NimbuNimith/Zoom_Session_Launch_Automation/main/version.txt"
DOWNLOAD_URL = "https://github.com/NimbuNimith/Zoom_Session_Launch_Automation/releases/latest/download/Zoom_Ai_latest.exe"


def _do_update(parent, new_version: str):
    from main_window import confirm, notify  # local import avoids a circular import at module load

    current_exe = sys.executable if getattr(sys, "frozen", False) else None
    if not current_exe:
        notify(parent, "Update Available",
               f"Version {new_version} is available.\n\n"
               "Download the latest release from GitHub and replace this file.\n"
               "(Running from source right now, not a packaged .exe, so this "
               "can't self-replace — that only works in the built app.)")
        return

    new_exe_path = current_exe + ".new"
    try:
        notify(parent, "Downloading Update",
               f"Downloading v{new_version}...\nThe app will restart automatically when done.")
        r = requests.get(DOWNLOAD_URL, stream=True, timeout=60)
        r.raise_for_status()
        with open(new_exe_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        notify(parent, "Update Failed", f"Could not download update:\n{e}")
        return

    bat_path = current_exe + "_updater.bat"
    bat = ("@echo off\n" "timeout /t 2 /nobreak > nul\n"
           f'move /y "{new_exe_path}" "{current_exe}"\n'
           f'start "" "{current_exe}"\n' 'del "%~f0"\n')
    with open(bat_path, "w") as f:
        f.write(bat)
    subprocess.Popen(bat_path, shell=True)
    # NOT sys.exit(0) — that exits the interpreter immediately and skips
    # MainWindow.closeEvent() entirely, which is where the "sessions are
    # LIVE, are you sure?" confirmation lives, along with the only code
    # that actually calls engine.shutdown() to close the Playwright
    # browser cleanly. A bare sys.exit() here orphans the browser
    # process on update exactly the way the original zombie-process bug
    # did before closeEvent()/the 8s safety-net thread were added —
    # this reuses that same, already-tested shutdown path instead of
    # bypassing it.
    parent.close()


def check_for_update(parent):
    """Call once, shortly after the main window is shown (see app.py).
    Silent no-op on any failure — a broken update check should never be
    the reason the app won't open."""
    try:
        from packaging import version as pkg_version
        resp = requests.get(VERSION_URL, timeout=5)
        resp.raise_for_status()
        latest = resp.text.strip()
        if pkg_version.parse(latest) > pkg_version.parse(CURRENT_VERSION):
            def _prompt():
                from main_window import confirm
                if confirm(parent, "Update Available",
                           f"A new version is available: v{latest}\n"
                           f"You have: v{CURRENT_VERSION}\n\nDownload and restart now?",
                           danger=False):
                    _do_update(parent, latest)
            QTimer.singleShot(1000, _prompt)
    except Exception:
        pass
