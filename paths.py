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
import shutil


def resource_path(relative_path: str) -> str:
    """Resolves a path that works both running from source and as a
    frozen PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def stable_chromium_dir() -> str:
    """%LOCALAPPDATA%\\Command Center\\chromium — where the browser is
    copied so it runs from the SAME path on every launch. The onefile exe
    unpacks itself into a fresh random %TEMP%\\_MEIxxxxxx folder each run,
    and Windows Firewall rules are keyed to the program's path, so a
    browser run from there looks like a brand-new program (and triggers
    the "allow public and private networks?" prompt) every single time."""
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    path = os.path.join(base, "Command Center", "chromium")
    os.makedirs(path, exist_ok=True)
    return path


def stable_chromium_exe_for(src_exe: str) -> str:
    """Where ensure_stable_chromium() puts (or will put) the copy of the
    bundled chrome executable `src_exe`. Pure path math — touches nothing.
    Layout mirrors the source: <revision>\\<chrome folder>\\chrome.exe, e.g.
    chromium-1208\\chrome-win64\\chrome.exe, so a new Chromium revision gets
    its own folder."""
    src_dir = os.path.dirname(src_exe)
    return os.path.join(stable_chromium_dir(),
                        os.path.basename(os.path.dirname(src_dir)),
                        os.path.basename(src_dir),
                        os.path.basename(src_exe))


def ensure_stable_chromium(src_exe: str):
    """Copy the bundled Chromium folder (the one holding `src_exe`) to its
    stable location once. Returns (exe_path, None) on success, or
    (None, "why it failed") if anything goes wrong — callers then fall back
    to the bundled copy, so this can never leave the app unable to launch
    a browser, and the reason reaches the Event Log instead of vanishing.

    Later launches find the copy already there and return immediately. The
    copy goes to "<revision>.tmp" first and is renamed into place only
    when complete, so a crash or power loss mid-copy can't leave a
    half-copied browser that looks finished. Folders of older Chromium
    revisions are removed afterwards. Blocking file work (~390 MB the
    first time) — call it via asyncio.to_thread."""
    try:
        dest_exe = stable_chromium_exe_for(src_exe)
        if os.path.isfile(dest_exe):
            return dest_exe, None

        src_dir = os.path.dirname(src_exe)
        chrome_folder = os.path.basename(src_dir)
        root = stable_chromium_dir()
        dest_rev = os.path.dirname(os.path.dirname(dest_exe))
        tmp_rev = dest_rev + ".tmp"

        shutil.rmtree(tmp_rev, ignore_errors=True)          # leftover from an interrupted copy
        shutil.copytree(src_dir, os.path.join(tmp_rev, chrome_folder))
        shutil.rmtree(dest_rev, ignore_errors=True)          # exe was missing, so whatever's here is partial
        os.replace(tmp_rev, dest_rev)

        keep = os.path.basename(dest_rev)
        for name in os.listdir(root):
            if name != keep and name.startswith("chromium-"):
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
        if not os.path.isfile(dest_exe):
            return None, f"copy finished but {dest_exe} is missing"
        return dest_exe, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


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
