# Zoom_Ai_latest.spec
# ---------------------
# PyInstaller spec for the PySide6 rebuild. Entry point is app.py, not
# Zoom_Ai_V54.py — that's the one thing that changes from your old build
# command. Everything else about your release process (bump version ->
# build -> GitHub Release -> update version.txt) stays the same.
#
# Build with:  pyinstaller Zoom_Ai_latest.spec --noconfirm
# (see the notes below before building a release)
#
# CHROMIUM IS NOT BUNDLED INTO THE EXE (as of 2.5.0)
# -----------------------------------------------------------------------
# Chromium used to be bundled into the exe, which made it ~435 MB and made
# every auto-update re-download all of it — 64% of the file, for something
# that almost never changes — and made every launch unpack ~1 GB into
# %TEMP%. It now lives in one fixed folder per Chromium revision:
#     %LOCALAPPDATA%\Command Center\chromium\chromium-<rev>\chrome-win64\
# put there by the installer (installer.iss), by earlier app versions
# (2.2.3+), or downloaded once by the app itself (browser_setup.py) from a
# GitHub Release asset. The app always launches it through Playwright's
# explicit `executable_path=`, which is what makes this work: an earlier
# external-browser attempt failed because Playwright resolved the browser
# path through an environment variable inside the exe's temp folder.
#
# So this spec still bundles the Playwright DRIVER (node.exe + package, incl.
# browsers.json, which says which Chromium revision is needed) but skips
# `.local-browsers/` entirely. That folder also held chromium_headless_shell
# (259 MB), ffmpeg and winldd, none of which this app uses (it launches
# headed Chromium only).
#
# Before building a RELEASE, run `python tools/prepare_release_assets.py`
# (once per Chromium revision / Playwright upgrade): it builds the zip the
# app downloads as a fallback, and writes installer_paths.iss for the
# installer. Build with:
#     pyinstaller Zoom_Ai_latest.spec --noconfirm

import os
import sys
import playwright
from pathlib import Path

block_cipher = None

def _driver_datas():
    """The Playwright driver (node.exe + package), WITHOUT .local-browsers/ —
    see the header comment."""
    driver_dir = Path(playwright.__file__).parent / "driver"
    datas = []
    for f in driver_dir.rglob("*"):
        if not f.is_file():
            continue
        if ".local-browsers" in f.relative_to(driver_dir).parts:
            continue
        rel_dir = f.parent.relative_to(driver_dir)
        dest = os.path.join("playwright", "driver", str(rel_dir))
        datas.append((str(f), dest))
    return datas

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=_driver_datas() + [
        ('assets/icon.ico', 'assets'),
    ],
    hiddenimports=[
        'qasync',
        'playwright.async_api',
        'playwright._impl._driver',
        'PySide6.QtSvg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # This app uses PySide6 exclusively. The build machine's Python
    # environment is shared across other, unrelated projects — if any
    # of them ever pull in PyQt5, PyInstaller's analysis aborts outright
    # ("multiple Qt bindings packages") since the two can't coexist in
    # one frozen app. Excluding PyQt5 here keeps this build unaffected
    # by whatever else happens to be installed alongside it.
    excludes=['PyQt5'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Playwright ships its own PyInstaller hook (playwright/_impl/__pyinstaller)
# that collects EVERYTHING under the playwright package as data — including
# driver/package/.local-browsers (Chromium, the headless shell, ffmpeg: ~280 MB
# compressed). Filtering only our own datas above isn't enough, so drop those
# entries from the final list. See the header comment.
# (The big .exe/.dll files land in a.binaries rather than a.datas — PyInstaller
# reclassifies executables by type — so both lists need filtering. Filtering
# only a.datas removed just the small files and left ~230 MB of Chromium in.)
a.datas = [d for d in a.datas if ".local-browsers" not in d[0].replace("\\", "/")]
a.binaries = [b for b in a.binaries if ".local-browsers" not in b[0].replace("\\", "/")]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Native splash screen ─────────────────────────────────────────────
# This is deliberately NOT the same thing as splash.py's Qt-based
# splash. That one only helps with the QApplication + MainWindow
# construction phase — it can't appear any earlier than that, because
# Python and PySide6 have to already be fully loaded to run it. The
# actual dominant wait (onefile extracting the whole bundle) happens BEFORE that, in the bootloader itself,
# before Python starts at all.
#
# PyInstaller's own Splash object runs at that earlier stage: the
# bootloader extracts a small Tcl/Tk splash runtime FIRST (fast — it's
# not the ~300MB main bundle), shows this image immediately, and only
# THEN proceeds to extract the rest. app.py calls pyi_splash.close()
# once MainWindow is actually shown. See
# https://pyinstaller.org/en/stable/spec-files.html#example-splash-file
splash = Splash(
    'assets/splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(44, 205),
    text_size=11,
    text_color='#8A8F9C',
    text_default='Starting up…',
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    splash,
    splash.binaries,
    [],
    name='Zoom_Ai_latest',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
