# playwright_runtime_hook.py
#
# Runs automatically before Zoom_Ai.py starts (PyInstaller injects it).
# Tells Playwright exactly where the bundled Chromium lives inside the
# unpacked exe — must match the destination path used in the .spec file.

import os
import sys

if getattr(sys, "frozen", False):
    bundle_dir = sys._MEIPASS

    # Must match the dest path in Zoom_Ai.spec:
    # playwright/driver/package/.local-browsers/
    browsers_path = os.path.join(
        bundle_dir, "playwright", "driver", "package", ".local-browsers"
    )

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
    print(f"[Runtime Hook] PLAYWRIGHT_BROWSERS_PATH = {browsers_path}")

    # Also override the Playwright driver path so it doesn't try to
    # download anything at runtime
    driver_path = os.path.join(bundle_dir, "playwright", "driver")
    os.environ["PLAYWRIGHT_DRIVER_PATH"] = driver_path
    print(f"[Runtime Hook] PLAYWRIGHT_DRIVER_PATH   = {driver_path}")
