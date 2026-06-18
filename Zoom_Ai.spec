# ─────────────────────────────────────────────────────────────────────────────
# Zoom_Ai.spec  —  PyInstaller build spec
# Bundles Chromium so the exe is fully self-contained (no Playwright install needed)
#
# HOW TO BUILD:
#   1. Open Command Prompt in your project folder
#   2. Run:  pyinstaller Zoom_Ai.spec
#   3. Your exe will be at:  dist\Zoom_Ai_latest.exe
# ─────────────────────────────────────────────────────────────────────────────

import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# ── Paths (based on your machine) ────────────────────────────────────────────
CHROMIUM_DIR = os.path.join(
    os.environ["USERPROFILE"],
    "AppData", "Local", "ms-playwright", "chromium-1208"
)

# ── Collect Playwright's own package data ────────────────────────────────────
playwright_datas = collect_data_files("playwright", includes=["**/*"])

# ── Bundle Chromium into the exact path Playwright expects inside the exe ────
# Playwright frozen looks for browsers at:
#   sys._MEIPASS/playwright/driver/package/.local-browsers/chromium-1208/
chromium_datas = []
DEST_BASE = os.path.join("playwright", "driver", "package", ".local-browsers", "chromium-1208")
for root, dirs, files in os.walk(CHROMIUM_DIR):
    for file in files:
        src_path = os.path.join(root, file)
        rel = os.path.relpath(root, CHROMIUM_DIR)
        if rel == ".":
            dest = DEST_BASE
        else:
            dest = os.path.join(DEST_BASE, rel)
        chromium_datas.append((src_path, dest))

print(f"[spec] Bundling {len(chromium_datas)} Chromium files from {CHROMIUM_DIR}")

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["Zoom_Ai.py"],
    pathex=[],
    binaries=collect_dynamic_libs("playwright"),
    datas=playwright_datas + chromium_datas,
    hiddenimports=[
        "playwright",
        "playwright.async_api",
        "playwright._impl._api_types",
        "playwright._impl._browser",
        "playwright._impl._browser_context",
        "playwright._impl._page",
        "playwright._impl._element_handle",
        "playwright._impl._network",
        "playwright._impl._driver",
        "pandas",
        "packaging",
        "packaging.version",
        "requests",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "asyncio",
        "threading",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["playwright_runtime_hook.py"],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Zoom_Ai_latest",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,       # Keep console ON until confirmed working, then set False
    disable_windowed_traceback=False,
    icon=None,
)
