# Zoom_Ai_latest.spec
# ---------------------
# PyInstaller spec. Entry point is app.py.
#
# FOLDER BUILD (onedir), since 2.6.0
# -----------------------------------------------------------------------
# The output is dist\Zoom_Ai_latest\ : a small Zoom_Ai_latest.exe plus an
# _internal\ folder with Python, Qt and the Playwright driver. The installer
# copies that folder to {app}; nothing is unpacked at launch any more (the old
# single-file exe unpacked ~2,000 files / 389 MB into %TEMP% on EVERY start,
# and antivirus scanned each one). Updates replace only the exe unless the
# contents of _internal changed (see updater.py / tools/make_release_assets.py).
#
# Build with the CLEAN environment (tools\make_build_env.bat creates .buildenv
# with only requirements.txt + PyInstaller) so nothing from other projects
# (pyarrow, Pillow, cryptography, ...) is swept in:
#     .buildenv\Scripts\pyinstaller Zoom_Ai_latest.spec --noconfirm --clean
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
    # Also excluded: packages this app never uses that a shared build environment
    # tends to contain (the clean .buildenv has none of them; this is belt and braces).
    excludes=['PyQt5', 'PyQt6', 'tkinter', '_tkinter', 'PIL', 'pyarrow', 'cryptography',
              'matplotlib', 'scipy', 'IPython', 'jupyter', 'notebook', 'streamlit'],
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


# ── Trim Qt ───────────────────────────────────────────────────────────
# Only widgets are used (no QtQuick / WebEngine / 3D / multimedia). Drop the
# translation files and the software-OpenGL fallback DLL, and any Qt module or
# plugin PyInstaller collected that the app cannot load.
_QT_DROP = (
    "pyside6/translations/",
    "opengl32sw.dll",
    "d3dcompiler_47.dll",
    "qt6quick", "qt6qml", "qt6pdf", "qt6webengine", "qt6designer", "qt63d",
    "qt6charts", "qt6datavisualization", "qt6multimedia", "qt6sql", "qt6test",
    "qt6bluetooth", "qt6positioning", "qt6sensors", "qt6serialport",
    "qt6virtualkeyboard", "qt6remoteobjects", "qt6scxml", "qt6statemachine",
    "plugins/qmltooling/", "plugins/designer/", "plugins/sqldrivers/",
    "plugins/multimedia/", "plugins/position/", "plugins/sensors/",
    "plugins/generic/", "plugins/platforminputcontexts/", "plugins/platformthemes/",
)


def _keep(entry):
    name = entry[0].replace("\\", "/").lower()
    if "pyside6" not in name:
        return True
    return not any(token in name for token in _QT_DROP)


a.datas = [d for d in a.datas if _keep(d)]
a.binaries = [b for b in a.binaries if _keep(b)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Zoom_Ai_latest',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX makes antivirus slower and more suspicious, and gains little here
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Zoom_Ai_latest',
)
