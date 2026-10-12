"""
paths.py
---------
One place for "where's that bundled file" logic. Needed because a
PyInstaller-frozen app doesn't run from its source folder — data files
listed in the .spec's `datas` get extracted to a temp dir (`sys._MEIPASS`)
at runtime instead. Every place that needs assets/icon.ico or similar
should go through resource_path() rather than a bare relative path, or it
works when you run `python app.py` and silently breaks in the built exe.
"""

import sys
import os


def resource_path(relative_path: str) -> str:
    """Resolves a path that works both running from source and as a
    frozen PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def stable_chromium_dir() -> str:
    """%LOCALAPPDATA%\\Command Center\\chromium — where the browser lives
    (installed there by the installer, or downloaded there once — see
    browser_setup.py) so it runs from the SAME path on every launch. The onefile exe
    unpacks itself into a fresh random %TEMP%\\_MEIxxxxxx folder each run,
    and Windows Firewall rules are keyed to the program's path, so a
    browser run from there looks like a brand-new program (and triggers
    the "allow public and private networks?" prompt) every single time."""
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    path = os.path.join(base, "Command Center", "chromium")
    os.makedirs(path, exist_ok=True)
    return path


def browsers_path() -> str:
    """NOT CURRENTLY USED — kept for reference only.

    This was app.py's external-cache fix for the "_MEIxxxxxx\\...\\
    .local-browsers\\..." bug (see git history / prior conversation).
    It was superseded by bundling Chromium directly into the exe (see
    Zoom_Ai_latest.spec), which lets Playwright's own frozen-mode
    default find the browser with no env var involved at all — simpler
    and, more importantly, actually verifiable, unlike this approach
    which kept being hard to confirm was really deployed. Left here in
    case the bundled approach ever needs to be reverted; there's no
    live call site for this function anymore."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        path = os.path.join(base, "Command Center", "browsers")
    else:
        path = os.path.expanduser("~/.command_center/browsers")
    os.makedirs(path, exist_ok=True)
    return path
