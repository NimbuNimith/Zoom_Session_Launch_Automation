# Zoom_Ai_latest.spec
# ---------------------
# PyInstaller spec for the PySide6 rebuild. Entry point is app.py, not
# Zoom_Ai_V54.py — that's the one thing that changes from your old build
# command. Everything else about your release process (bump version ->
# build -> GitHub Release -> update version.txt) stays the same.
#
# Build with:
#     pyinstaller Zoom_Ai_latest.spec --noconfirm --clean
# (--clean matters here more than usual — see the REQUIRED BUILD STEP
# below; a stale build/ cache can otherwise hide whether Chromium
# actually got bundled on a given build.)
#
# CHROMIUM IS NOW BUNDLED INTO THE EXE — reversing an earlier decision.
# -----------------------------------------------------------------------
# An earlier version of this file deliberately did NOT bundle Chromium,
# instead installing it once via the installer into an external cache
# (or later, a persistent %LOCALAPPDATA% folder), specifically to keep
# every future auto-update small (see updater.py — Zoom_Ai_latest.exe is
# what gets re-downloaded on EVERY update, not just first install).
#
# That approach was reverted after repeated real-world failures: every
# variant kept resolving to
#     ...\AppData\Local\Temp\_MEIxxxxxx\playwright\driver\package\
#     .local-browsers\chromium-1208\chrome-win64\chrome.exe
# — Playwright's OWN frozen-app code (playwright/_impl/_transport.py)
# does `if getattr(sys, "frozen", False): env.setdefault(
# "PLAYWRIGHT_BROWSERS_PATH", "0")`, which is a "hermetic install"
# relative to wherever the driver package lands — i.e. inside
# sys._MEIPASS, this onefile build's ephemeral per-launch temp dir.
# Overriding that env var explicitly should work (setdefault only fills
# in a missing value) and did work in isolated testing, but confirming
# it was genuinely live in a rebuilt, redeployed exe on the real target
# machine turned into its own persistent problem. The official Playwright
# docs (https://playwright.dev/python/docs/library#pyinstaller) only
# document ONE supported pattern for PyInstaller anyway: bundle the
# browser in. So: this now does that, matching Playwright's own default
# frozen-mode behavior instead of fighting it — no env var to lose,
# override, or fail to redeploy, ever again.
#
# REQUIRED BUILD STEP — do this once (or whenever the playwright pip
# version changes) on THIS BUILD MACHINE, in the same environment you
# run `pyinstaller` from, BEFORE building:
#
#     set PLAYWRIGHT_BROWSERS_PATH=0
#     playwright install chromium
#
# That downloads Chromium into this environment's own
# site-packages\playwright\driver\package\.local-browsers\ — which is
# exactly the folder _driver_datas_including_browsers() below bundles.
# Skip this and the build now FAILS LOUDLY (see that function) rather
# than silently shipping an exe with an empty .local-browsers folder,
# which would just be this same bug wearing a new disguise.
#
# Trade-off, worth remembering: the exe is now ~250-300MB bigger, and
# every future auto-update re-downloads that full size rather than a
# small delta. Reliability wins here given how much trouble the
# external-cache alternative caused in practice.

import os
import sys
import playwright
from pathlib import Path

block_cipher = None

def _driver_datas_including_browsers():
    """Bundles the ENTIRE playwright driver folder, including
    .local-browsers/ — the actual Chromium binaries this time. See the
    REQUIRED BUILD STEP in this file's header comment; this function
    enforces it at build time instead of failing silently."""
    driver_dir = Path(playwright.__file__).parent / "driver"
    local_browsers = driver_dir / "package" / ".local-browsers"
    if not local_browsers.is_dir() or not any(local_browsers.iterdir()):
        sys.exit(
            "\n\nBUILD ABORTED: Chromium isn't installed in THIS environment's "
            f"Playwright package ({local_browsers} is missing or empty).\n"
            "Run this once, in the same environment you're building with, "
            "then re-run this build:\n\n"
            "    set PLAYWRIGHT_BROWSERS_PATH=0\n"
            "    playwright install chromium\n"
        )
    datas = []
    for f in driver_dir.rglob("*"):
        if not f.is_file():
            continue
        rel_dir = f.parent.relative_to(driver_dir)
        dest = os.path.join("playwright", "driver", str(rel_dir))
        datas.append((str(f), dest))
    return datas

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=_driver_datas_including_browsers() + [
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Native splash screen ─────────────────────────────────────────────
# This is deliberately NOT the same thing as splash.py's Qt-based
# splash. That one only helps with the QApplication + MainWindow
# construction phase — it can't appear any earlier than that, because
# Python and PySide6 have to already be fully loaded to run it. The
# actual dominant wait (onefile extracting the whole ~300MB bundle,
# Chromium included) happens BEFORE that, in the bootloader itself,
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
